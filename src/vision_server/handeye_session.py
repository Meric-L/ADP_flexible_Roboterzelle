"""Hand-eye calibration over OPC UA: `T_flange_cam` on the hand Pi.

Same pattern as the camera calibration (start / capture / finish / abort), only
with a partner outside: the robot moves, and for each capture the frontend reports
the **reported** flange pose (forward kinematics from the real joint angles). The
Pi measures the target in camera coordinates: a tag or a calibration chessboard
(`hand-eye-chessboard-automation.md`).

At the end, `tagloc.handeye.solve_hand_eye` (Park-Martin) solves over all pairs.
The tag's position does not need to be known for this, only that it is fixed. If
it is known (`tagInBase` from the module catalogue), it serves as a **cross-check**,
not as an input -- the calibration is not meant to be as accurate as the catalogue,
but as accurate as the measurement.

The result goes to `data/handeye/<frame>.json`; the old file is kept beside it as a
backup first. It becomes active immediately, without a restart.

No asyncua is used here: the camera, the file and the nodes are passed in as
functions (`HandEyePorts`). See
`doc/project_docs/work-plans/hand-eye-over-opcua.md`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from tagloc.calibration import CameraCalibration, load_calibration, save_calibration
from tagloc.boards import CHARUCO, CHESSBOARD, BoardSpec, chessboard_is_unambiguous
from tagloc.geometry import (
    Pose,
    average_poses,
    compose,
    pose_to_dict,
    rotation_distance_rad,
    translation_distance_m,
)
from tagloc.handeye import (
    AXIS_THRESHOLD_DEG,
    BOARD_SCALE_WARNING,
    MIN_SAMPLES,
    SESSION_FILE,
    STAGE_LEAVE_ONE_OUT,
    STAGE_REFINEMENT,
    BoardRefinement,
    HandEye,
    HandEyeSolution,
    RefinementError,
    SessionSample,
    load_hand_eye,
    refine_board_session,
    rotation_diversity,
    save_hand_eye,
    session_json,
    session_payload,
    solve_hand_eye_robust,
)
from tagloc.handeye_offline import POSE_GROUPS, calibrate_session
from tagloc.jsonio import finite_or_none, utc_stamp

from .errors import VisionErrorCode
from .layer2.plan import _load, _pose

_log = logging.getLogger(__name__)

START_SCHEMA = "wsc.vision.handeye.start/1"
SAMPLE_SCHEMA = "wsc.vision.handeye.sample/1"
STATUS_SCHEMA = "wsc.vision.handeye.status/1"

IDLE = "idle"
CAPTURING = "capturing"
SOLVING = "solving"
FINISHED = "finished"
FAILED = "failed"

#: Recommendation for the operator -- `MIN_SAMPLES` is only the computational minimum.
RECOMMENDED_SAMPLES = 8
#: `finish` requires rotation about this many axes (`rotation_diversity`); with
#: fewer, `T_flange_cam` is not determined, even if the solver still computes.
REQUIRED_AXES = 3
#: If the estimated board scale deviates from the nominal size by more than this
#: (0.3 %), a warning is issued: the square size or the intrinsics are wrong.
SCALE_WARNING = BOARD_SCALE_WARNING
#: Values of `intrinsicsSource` for which the intrinsics do not come from us.
FACTORY_INTRINSICS = "realsense_factory"
PLACEHOLDER_INTRINSICS = "placeholder"


@dataclass(frozen=True)
class HandEyePorts:
    """What the session needs from the server."""

    #: Measures `T_cam_tag`: `(Pose, "")` or `(None, reason)`, optionally with a
    #: third element `Info` (`reprojectionErrorPx`).
    measure_tag: Callable[[int], Awaitable[tuple]]
    #: Is a job, a camera calibration or a layer-2 run active?
    busy: Callable[[], bool]
    #: Target of the calibration file.
    path: Path
    #: Applies the new calibration to the running source.
    apply: Callable[[HandEye], None]
    #: Writes `HandEyeStatus`.
    publish: Callable[[str], Awaitable[None]]
    frame_id: str = "cam_flange"
    #: Measures `T_cam_board` of a chessboard; `None`: the server cannot do it.
    #: The third element (`Info`) may additionally contain `corners` (canonical
    #: corners `(N, 2)` in the distorted image), `calibration` (`CameraCalibration`),
    #: `intrinsicsSource` and `image` -- without them there is no refinement, only
    #: the Park-Martin solution.
    measure_board: Callable[[BoardSpec], Awaitable[tuple]] | None = None
    #: Where the camera intrinsics come from (`calibration`,
    #: `realsense_factory`, `placeholder`); `None`: unknown.
    intrinsics_source: Callable[[], str | None] | None = None
    #: Where the session datasets go; `None`: `sessions/` next to `path`.
    sessions_dir: Path | None = None
    capture_board_raw: Callable[[BoardSpec], Awaitable[tuple]] | None = None
    raw_sessions_dir: Path | None = None
    #: Full calibration (`fullCalibration`): target of the new camera intrinsics
    #: (`data/calibration/<frame>.json`) and their adoption into the running source,
    #: including the overlay. Without both, `finish` does not exist with
    #: `fullCalibration`.
    intrinsics_path: Path | None = None
    apply_intrinsics: Callable[[CameraCalibration], Awaitable[None]] | None = None


@dataclass
class _Sample:
    flange_in_base: Pose
    cam_to_tag: Pose | None
    detail: str
    #: Mean reprojection error of the images of this capture, even if the capture
    #: was discarded (e.g. because the error was too large). `None` if nothing was found.
    reprojection_error_px: float | None = None
    #: After `finish`: deviation of this capture's board position from the mean of
    #: the used captures, and whether it was discarded as an outlier. `None` before.
    residual_m: float | None = None
    residual_deg: float | None = None
    rejected: bool | None = None
    #: Board mode: canonical corners `(N, 2)` in the distorted image, averaged over
    #: the images used for this capture. `None` for tags.
    corners_px: np.ndarray | None = None
    #: One image of the capture for the dataset, not in the status.
    image: Any = None
    #: After `finish`: prediction error of the refinement without this capture
    #: (px; m in camera coordinates). `None` before that, for tags and for outliers.
    loo_error_px: float | None = None
    loo_error_m: float | None = None
    pose_id: str | None = None
    joints_rad: list[float] | None = None
    captured_at: str | None = None
    pose_group: str | None = None
    invalidated: bool = False

    @property
    def accepted(self) -> bool:
        return self.cam_to_tag is not None and not self.invalidated


@dataclass
class _State:
    state: str = IDLE
    message: str = ""
    tag_id: int = -1
    tag_in_base: Pose | None = None
    #: Set when a chessboard is measured instead of a tag.
    board: BoardSpec | None = None
    samples: list[_Sample] = field(default_factory=list)
    result: dict | None = None
    #: Intrinsics and their origin, as used by the board measurement.
    calibration: Any = None
    intrinsics_source: str | None = None
    #: Folder name of the dataset (UTC), set at start.
    stamp: str = ""
    capture_only: bool = False
    #: Raw capture like `capture_only`, but at the end the intrinsics and hand-eye are computed too.
    full_calibration: bool = False
    session_path: str | None = None

    @property
    def raw(self) -> bool:
        """Capture raw images (`captureOnly` or `fullCalibration`)."""
        return self.capture_only or self.full_calibration


def _intrinsics_warnings(source: str | None) -> list[str]:
    """Return hints about the origin of the intrinsics."""
    if source == FACTORY_INTRINSICS:
        return [
            "Intrinsik: Werkswerte der Kamera, keine eigene Kalibrierung -- "
            "Massstab und Verzeichnung koennen abweichen; Kamera neu kalibrieren."
        ]
    if source == PLACEHOLDER_INTRINSICS:
        return ["Intrinsik: Platzhalter, die Posen sind nicht massshaltig."]
    return []


#: Log message for each failed step of `refine_board_session`.
_REFINEMENT_FAILURES = {
    STAGE_REFINEMENT: "Gemeinsame Verfeinerung fehlgeschlagen",
    STAGE_LEAVE_ONE_OUT: "Leave-one-out fehlgeschlagen",
}


def _refine_board(
    spec: BoardSpec, calibration, samples: list[_Sample], flanges: list[Pose], tags: list[Pose]
) -> BoardRefinement | None:
    """Run `tagloc.handeye.refine_board_session` over the accepted captures.

    Returns `None` if this is not possible (missing corners or intrinsics, or a
    computation error; the error is in the log). A `ValueError` (poses too similar)
    is passed on to the caller. Safe to run in a thread.
    """
    if calibration is None or not samples or any(sample.corners_px is None for sample in samples):
        return None
    try:
        return refine_board_session(
            flanges, [sample.corners_px for sample in samples], tags, spec, calibration
        )
    except RefinementError as error:
        _log.exception("%s -- Park-Martin bleibt", _REFINEMENT_FAILURES[error.stage])
        return None


def _write_sample_images(folder: Path, samples: list[_Sample]) -> dict[int, str]:
    """Write one PNG per capture that has an image; return `{index: filename}`. Errors go to the log only."""
    images: dict[int, str] = {}
    try:
        import cv2

        for index, sample in enumerate(samples):
            image = sample.image
            if image is None:
                continue
            name = f"sample_{index:03d}.png"
            if cv2.imwrite(str(folder / name), np.asarray(image)):
                images[index] = name
    except Exception:
        _log.exception("Bilder des Hand-Auge-Datensatzes nicht geschrieben")
    return images


def _write_session_dataset(
    folder: Path, state: _State, outcome: str, intrinsics_source: str | None, frame_id: str
) -> None:
    """Write `session.json` and one PNG per capture with an image into `folder`.

    One folder per session (time of the start); a second attempt of the same
    session overwrites it. The file is enough to recompute with
    `calibrate_handeye --session`: reported flange poses, measured board poses,
    corners, intrinsics and board geometry. Format and reader:
    `tagloc.handeye.session_payload` / `load_session`.
    """
    folder.mkdir(parents=True, exist_ok=True)
    images = _write_sample_images(folder, state.samples)
    payload = session_payload(
        outcome=outcome,
        frame_id=frame_id,
        tag_id=state.tag_id,
        tag_in_base=state.tag_in_base,
        board=state.board,
        intrinsics_source=intrinsics_source,
        calibration=state.calibration,
        samples=[
            SessionSample(
                accepted=sample.accepted,
                detail=sample.detail,
                flange_in_base=sample.flange_in_base,
                cam_to_target=sample.cam_to_tag,
                reprojection_error_px=sample.reprojection_error_px,
                corners_px=sample.corners_px,
                image=images.get(index),
            )
            for index, sample in enumerate(state.samples)
        ],
        result=state.result,
    )
    (folder / SESSION_FILE).write_text(session_json(payload), encoding="utf-8")


#: Session folders are named by the UTC stamp of their start (`utc_stamp`, e.g.
#: `20261117T150000Z`, raw ones with a suffix); only these are ever deleted.
_SESSION_FOLDER = re.compile(r"^\d{8}T\d{6}Z")


def _remove_session_folders(root: Path | None) -> int:
    """Delete the session folders under `root` (only those named by a UTC stamp).

    Like the camera calibration of Layer 1, only the images of the current
    calibration are kept. Returns how many folders were deleted.
    """
    if root is None or not root.is_dir():
        return 0
    removed = 0
    for child in root.iterdir():
        if child.is_dir() and _SESSION_FOLDER.match(child.name):
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


def _write_raw_capture(folder: Path, state: _State, sample: _Sample,
                       calibration, frame_id: str) -> None:
    """Store one image and its robot state under the same pose ID."""
    from tagloc.handeye import intrinsics_to_dict

    folder.mkdir(parents=True, exist_ok=True)
    (folder / "results").mkdir(exist_ok=True)
    config_path = folder / "config.json"
    existing = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else None
    if existing is None or (existing.get("intrinsics") is None and calibration is not None):
        config = {
            "schema": "wsc.vision.handeye.raw/1", "frameId": frame_id,
            "imageStream": "configured", "units": "m", "rotation": "quaternion_xyzw",
            "frames": {"T_base_flange": "flange in base", "T_camera_board": "board in camera",
                       "T_flange_camera": "camera in flange"},
            "board": state.board.as_dict(),
            "intrinsics": intrinsics_to_dict(calibration) if calibration is not None else None,
            "intrinsicsSource": None if state.full_calibration else state.intrinsics_source,
            "fullCalibration": state.full_calibration,
        }
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    pose_folder = folder / "poses" / sample.pose_id
    pose_folder.mkdir(parents=True, exist_ok=False)
    if sample.image is not None:
        import cv2
        if not cv2.imwrite(str(pose_folder / "camera.png"), np.asarray(sample.image)):
            raise OSError(f"Bild nicht geschrieben: {pose_folder}")
    state_json = {
        "schema": "wsc.vision.handeye.robot-state/1", "poseId": sample.pose_id,
        "T_base_flange": np.asarray(sample.flange_in_base).tolist(),
        "jointAnglesRad": sample.joints_rad, "capturedAt": sample.captured_at,
        "poseGroup": sample.pose_group,
        "accepted": sample.accepted, "status": sample.detail,
        "reprojectionErrorPx": sample.reprojection_error_px,
    }
    (pose_folder / "robot_state.json").write_text(
        json.dumps(state_json, indent=2) + "\n", encoding="utf-8"
    )


def _invalidate_raw_capture(folder: Path, pose_id: str, reason: str) -> None:
    """Store the quality decision separately from the unchanged raw state."""
    path = folder / "poses" / pose_id / "quality.json"
    path.write_text(json.dumps({"poseId": pose_id, "valid": False, "reason": reason},
                               indent=2) + "\n", encoding="utf-8")


def _restore(path: Path, backup: Path | None) -> None:
    """Restore a file from its backup; without a backup the file did not exist before."""
    if backup is not None:
        shutil.copy2(backup, path)
    else:
        path.unlink(missing_ok=True)


def _save_pair(intrinsics_path: Path, calibration: CameraCalibration,
               hand_eye_path: Path, hand_eye: HandEye):
    """Write intrinsics and hand-eye with backups, then load them back -- always both together.

    If anything fails, both files are restored from their backups. Returns
    `((backup of intrinsics | None, backup of hand-eye | None),
    loaded calibration, loaded hand-eye)`.
    """
    intrinsics_backup = None
    if intrinsics_path.is_file():
        intrinsics_backup = intrinsics_path.with_name(f"{intrinsics_path.name}.{utc_stamp()}.bak")
        shutil.copy2(intrinsics_path, intrinsics_backup)
    hand_eye_backup = None
    try:
        save_calibration(intrinsics_path, calibration)
        hand_eye_backup = save_with_backup(hand_eye_path, hand_eye)
        loaded_calibration = load_calibration(intrinsics_path)
        loaded_hand_eye = load_hand_eye(hand_eye_path)
    except Exception:
        _restore(intrinsics_path, intrinsics_backup)
        if hand_eye_backup is not None:
            _restore(hand_eye_path, hand_eye_backup)
        raise
    return ((str(intrinsics_backup) if intrinsics_backup else None,
             str(hand_eye_backup) if hand_eye_backup else None),
            loaded_calibration, loaded_hand_eye)


def save_with_backup(path: Path, hand_eye: HandEye) -> Path | None:
    """Write the calibration; an existing file is copied first.

    Returns the path of the backup, or `None` if there was no old file.
    """
    backup = None
    if path.is_file():
        stamp = utc_stamp()
        backup = path.with_name(f"{path.name}.{stamp}.bak")
        shutil.copy2(path, backup)
    save_hand_eye(path, hand_eye)
    return backup


@dataclass(frozen=True)
class _Accepted:
    """The accepted captures of a session, in capture order."""

    #: Indices into `samples[]` -- the status counts all captures, including discarded ones.
    indices: list[int]
    samples: list[_Sample]

    @classmethod
    def of(cls, samples: list[_Sample]) -> _Accepted:
        # Indices instead of comparisons: `_Sample` holds numpy poses, so `==` and
        # `list.index` would fail on them.
        indices = [index for index, sample in enumerate(samples) if sample.accepted]
        return cls(indices, [samples[index] for index in indices])

    @property
    def flanges(self) -> list[Pose]:
        return [sample.flange_in_base for sample in self.samples]

    @property
    def tags(self) -> list[Pose]:
        return [sample.cam_to_tag for sample in self.samples]


def _axes_refusal(flanges: list[Pose]) -> str | None:
    """Return a message if the flange poses rotate about too few axes, else `None`.

    The solver also computes with two axes, but then the rotation is partly
    determined by noise. Better to keep capturing. Below `MIN_SAMPLES` poses the
    solver itself says what is missing.
    """
    _, axes = rotation_diversity(flanges)
    if len(flanges) >= MIN_SAMPLES and axes < REQUIRED_AXES:
        return (
            f"Nicht berechnet: nur {axes} Drehachse(n) erreicht, noetig sind "
            f"{REQUIRED_AXES}. Den Flansch um alle drei Achsen kippen und "
            "drehen (je mindestens "
            f"{AXIS_THRESHOLD_DEG:.0f} Grad), dann erneut berechnen."
        )
    return None


def _annotate_samples(
    accepted: _Accepted, used: _Accepted, solution: HandEyeSolution, refined: BoardRefinement | None
) -> None:
    """Write residuals, the discarded flag and prediction errors into the captures.

    Discarded captures do not go into the calibration, but their residual still
    appears in the status -- so that one can see which pose was bad.
    """
    rejected_positions = set(solution.rejected)
    for position, (sample, (residual_m, residual_deg)) in enumerate(
        zip(accepted.samples, solution.residuals)
    ):
        sample.residual_m, sample.residual_deg = residual_m, residual_deg
        sample.rejected = position in rejected_positions
    if refined is not None and refined.loo is not None:
        for sample, error_px, error_m in zip(used.samples, refined.loo.error_px, refined.loo.error_m):
            sample.loo_error_px, sample.loo_error_m = error_px, error_m


def _result_warnings(
    intrinsics_source: str | None,
    board: BoardSpec | None,
    refined: BoardRefinement | None,
    used: _Accepted,
) -> list[str]:
    """Return the warnings for the result: intrinsics origin, missing refinement, board scale."""
    warnings = _intrinsics_warnings(intrinsics_source)
    if board is not None and refined is None and used.samples:
        warnings.append(
            "Keine gemeinsame Verfeinerung gegen die Bildpunkte moeglich -- "
            "Ergebnis nur aus Park-Martin."
        )
    if refined is not None:
        scale = refined.refinement.board_scale
        if abs(scale - 1.0) > SCALE_WARNING:
            warnings.append(
                f"Brettmassstab weicht {abs(scale - 1.0) * 100:.1f} % vom Nennmass ab "
                "-- Brett nachmessen / Intrinsik pruefen."
            )
    return warnings


def _result_payload(
    *,
    solution: HandEyeSolution,
    refined: BoardRefinement | None,
    accepted: _Accepted,
    used: _Accepted,
    tag_in_base: Pose | None,
    warnings: list[str],
    path: Path,
    backup: Path | None,
) -> dict:
    """Build the `result` of the status: key figures, cross-check, refinement, warnings.

    `sessionPath` stays `None` -- the dataset is only written by `_persist`, which
    fills in the value afterwards.
    """
    pose_flange_cam = solution.pose_flange_cam
    # Where the board/tag lies in the base frame: the mean of F·X·C, or the refined
    # `T_base_brett`, position in m.
    reconstructed = None
    if refined is None or tag_in_base is not None:
        reconstructed = average_poses(
            [compose(flange, pose_flange_cam, tag) for flange, tag in zip(used.flanges, used.tags)]
        )
    result = {
        "poseFlangeCam": pose_to_dict(pose_flange_cam),
        # True RMS of the deviation of the board position over the used captures;
        # the maximum is listed separately next to it.
        "rmsPositionM": solution.rms_position_m,
        "rmsRotationDeg": solution.rms_rotation_deg,
        "maxPositionM": solution.max_position_m,
        "maxRotationDeg": solution.max_rotation_deg,
        "sampleCount": solution.used_count,
        "rejectedCount": len(solution.rejected),
        #: Indices into `samples[]`, in the order they were discarded.
        "rejectedSamples": [accepted.indices[i] for i in solution.rejected],
        "knownTagCheckM": None,
        "knownTagCheckDeg": None,
        "path": str(path),
        "backupPath": str(backup) if backup is not None else None,
        # Only with board and refinement, otherwise `null`. Reprojection of all
        # board corners after the joint refinement (px).
        "refinedReprojectionRmsPx": None,
        #: Board scale relative to the nominal size (1.0 = board as specified).
        "boardScale": None,
        #: Prediction error without the respective capture (RMS over all): the honest
        #: accuracy value, px and m (camera coordinates).
        "looErrorRmsPx": None,
        "looErrorRmsM": None,
        "refinementConverged": None,
        "targetInBase": pose_to_dict(
            refined.refinement.pose_base_board if refined is not None else reconstructed
        ),
        "warnings": warnings,
        "sessionPath": None,
    }
    if refined is not None:
        result["refinedReprojectionRmsPx"] = refined.refinement.reprojection_rms_px
        result["boardScale"] = refined.refinement.board_scale
        result["refinementConverged"] = refined.refinement.converged
        if refined.loo is not None:
            result["looErrorRmsPx"] = refined.loo.rms_px
            result["looErrorRmsM"] = refined.loo.rms_m
    if tag_in_base is not None:
        result["knownTagCheckM"] = translation_distance_m(reconstructed, tag_in_base)
        result["knownTagCheckDeg"] = math.degrees(rotation_distance_rad(reconstructed, tag_in_base))
    return result


def _summary_message(solution: HandEyeSolution, refined: BoardRefinement | None) -> str:
    """Return the status message after a successful `finish`."""
    dropped = f", {len(solution.rejected)} Ausreisser verworfen" if solution.rejected else ""
    message = (
        f"Gespeichert und aktiv: RMS {solution.rms_position_m * 1000:.1f} mm / "
        f"{solution.rms_rotation_deg:.2f} Grad (max {solution.max_position_m * 1000:.1f} mm) "
        f"ueber {solution.used_count} Posen{dropped}"
    )
    if refined is not None:
        message += f", Reprojektion {refined.refinement.reprojection_rms_px:.2f} px"
        if refined.loo is not None:
            message += f", Vorhersagefehler {refined.loo.rms_m * 1000:.1f} mm"
    return message


class HandEyeSession:
    def __init__(self, ports: HandEyePorts) -> None:
        self._ports = ports
        self._s = _State()
        #: A capture measures over several images; a second one running at the same
        #: time would attach its flange pose to the wrong image.
        self._measuring = False

    @property
    def active(self) -> bool:
        """Return whether the session blocks a layer-2 run, camera calibration and SetTagMap."""
        return self._s.state in (CAPTURING, SOLVING)

    # --- Inputs --------------------------------------------------------------

    async def start(self, options_json: str) -> VisionErrorCode:
        if self.active:
            _log.warning("StartHandEyeCalibration abgelehnt: Sitzung laeuft schon")
            return VisionErrorCode.BUSY
        if self._ports.busy():
            _log.warning("StartHandEyeCalibration abgelehnt: Job, Kalibrierung oder Lauf aktiv")
            return VisionErrorCode.BUSY
        try:
            tag_id, tag_in_base, board = parse_start(options_json)
            options = _load(options_json, START_SCHEMA)
            capture_only = options.get("captureOnly", False)
            full_calibration = options.get("fullCalibration", False)
            if not isinstance(capture_only, bool) or not isinstance(full_calibration, bool):
                raise ValueError("captureOnly und fullCalibration muessen Bool-Werte sein")
            if capture_only and full_calibration:
                raise ValueError("captureOnly und fullCalibration schliessen sich aus")
            if (capture_only or full_calibration) and board is None:
                raise ValueError("captureOnly und fullCalibration erfordern ein Kalibrierbrett")
            if capture_only or full_calibration:
                check_raw_board(board)
        except ValueError as error:
            _log.warning("StartHandEyeCalibration abgelehnt: %s", error)
            return VisionErrorCode.INVALID_ARGUMENT
        await self._clear_previous_sessions(raw=capture_only or full_calibration)
        self._s = _State(
            state=CAPTURING,
            tag_id=tag_id,
            tag_in_base=tag_in_base,
            board=board,
            stamp=utc_stamp(),
            capture_only=capture_only,
            full_calibration=full_calibration,
        )
        if capture_only or full_calibration:
            from uuid import uuid4
            self._s.session_path = str(self._raw_root() / f"{self._s.stamp}_{uuid4().hex[:8]}")
        self._s.message = self._hint()
        _log.info("Hand-Auge-Kalibrierung gestartet, Ziel %s", self._target_label())
        await self._publish()
        return VisionErrorCode.OK

    async def capture(self, sample_json: str) -> VisionErrorCode:
        """Measure the target for the reported flange pose. Replies only afterwards:
        `OK` = capture accepted, `DETECTION_FAILED` = discarded (reason in the
        status)."""
        s = self._s
        if s.state != CAPTURING:
            return VisionErrorCode.INVALID_STATE
        if self._measuring or self._ports.busy():
            return VisionErrorCode.BUSY
        if s.raw:
            try:
                command = _load(sample_json, SAMPLE_SCHEMA)
            except ValueError:
                return VisionErrorCode.INVALID_ARGUMENT
            if "invalidatePoseId" in command:
                pose_id = command["invalidatePoseId"]
                matching = next((sample for sample in s.samples if sample.pose_id == pose_id), None)
                if matching is None or not isinstance(command.get("reason"), str):
                    return VisionErrorCode.INVALID_ARGUMENT
                try:
                    _invalidate_raw_capture(Path(s.session_path), pose_id, command["reason"])
                except OSError:
                    _log.exception("Rohaufnahme %s konnte nicht markiert werden", pose_id)
                    return VisionErrorCode.INTERNAL
                matching.invalidated = True
                s.message = f"Aufnahme {pose_id} verworfen: {command['reason']}"
                await self._publish()
                return VisionErrorCode.OK
        try:
            flange_in_base = parse_sample(sample_json)
            metadata = _load(sample_json, SAMPLE_SCHEMA)
            if s.raw:
                pose_id = metadata.get("poseId")
                joints = metadata.get("jointAnglesRad")
                if not isinstance(pose_id, str) or not pose_id.startswith("pose_") or not pose_id[5:].isdigit():
                    raise ValueError("poseId muss pose_NNN sein")
                if any(x.pose_id == pose_id for x in s.samples):
                    raise ValueError(f"poseId doppelt: {pose_id}")
                if metadata.get("poseGroup") not in POSE_GROUPS:
                    raise ValueError(f"poseGroup muss {POSE_GROUPS[0]} bis {POSE_GROUPS[-1]} sein")
                if not isinstance(joints, list) or len(joints) != 7 or any(
                    not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x)
                    for x in joints
                ):
                    raise ValueError("jointAnglesRad muss sieben endliche Winkel enthalten")
        except ValueError as error:
            _log.warning("CaptureHandEyeSample abgelehnt: %s", error)
            return VisionErrorCode.INVALID_ARGUMENT
        self._measuring = True
        try:
            cam_to_tag, reason, info = await self._measure(s)
        except Exception as error:  # camera gone -- report, do not crash
            _log.exception("Hand-Auge-Aufnahme fehlgeschlagen")
            cam_to_tag, reason, info = None, f"Messung fehlgeschlagen: {error}", {}
        finally:
            self._measuring = False
        if self._s is not s or s.state != CAPTURING:
            return VisionErrorCode.CANCELLED  # aborted during the measurement
        detail = reason if cam_to_tag is None else f"{self._target_label()} gemessen"
        sample = _Sample(
            flange_in_base,
            cam_to_tag,
            detail,
            reprojection_error_px=finite_or_none(info.get("reprojectionErrorPx")),
            pose_id=metadata.get("poseId") if s.raw else None,
            joints_rad=metadata.get("jointAnglesRad") if s.raw else None,
            captured_at=info.get("capturedAt"),
            pose_group=metadata.get("poseGroup") if s.raw else None,
        )
        sample.image = info.get("image")
        if s.capture_only:
            calibration = info.get("calibration")
            if calibration is None:
                s.message = "Keine Kameraintrinsik fuer den konfigurierten Kamerastrom"
                await self._publish()
                return VisionErrorCode.INVALID_STATE
            if info.get("intrinsicsSource") != "calibration":
                s.message = "Aufnahme erfordert die zuvor kalibrierte Kameraintrinsik"
                await self._publish()
                return VisionErrorCode.INVALID_STATE
            if s.calibration is not None and (
                calibration.image_size != s.calibration.image_size or
                calibration.model != s.calibration.model or
                not np.allclose(calibration.camera_matrix, s.calibration.camera_matrix) or
                not np.allclose(calibration.distortion, s.calibration.distortion)
            ):
                s.message = "Kameraintrinsik hat sich waehrend der Aufnahme geaendert"
                await self._publish()
                return VisionErrorCode.INVALID_STATE
        if info.get("calibration") is not None:
            s.calibration = info["calibration"]
        if info.get("intrinsicsSource"):
            s.intrinsics_source = str(info["intrinsicsSource"])
        if cam_to_tag is not None and info.get("corners") is not None:
            sample.corners_px = np.asarray(info["corners"], dtype=np.float64).reshape(-1, 2)
        s.samples.append(sample)
        if s.raw:
            try:
                _write_raw_capture(Path(s.session_path), s, sample,
                                   None if s.full_calibration else s.calibration,
                                   self._ports.frame_id)
            except Exception:
                _log.exception("Rohaufnahme %s konnte nicht gespeichert werden", sample.pose_id)
                s.samples.pop()
                s.message = f"Rohaufnahme {sample.pose_id} nicht gespeichert; Fahrt anhalten"
                await self._publish()
                return VisionErrorCode.INTERNAL
        s.message = self._hint() if cam_to_tag is not None else f"Aufnahme verworfen: {reason}"
        await self._publish()
        if cam_to_tag is not None:
            return VisionErrorCode.OK
        return VisionErrorCode.DETECTION_FAILED

    async def finish(self) -> VisionErrorCode:
        """Compute, save and activate. Too few or too similar poses: `INVALID_STATE`;
        the session stays open for further captures."""
        s = self._s
        if s.state != CAPTURING or self._measuring:
            return VisionErrorCode.INVALID_STATE
        if s.capture_only:
            s.state = FINISHED
            s.message = f"{sum(x.accepted for x in s.samples)} Rohaufnahmen gespeichert: {s.session_path}"
            await self._publish()
            return VisionErrorCode.OK
        if s.full_calibration:
            return await self._finish_full(s)
        accepted = _Accepted.of(s.samples)
        refusal = _axes_refusal(accepted.flanges)
        if refusal is not None:
            s.message = refusal
            await self._persist("refused")
            await self._publish()
            return VisionErrorCode.INVALID_STATE
        s.state = SOLVING
        s.message = f"Berechne aus {len(accepted.samples)} Aufnahmen"
        await self._publish()
        try:
            refined, solution = await self._solve(accepted)
        except ValueError as error:
            s.state = CAPTURING
            s.message = f"Nicht berechnet: {error}"
            await self._persist("failed")
            await self._publish()
            return VisionErrorCode.INVALID_STATE

        used = _Accepted(
            [i for position, i in enumerate(accepted.indices) if position not in solution.rejected],
            [x for position, x in enumerate(accepted.samples) if position not in solution.rejected],
        )
        _annotate_samples(accepted, used, solution, refined)
        intrinsics_source = self._intrinsics_source()
        hand_eye = HandEye(
            pose_flange_cam=solution.pose_flange_cam,
            frame_id=self._ports.frame_id,
            rms_position_m=solution.rms_position_m,
            rms_rotation_deg=solution.rms_rotation_deg,
            sample_count=solution.used_count,
            max_position_m=solution.max_position_m,
            max_rotation_deg=solution.max_rotation_deg,
            intrinsics_source=intrinsics_source or "",
            **(refined.hand_eye_fields() if refined is not None else {}),
        )
        try:
            backup = await self._save(hand_eye)
        except Exception as error:
            _log.exception("Hand-Auge-Kalibrierung nicht gespeichert")
            s.state = FAILED
            s.message = f"Berechnet, aber nicht gespeichert: {error}"
            await self._persist("failed")
            await self._publish()
            return VisionErrorCode.INTERNAL

        s.result = _result_payload(
            solution=solution,
            refined=refined,
            accepted=accepted,
            used=used,
            tag_in_base=s.tag_in_base,
            warnings=_result_warnings(intrinsics_source, s.board, refined, used),
            path=self._ports.path,
            backup=backup,
        )
        s.state = FINISHED
        s.message = _summary_message(solution, refined)
        _log.info("Hand-Auge-Kalibrierung: %s", s.message)
        s.result["sessionPath"] = await self._persist("finished")
        await self._publish()
        return VisionErrorCode.OK

    async def _finish_full(self, s: _State) -> VisionErrorCode:
        """Handle `fullCalibration`: compute the intrinsics from the raw images first,
        then the hand-eye, write both files, and apply both live."""
        ports = self._ports
        if ports.intrinsics_path is None or ports.apply_intrinsics is None:
            s.message = "Nicht berechnet: Dieser Server kann die Intrinsik nicht speichern"
            await self._publish()
            return VisionErrorCode.INVALID_STATE
        s.state = SOLVING
        s.message = "Berechne Intrinsik und Hand-Auge aus den Rohbildern"
        await self._publish()
        try:
            if not (Path(s.session_path) / "config.json").is_file():
                raise ValueError("Keine Rohaufnahme gespeichert")
            result = await asyncio.to_thread(
                calibrate_session, Path(s.session_path), frame_id=ports.frame_id)
        except ValueError as error:
            s.state = CAPTURING
            s.message = f"Nicht berechnet: {error}"
            await self._publish()
            return VisionErrorCode.INVALID_STATE
        except Exception as error:
            _log.exception("Volle Kalibrierung fehlgeschlagen")
            s.state = FAILED
            s.message = f"Berechnung fehlgeschlagen: {error}"
            await self._publish()
            return VisionErrorCode.INTERNAL
        hand_eye = HandEye(
            pose_flange_cam=result.hand_eye_pose,
            frame_id=ports.frame_id,
            calibration_id=result.calibration.calibration_id,
            rms_position_m=result.board_spread_rms_mm / 1000,
            rms_rotation_deg=result.board_spread_rms_deg,
            sample_count=result.handeye_pose_count,
            refined_reprojection_rms_px=result.handeye_reprojection_rms_px,
            intrinsics_source="calibration",
        )
        try:
            backups, loaded_calibration, loaded_hand_eye = await asyncio.to_thread(
                _save_pair, ports.intrinsics_path, result.calibration, ports.path, hand_eye)
        except Exception as error:
            _log.exception("Volle Kalibrierung nicht gespeichert")
            s.state = FAILED
            s.message = f"Berechnet, aber nicht gespeichert: {error}"
            await self._publish()
            return VisionErrorCode.INTERNAL
        try:
            await ports.apply_intrinsics(loaded_calibration)
            ports.apply(loaded_hand_eye)
        except Exception as error:
            _log.exception("Volle Kalibrierung nicht uebernommen")
            s.state = FAILED
            s.message = f"Gespeichert, aber nicht live uebernommen: {error}"
            await self._publish()
            return VisionErrorCode.INTERNAL
        report = result.report
        s.result = {
            "kind": "intrinsicsAndHandEye",
            "sessionPath": s.session_path,
            "intrinsics": report["intrinsics"],
            "handEye": {key: value for key, value in report["handEye"].items()
                        if key != "T_flange_camera"},
            "excluded": result.excluded,
            "backups": {"intrinsics": backups[0], "handEye": backups[1]},
        }
        s.state = FINISHED
        s.message = (
            f"Intrinsik {result.intrinsic_rms_px:.2f} px aus {result.intrinsic_image_count} Bildern, "
            f"Hand-Auge {result.handeye_reprojection_rms_px:.1f} px aus "
            f"{result.handeye_pose_count} Posen -- aktiviert"
        )
        _log.info("Volle Kalibrierung: %s", s.message)
        await self._publish()
        return VisionErrorCode.OK

    async def _solve(self, accepted: _Accepted) -> tuple[BoardRefinement | None, HandEyeSolution]:
        """Compute the solution: with a board, the refinement; otherwise (or if it is not
        possible) Park-Martin with outlier checks. Raises `ValueError` if the poses are
        not sufficient. The refined `X` replaces Park-Martin (`BoardRefinement.solution`
        is computed for this purpose)."""
        s = self._s
        flanges, tags = accepted.flanges, accepted.tags
        refined = None
        if s.board is not None:
            refined = await asyncio.to_thread(
                _refine_board, s.board, s.calibration, accepted.samples, flanges, tags
            )
        if refined is not None:
            return refined, refined.solution
        return None, await asyncio.to_thread(solve_hand_eye_robust, flanges, tags)

    async def _save(self, hand_eye: HandEye) -> Path | None:
        """Write the file with a backup, load it back and hand it to the source.

        Returns the path of the backup (`None` without an old file).
        """
        backup = await asyncio.to_thread(save_with_backup, self._ports.path, hand_eye)
        loaded = await asyncio.to_thread(load_hand_eye, self._ports.path)
        self._ports.apply(loaded)
        return backup

    async def abort(self) -> VisionErrorCode:
        if not self.active:
            return VisionErrorCode.INVALID_STATE
        self._s.state = IDLE
        self._s.message = (
            f"abgebrochen, Rohaufnahmen bleiben unter {self._s.session_path}"
            if self._s.raw else "abgebrochen, nichts gespeichert"
        )
        _log.info("Hand-Auge-Kalibrierung abgebrochen")
        await self._publish()
        return VisionErrorCode.OK

    async def publish_idle(self) -> None:
        await self._publish()

    async def _measure(self, s: _State) -> tuple[Any, str, dict]:
        """Return `(Pose | None, reason, info)`. The measuring functions may also return
        only `(Pose, reason)`."""
        if s.board is None:
            result = await self._ports.measure_tag(s.tag_id)
        elif s.raw and self._ports.capture_board_raw is not None:
            result = await self._ports.capture_board_raw(s.board)
        elif self._ports.measure_board is None:
            return None, "Dieser Server kann kein Schachbrett messen", {}
        else:
            result = await self._ports.measure_board(s.board)
        return result[0], result[1], (result[2] if len(result) > 2 else {})

    def _intrinsics_source(self) -> str | None:
        """Return the origin of the intrinsics: first the server's source, otherwise what
        the measurements reported; `None` if unknown."""
        if self._ports.intrinsics_source is not None:
            try:
                source = self._ports.intrinsics_source()
            except Exception as error:
                _log.debug("Herkunft der Intrinsik nicht lesbar: %s", error)
                source = None
            if source:
                return str(source)
        return self._s.intrinsics_source

    def _raw_root(self) -> Path:
        """Folder of the raw-image sessions (capture-only and full calibration)."""
        return self._ports.raw_sessions_dir or (
            Path(__file__).resolve().parents[2] / "data" / "rohbilder" / "calibration"
        )

    def _sessions_root(self) -> Path:
        """Folder of the session datasets (`_persist`)."""
        return self._ports.sessions_dir or self._ports.path.parent / "sessions"

    async def _clear_previous_sessions(self, *, raw: bool) -> None:
        """Delete the images and datasets of earlier calibrations before a new one starts.

        The raw-image folder only when the new session writes raw images itself
        (`raw`). Errors are only logged: a folder that cannot be deleted must not
        stop the calibration.
        """
        roots = [self._sessions_root()] + ([self._raw_root()] if raw else [])
        for root in roots:
            try:
                removed = await asyncio.to_thread(_remove_session_folders, root)
            except OSError as error:
                _log.warning("Alte Hand-Auge-Sitzungen unter %s nicht geloescht: %s", root, error)
                continue
            if removed:
                _log.info("Hand-Auge-Kalibrierung: %d alte Sitzung(en) unter %s geloescht", removed, root)

    async def _persist(self, outcome: str) -> str | None:
        """Write the session dataset. Errors go to the log only (never a reason to let the
        calibration fail). Returns the folder, or `None`."""
        s = self._s
        folder = self._sessions_root() / s.stamp
        try:
            await asyncio.to_thread(
                _write_session_dataset, folder, s, outcome, self._intrinsics_source(),
                self._ports.frame_id,
            )
        except Exception:
            _log.exception("Hand-Auge-Datensatz nicht geschrieben: %s", folder)
            return None
        return str(folder)

    def _target_label(self) -> str:
        board = self._s.board
        if board is None:
            return f"Tag {self._s.tag_id}"
        return f"Schachbrett {board.cols}x{board.rows} Ecken"

    # --- Status --------------------------------------------------------------

    def _hint(self) -> str:
        flanges = [sample.flange_in_base for sample in self._s.samples if sample.accepted]
        count = len(flanges)
        _, axes = rotation_diversity(flanges)
        if count < MIN_SAMPLES:
            return (
                f"{count} Aufnahmen -- mindestens {MIN_SAMPLES}, besser "
                f"{RECOMMENDED_SAMPLES}, aus deutlich verschiedenen Winkeln"
            )
        if axes < 2:
            return (
                f"{count} Aufnahmen, aber gedreht um nur {axes} Achse(n) -- "
                "den Flansch auch um eine andere Achse neigen"
            )
        if count < RECOMMENDED_SAMPLES:
            return f"{count} Aufnahmen, Drehung um {axes} Achsen -- berechenbar, mehr wird genauer"
        return f"{count} Aufnahmen, Drehung um {axes} Achsen -- bereit zum Berechnen"

    async def _publish(self) -> None:
        try:
            await self._ports.publish(self.status_json())
        except Exception:
            _log.exception("HandEyeStatus konnte nicht geschrieben werden")

    def _current(self) -> dict | None:
        """Return the calibration currently in the file -- also after a restart, when no
        session has a `result`. This lets the frontend show the camera at the flange.
        `None` without a (readable) file."""
        try:
            hand_eye = load_hand_eye(self._ports.path)
        except Exception as error:
            _log.debug("Keine lesbare Hand-Auge-Datei %s: %s", self._ports.path, error)
            return None
        return {
            "poseFlangeCam": pose_to_dict(hand_eye.pose_flange_cam),
            "calibrationId": hand_eye.calibration_id,
            "rmsPositionM": finite_or_none(hand_eye.rms_position_m),
            "rmsRotationDeg": finite_or_none(hand_eye.rms_rotation_deg),
            "maxPositionM": finite_or_none(hand_eye.max_position_m),
            "maxRotationDeg": finite_or_none(hand_eye.max_rotation_deg),
            "sampleCount": hand_eye.sample_count,
            "refinedReprojectionRmsPx": finite_or_none(hand_eye.refined_reprojection_rms_px),
            "boardScale": finite_or_none(hand_eye.board_scale),
            "looErrorRmsPx": finite_or_none(hand_eye.loo_error_rms_px),
            "looErrorRmsM": finite_or_none(hand_eye.loo_error_rms_m),
            "intrinsicsSource": hand_eye.intrinsics_source or None,
        }

    def status_json(self) -> str:
        s = self._s
        flanges = [sample.flange_in_base for sample in s.samples if sample.accepted]
        spread_deg, axes = rotation_diversity(flanges)
        return json.dumps(
            {
                "schema": STATUS_SCHEMA,
                "state": s.state,
                "message": s.message,
                "tagId": s.tag_id,
                "tagInBase": pose_to_dict(s.tag_in_base) if s.tag_in_base is not None else None,
                "target": (
                    {
                        "type": CHESSBOARD,
                        "cols": s.board.cols,
                        "rows": s.board.rows,
                        "squareSizeM": s.board.square_size_m,
                    }
                    if s.board is not None
                    else {"type": "tag", "tagId": s.tag_id}
                ),
                #: Origin of the intrinsics: `calibration`, `realsense_factory`,
                #: `placeholder` or `null` (unknown).
                "intrinsicsSource": self._intrinsics_source(),
                "sessionPath": s.session_path,
                "fullCalibration": s.full_calibration,
                "acceptedCount": len(flanges),
                "rotationSpreadDeg": spread_deg,
                "rotationAxes": axes,
                "samples": [
                    {
                        "index": index,
                        "poseId": sample.pose_id,
                        "accepted": sample.accepted,
                        "detail": sample.detail,
                        "flangeInBase": pose_to_dict(sample.flange_in_base),
                        "camToTag": (
                            pose_to_dict(sample.cam_to_tag) if sample.cam_to_tag is not None else None
                        ),
                        # Mean over the images of this capture, also for discarded ones.
                        "reprojectionErrorPx": sample.reprojection_error_px,
                        # Only after `FinishHandEyeCalibration`, otherwise `null`.
                        "residualM": sample.residual_m,
                        "residualDeg": sample.residual_deg,
                        "rejected": sample.rejected,
                        # Prediction error without this capture (board mode, after
                        # `finish`, not for outliers), otherwise `null`.
                        "looErrorPx": sample.loo_error_px,
                        "looErrorM": sample.loo_error_m,
                    }
                    for index, sample in enumerate(s.samples)
                ],
                "result": s.result,
                "current": self._current(),
            },
            allow_nan=False,
            default=float,
        )


# --- JSON ---------------------------------------------------------------------


def parse_start(text: str) -> tuple[int, Pose | None, BoardSpec | None]:
    """Parse `wsc.vision.handeye.start/1` and return `(tagId, tagInBase, board)`.

    Exactly one of `tagId` and `board` must be given; with a board, `tagId` is -1.
    Raises ValueError with a plain-language message.
    """
    data = _load(text, START_SCHEMA)
    has_tag = data.get("tagId") is not None
    has_board = data.get("board") is not None
    if has_tag == has_board:
        raise ValueError("Genau eins von tagId und board angeben.")
    if has_board:
        return -1, None, _parse_board(data["board"])
    tag_id = data.get("tagId")
    if not isinstance(tag_id, int) or isinstance(tag_id, bool) or tag_id < 0:
        raise ValueError("tagId fehlt oder ist keine nicht-negative Ganzzahl.")
    raw = data.get("tagInBase")
    return tag_id, (_pose(raw, "tagInBase") if raw is not None else None), None


def _parse_board(raw: Any) -> BoardSpec:
    if not isinstance(raw, dict) or raw.get("type") not in (CHESSBOARD, CHARUCO):
        raise ValueError('board.type muss "chessboard" oder "charuco" sein.')
    cols, rows, size = raw.get("cols"), raw.get("rows"), raw.get("squareSizeM")
    for name, value in (("cols", cols), ("rows", rows)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 2:
            raise ValueError(f"board.{name}: Anzahl als Ganzzahl >= 2.")
    if not isinstance(size, (int, float)) or isinstance(size, bool) or not 0 < size < 1:
        raise ValueError("board.squareSizeM: Feldkante in Metern, zwischen 0 und 1.")
    if raw["type"] == CHARUCO:
        marker = raw.get("markerSizeM")
        dictionary = raw.get("dictionary")
        if not isinstance(marker, (int, float)) or isinstance(marker, bool) or not 0 < marker < size:
            raise ValueError("board.markerSizeM muss kleiner als squareSizeM und positiv sein.")
        if not isinstance(dictionary, str):
            raise ValueError("board.dictionary: Name des ArUco-Woerterbuchs erwartet.")
        _check_dictionary(dictionary, cols * rows // 2)
        return BoardSpec(type=CHARUCO, cols=cols, rows=rows, square_size_m=float(size),
                         marker_size_m=float(marker), dictionary=dictionary)
    spec = BoardSpec(type=CHESSBOARD, cols=cols, rows=rows, square_size_m=float(size))
    if not chessboard_is_unambiguous(spec):
        raise ValueError(
            f"board: {cols}x{rows} Innenecken -- cols + rows muss ungerade sein (z. B. 5x6), "
            "sonst ist das Brett um 180 Grad gedreht nicht vom Original zu unterscheiden."
        )
    return spec


#: Allowed ArUco dictionaries for ChArUco boards.
ALLOWED_DICTIONARIES = tuple(
    f"DICT_{size}_{count}" for size in ("4X4", "5X5", "6X6", "7X7") for count in (50, 100, 250, 1000)
)


def check_raw_board(board: BoardSpec) -> None:
    """Check that a board is plausible for raw capture and full calibration.

    The dimensions are the ones measured on the real board and are used unchanged.
    Raises ValueError with a plain-language message.
    """
    if board.cols < 3 or board.rows < 3:
        what = "Innenecken" if board.type == CHESSBOARD else "Felder"
        raise ValueError(f"board: mindestens 3x3 {what}")
    if not 0.005 <= board.square_size_m <= 0.1:
        raise ValueError("board.squareSizeM: Feldkante zwischen 5 und 100 mm")
    if board.type == CHARUCO:
        if not 0 < board.marker_size_m < board.square_size_m:
            raise ValueError("board.markerSizeM muss positiv und kleiner als squareSizeM sein")
        _check_dictionary(board.dictionary, board.cols * board.rows // 2)


def _check_dictionary(name: str, markers: int) -> None:
    if name not in ALLOWED_DICTIONARIES:
        raise ValueError(f"board.dictionary: {name!r} nicht erlaubt "
                         f"(erlaubt: {', '.join(ALLOWED_DICTIONARIES)})")
    capacity = int(re.search(r"_(\d+)$", name).group(1))
    if markers > capacity:
        raise ValueError(f"board.dictionary {name} hat nur {capacity} Marker, "
                         f"das Brett braucht {markers}")


def parse_sample(text: str) -> Pose:
    """Parse `wsc.vision.handeye.sample/1`; raises ValueError with a plain-language message."""
    return _pose(_load(text, SAMPLE_SCHEMA).get("flangeInBase"), "flangeInBase")

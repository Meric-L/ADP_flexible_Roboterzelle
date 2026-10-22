"""Hand-Auge: wo die Kamera am Roboter sitzt, und wie daraus ein Anker wird.

Die Kamera ist **starr** mit dem Roboter verbunden -- nur der Roboter als
Ganzes bewegt sich. `T_flansch_cam` ist deshalb eine Konstante der Hardware und
wird genau **einmal** kalibriert, nicht je Vorgang.

Damit schliesst sich die Luecke, die der rein optische Weg offen laesst: ein
Welttag muss nur **einmal zu Beginn** eines Lokalisierungsvorgangs gesehen
werden. Steht die Handkamera danach dicht vor einem Modul, liegt der naechste
Welttag laengst ausserhalb des Bildfelds -- die Kinematik traegt dann weiter.

```
Einmal je Vorgang (Ankern):
    T_world_base = T_world_cam · inv(T_flansch_cam) · inv(T_base_flansch)

Je Modul danach:
    T_world_cam  = T_world_base · T_base_flansch(jetzt) · T_flansch_cam
```

Ohne OpenCV, auch der Solver. `cv2.calibrateHandEye` waere die naheliegende
Wahl, ist in OpenCV 5.0 aber nicht mehr nach Python exportiert -- nur noch die
`CALIB_HAND_EYE_*`-Konstanten sind da. Das Repo traegt bewusst 4.x und 5.x
(doc/projektdoku/altlasten.md D3), und eine Versionsverzweigung an einer Stelle, die einmal
im Leben der Zelle laeuft, waere schlechter als dreissig Zeilen Mathematik.
Dieselbe Linie wie `geometry.from_rvec_tvec`, das Rodrigues in numpy rechnet
statt `cv2.Rodrigues` zu rufen.
"""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .geometry import (
    Pose,
    average_poses,
    compose,
    invert,
    pose_from_dict,
    pose_to_dict,
    rotation_distance_rad,
    rvec_from_rotation,
    translation_distance_m,
)

SCHEMA = "wsc.vision.handeye/1"

#: Hand-Auge braucht Bewegung um mehr als eine Achse, sonst ist die Rotation
#: nicht bestimmt. Drei Posen sind das rechnerische Minimum; die Kalibrierfahrt
#: sollte deutlich mehr aufnehmen.
MIN_SAMPLES = 3

#: Unterhalb dieses Eigenwerts gilt die Drehachsenverteilung als entartet --
#: der Roboter hat dann im Wesentlichen um nur eine Achse gedreht.
_DEGENERATE_EIGENVALUE = 1e-12


@dataclass(frozen=True)
class HandEye:
    """Der konstante Versatz Flansch -> Kameraoptik, mit Herkunft."""

    pose_flange_cam: Pose
    frame_id: str = ""
    calibration_id: str = ""
    #: Streuung des rekonstruierten Zielorts ueber alle Aufnahmen der
    #: Kalibrierfahrt. Steht der Tag fest, muss er aus jeder Roboterpose an
    #: derselben Stelle herauskommen -- tut er das nicht, stimmt die
    #: Kalibrierung nicht.
    rms_position_m: float = float("nan")
    rms_rotation_deg: float = float("nan")
    sample_count: int = 0
    #: Groesste Abweichung (nicht RMS) unter den verwendeten Aufnahmen. Ab
    #: 22.10.2026 getrennt vom RMS; aeltere Dateien haben sie nicht.
    max_position_m: float = float("nan")
    max_rotation_deg: float = float("nan")


@dataclass(frozen=True)
class RobotAnchor:
    """Wo die Roboterbasis im Welt-KS steht, samt Herkunft des Ankers."""

    pose_world_base: Pose
    #: Welttag, an dem geankert wurde.
    world_tag_id: int = -1
    #: Guete der Welttag-Lokalisierung im Moment des Ankerns. Ein Anker ist
    #: nie besser als die Messung, aus der er stammt.
    spread_m: float = 0.0
    spread_rad: float = 0.0


def anchor_world_base(
    pose_world_cam: Pose, pose_base_flange: Pose, pose_flange_cam: Pose
) -> Pose:
    """Return `T_world_base` from one world-tag sighting and the robot pose.

    `T_world_cam = T_world_base · T_base_flansch · T_flansch_cam`, nach
    `T_world_base` aufgeloest. Das ist der Ankerschritt: ab hier weiss der
    Roboter, wo seine eigene Basis im Welt-KS steht, und braucht den Welttag
    nicht mehr im Bild.
    """
    return compose(pose_world_cam, invert(pose_flange_cam), invert(pose_base_flange))


def camera_pose_from_robot(
    pose_world_base: Pose, pose_base_flange: Pose, pose_flange_cam: Pose
) -> Pose:
    """Return `T_world_cam` from the anchor and the current robot pose.

    Der Weg ohne Welttag im Bild. Die Genauigkeit haengt jetzt am Anker und an
    der Kinematik, nicht mehr am Sichtfeld.
    """
    return compose(pose_world_base, pose_base_flange, pose_flange_cam)


def target_spread(
    poses_base_flange: Sequence[Pose],
    poses_cam_tag: Sequence[Pose],
    pose_flange_cam: Pose,
) -> tuple[float, float]:
    """Return how far the reconstructed target location scatters, `(m, rad)`.

    Der Tag der Kalibrierfahrt steht fest. Also muss
    `T_base_tag = T_base_flansch(i) · T_flansch_cam · T_cam_tag(i)` fuer jedes
    `i` dieselbe Pose ergeben. Die Streuung darum ist das Guetemass -- und das
    einzige, das ohne zweite Messkette auskommt.
    """
    reconstructed = [
        compose(pose_base_flange, pose_flange_cam, pose_cam_tag)
        for pose_base_flange, pose_cam_tag in zip(poses_base_flange, poses_cam_tag)
    ]
    if len(reconstructed) < 2:
        return (0.0, 0.0)
    mean = average_poses(reconstructed)
    return (
        max(translation_distance_m(mean, pose) for pose in reconstructed),
        max(rotation_distance_rad(mean, pose) for pose in reconstructed),
    )


def _motion_pairs(
    poses_base_flange: Sequence[Pose], poses_cam_tag: Sequence[Pose]
) -> list[tuple[Pose, Pose]]:
    """Return the `(A, B)` motion pairs of the hand-eye equation `A·X = X·B`.

    Der Zielort steht fest, also gilt fuer je zwei Aufnahmen `i`, `j`:

        T_base_flansch(i) · X · T_cam_tag(i) = T_base_flansch(j) · X · T_cam_tag(j)

    umgestellt auf `A · X = X · B` mit

        A = inv(T_base_flansch(j)) · T_base_flansch(i)
        B = T_cam_tag(j) · inv(T_cam_tag(i))

    Der unbekannte Zielort kuerzt sich dabei heraus -- man muss also nicht
    wissen, wo der Tag liegt, nur dass er sich nicht bewegt.
    """
    pairs = []
    count = len(poses_base_flange)
    for first in range(count):
        for second in range(first + 1, count):
            motion_flange = compose(
                invert(poses_base_flange[second]), poses_base_flange[first]
            )
            motion_cam = compose(poses_cam_tag[second], invert(poses_cam_tag[first]))
            pairs.append((motion_flange, motion_cam))
    return pairs


def solve_hand_eye(
    poses_base_flange: Sequence[Pose], poses_cam_tag: Sequence[Pose]
) -> tuple[Pose, float, float]:
    """Solve `T_flansch_cam` from N robot poses looking at one fixed tag.

    Aufnahme: der Tag bleibt liegen, der Roboter faehrt N deutlich
    verschiedene Posen an und misst ihn jedes Mal. Reine Translation reicht
    nicht -- ohne Drehung um mehrere Achsen ist die Rotation nicht bestimmt.

    Verfahren nach Park und Martin: die Drehachsen der Bewegungspaare spannen
    `M = sum(beta · alpha^T)` auf, daraus folgt `R_X = (M^T M)^(-1/2) · M^T`;
    die Translation faellt anschliessend als lineares Ausgleichsproblem
    `(R_A - I) · t_X = R_X · t_B - t_A` an.

    Liefert `(T_flansch_cam, Streuung in m, Streuung in Grad)`.
    """
    if len(poses_base_flange) != len(poses_cam_tag):
        raise ValueError(
            f"Hand-Auge: {len(poses_base_flange)} Roboterposen zu "
            f"{len(poses_cam_tag)} Tag-Messungen"
        )
    if len(poses_base_flange) < MIN_SAMPLES:
        raise ValueError(
            f"Hand-Auge braucht mindestens {MIN_SAMPLES} Posen, "
            f"bekommen: {len(poses_base_flange)}. Der Roboter muss dabei um "
            "mehrere Achsen drehen, reines Verschieben genuegt nicht."
        )

    pairs = _motion_pairs(poses_base_flange, poses_cam_tag)

    accumulator = np.zeros((3, 3), dtype=np.float64)
    for motion_flange, motion_cam in pairs:
        alpha = rvec_from_rotation(motion_flange[:3, :3]).reshape(3, 1)
        beta = rvec_from_rotation(motion_cam[:3, :3]).reshape(3, 1)
        accumulator += beta @ alpha.T

    eigenvalues, eigenvectors = np.linalg.eigh(accumulator.T @ accumulator)
    if float(np.min(eigenvalues)) < _DEGENERATE_EIGENVALUE:
        raise ValueError(
            "Hand-Auge: die Roboterposen drehen im Wesentlichen um nur eine "
            "Achse. Die Kalibrierfahrt braucht Drehungen um mehrere Achsen, "
            "sonst ist die Rotation nicht bestimmt."
        )
    inverse_sqrt = eigenvectors @ np.diag(1.0 / np.sqrt(eigenvalues)) @ eigenvectors.T
    rotation = inverse_sqrt @ accumulator.T

    left = np.zeros((3 * len(pairs), 3), dtype=np.float64)
    right = np.zeros((3 * len(pairs),), dtype=np.float64)
    for index, (motion_flange, motion_cam) in enumerate(pairs):
        row = slice(3 * index, 3 * index + 3)
        left[row, :] = motion_flange[:3, :3] - np.eye(3)
        right[row] = rotation @ motion_cam[:3, 3] - motion_flange[:3, 3]
    translation, *_ = np.linalg.lstsq(left, right, rcond=None)

    pose_flange_cam = np.eye(4, dtype=np.float64)
    pose_flange_cam[:3, :3] = rotation
    pose_flange_cam[:3, 3] = translation

    spread_m, spread_rad = target_spread(poses_base_flange, poses_cam_tag, pose_flange_cam)
    return pose_flange_cam, spread_m, math.degrees(spread_rad)


#: Ausreisser: eine Aufnahme, deren Brettlage mehr als `OUTLIER_MAD_FACTOR` mal
#: die robuste Streuung (MAD) ueber dem Median der Abweichungen liegt -- aber
#: nie unter den Untergrenzen: bei sauberen Daten ist die MAD fast null, und
#: ohne Untergrenze waere jedes Hundertstel Millimeter ein "Ausreisser".
OUTLIER_MAD_FACTOR = 3.0
OUTLIER_FLOOR_M = 0.002
OUTLIER_FLOOR_DEG = 0.3
#: Hoechstens dieser Anteil der Aufnahmen wird verworfen.
OUTLIER_MAX_FRACTION = 0.25
#: So viele Aufnahmen bleiben mindestens uebrig.
MIN_KEPT = 4


@dataclass(frozen=True)
class HandEyeSolution:
    """Ergebnis von `solve_hand_eye_robust`."""

    pose_flange_cam: Pose
    #: Echtes RMS der Abweichungen der Brettlage vom Mittel, ueber die
    #: verwendeten Aufnahmen.
    rms_position_m: float
    rms_rotation_deg: float
    #: Groesste Abweichung unter den verwendeten Aufnahmen.
    max_position_m: float
    max_rotation_deg: float
    #: Je Eingabe-Aufnahme `(Abweichung in m, in Grad)` der Brettlage vom Mittel
    #: der verwendeten -- auch fuer verworfene.
    residuals: tuple[tuple[float, float], ...]
    #: Indizes der verworfenen Eingabe-Aufnahmen, in der Reihenfolge des Verwerfens.
    rejected: tuple[int, ...]
    used_count: int


def _reconstruct(poses_base_flange, poses_cam_tag, pose_flange_cam) -> list[Pose]:
    """`T_base_tag = T_base_flansch · T_flansch_cam · T_cam_tag` je Aufnahme."""
    return [
        compose(flange, pose_flange_cam, tag)
        for flange, tag in zip(poses_base_flange, poses_cam_tag)
    ]


def _distance(centre: Pose, pose: Pose) -> tuple[float, float]:
    return (
        translation_distance_m(centre, pose),
        math.degrees(rotation_distance_rad(centre, pose)),
    )


def _threshold(values: list[float], floor: float) -> float:
    """Median + `OUTLIER_MAD_FACTOR` robuste Standardabweichungen, mindestens `floor`."""
    median = float(np.median(values))
    mad = float(np.median(np.abs(np.asarray(values) - median)))
    return max(floor, median + OUTLIER_MAD_FACTOR * 1.4826 * mad)


def solve_hand_eye_robust(
    poses_base_flange: Sequence[Pose],
    poses_cam_tag: Sequence[Pose],
    *,
    reject: bool = True,
) -> HandEyeSolution:
    """`solve_hand_eye` mit Ausreisserpruefung und ehrlichen Guetezahlen.

    Der Tag (oder das Brett) steht fest, also muss `T_base_tag` aus jeder
    Aufnahme dieselbe Pose ergeben. Das Residuum einer Aufnahme ist ihr
    Abstand zum Mittel **der anderen** (leave-one-out): eine schlechte
    Aufnahme zieht das Mittel nicht zu sich. Liegt das groesste Residuum ueber
    der Schwelle, wird genau diese Aufnahme verworfen und neu geloest, bis
    nichts mehr auffaellt. Verworfen werden hoechstens `OUTLIER_MAX_FRACTION`
    der Aufnahmen und nie unter `MIN_KEPT`; scheitert das Neulosen (die
    uebrigen drehen nicht genug um mehrere Achsen), bleibt die letzte gute
    Loesung.

    Raises:
        ValueError: wie `solve_hand_eye`, schon fuer die erste Loesung.
    """
    flanges = list(poses_base_flange)
    tags = list(poses_cam_tag)
    pose, _, _ = solve_hand_eye(flanges, tags)
    used = list(range(len(flanges)))
    rejected: list[int] = []
    max_rejected = int(len(flanges) * OUTLIER_MAX_FRACTION)

    while reject and len(rejected) < max_rejected and len(used) > max(MIN_SAMPLES, MIN_KEPT):
        reconstructed = _reconstruct(flanges, tags, pose)
        loo = {
            i: _distance(
                average_poses([reconstructed[j] for j in used if j != i]), reconstructed[i]
            )
            for i in used
        }
        threshold_m = _threshold([loo[i][0] for i in used], OUTLIER_FLOOR_M)
        threshold_deg = _threshold([loo[i][1] for i in used], OUTLIER_FLOOR_DEG)
        worst = max(used, key=lambda i: max(loo[i][0] / threshold_m, loo[i][1] / threshold_deg))
        if max(loo[worst][0] / threshold_m, loo[worst][1] / threshold_deg) <= 1.0:
            break
        trial = [i for i in used if i != worst]
        try:
            trial_pose, _, _ = solve_hand_eye([flanges[i] for i in trial], [tags[i] for i in trial])
        except ValueError:
            break
        used, pose = trial, trial_pose
        rejected.append(worst)

    reconstructed = _reconstruct(flanges, tags, pose)
    centre = average_poses([reconstructed[i] for i in used])
    residuals = tuple(_distance(centre, pose_i) for pose_i in reconstructed)
    kept = [residuals[i] for i in used]
    return HandEyeSolution(
        pose_flange_cam=pose,
        rms_position_m=math.sqrt(sum(m * m for m, _ in kept) / len(kept)),
        rms_rotation_deg=math.sqrt(sum(d * d for _, d in kept) / len(kept)),
        max_position_m=max(m for m, _ in kept),
        max_rotation_deg=max(d for _, d in kept),
        residuals=residuals,
        rejected=tuple(rejected),
        used_count=len(used),
    )


def load_hand_eye(path) -> HandEye:
    """Read a hand-eye calibration from JSON."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Hand-Auge-Kalibrierung nicht gefunden: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = data.get("schema")
    if schema != SCHEMA:
        raise ValueError(
            f"Unbekanntes Hand-Auge-Schema '{schema}' in {path} (erwartet {SCHEMA})"
        )
    pose = data.get("poseFlangeCam")
    if not pose:
        raise ValueError(f"Hand-Auge-Datei {path} enthaelt kein 'poseFlangeCam'")
    return HandEye(
        pose_flange_cam=pose_from_dict(pose),
        frame_id=str(data.get("frameId", "")),
        calibration_id=str(data.get("calibrationId", "")),
        rms_position_m=_or_nan(data.get("rmsPositionM")),
        rms_rotation_deg=_or_nan(data.get("rmsRotationDeg")),
        sample_count=int(data.get("sampleCount", 0)),
        max_position_m=_or_nan(data.get("maxPositionM")),
        max_rotation_deg=_or_nan(data.get("maxRotationDeg")),
    )


def save_hand_eye(path, hand_eye: HandEye) -> None:
    """Write a hand-eye calibration as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {
        "schema": SCHEMA,
        "frameId": hand_eye.frame_id,
        "calibrationId": hand_eye.calibration_id or f"{hand_eye.frame_id or path.stem}@{now}",
        "poseFlangeCam": pose_to_dict(hand_eye.pose_flange_cam),
        "rmsPositionM": _finite(hand_eye.rms_position_m),
        "rmsRotationDeg": _finite(hand_eye.rms_rotation_deg),
        "maxPositionM": _finite(hand_eye.max_position_m),
        "maxRotationDeg": _finite(hand_eye.max_rotation_deg),
        "sampleCount": int(hand_eye.sample_count),
        "createdAt": now,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _finite(value: float):
    """Map NaN to `None` -- `json.dumps` would otherwise emit invalid JSON."""
    number = float(value)
    return None if math.isnan(number) or math.isinf(number) else round(number, 6)


def _or_nan(value) -> float:
    return float("nan") if value is None else float(value)


__all__ = [
    "MIN_SAMPLES",
    "SCHEMA",
    "HandEye",
    "HandEyeSolution",
    "RobotAnchor",
    "anchor_world_base",
    "camera_pose_from_robot",
    "load_hand_eye",
    "save_hand_eye",
    "solve_hand_eye",
    "solve_hand_eye_robust",
    "target_spread",
]

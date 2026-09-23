"""Render synthetic scenes: images with **known** ground truth, no camera.

This underlies `tests/test_tag_pipeline.py` and is the only way to check the
whole chain (detector -> pose -> placement -> module pose) against reference
values instead of itself: tag poses are prescribed here, projected with the
real intrinsics, and then recovered by the library.

    # image series with tags plus matching calibration
    python tools/make_synthetic_scene.py --out /tmp/szene --count 20 \
        --calibration /tmp/szene/kalibrierung.json

    # chessboard views for the calibration path
    python tools/make_synthetic_scene.py --out /tmp/board --count 20 --mode chessboard

The tools then run without hardware:

    PYTHONPATH=src python3 -m tagloc.cli.calibrate --source /tmp/board \
        --out /tmp/kalib.json
    PYTHONPATH=src python3 -m tagloc.cli.build_tagmap --source /tmp/szene \
        --calibration /tmp/szene/kalibrierung.json --anchor 0 --out /tmp/tagmap.json

Rendered with `cv2.aruco.generateImageMarker` (fallback `drawMarker`) and
`cv2.warpPerspective`; corner points come from `cv2.projectPoints` using
exactly the intrinsics also written to the calibration file. That's what
makes the image match the calibration, and only that makes the reference
value a reference value.

`cv2` is imported only inside functions -- `--help` and importing this
module work without OpenCV.
"""

import argparse
import math
import random
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tagloc.boards import BoardSpec, chessboard_object_points  # noqa: E402
from tagloc.calibration import CameraCalibration, save_calibration  # noqa: E402
from tagloc.geometry import (  # noqa: E402
    Pose,
    compose,
    from_rotation_translation,
    invert,
    pose_to_dict,
    rotation_from_rvec,
    to_rvec_tvec,
)
from tagloc.jsonio import write_json  # noqa: E402
from tagloc.pose import tag_object_points  # noqa: E402

SCHEMA = "wsc.vision.synthetic/1"

#: Filename of the written views -- sortable, so `ImageFolderSource` delivers
#: them in capture order. PNG because `.gitignore` swallows `*.jpg` and
#: because JPEG artifacts would distort corner accuracy.
IMAGE_PATTERN = "ansicht_{index:03d}.png"
TRUTH_FILE = "wahrheit.json"


@dataclass(frozen=True)
class TagPlacement:
    """A tag in the world: ID, pose, and edge length in meters."""

    tag_id: int
    pose_world_tag: Pose
    size_m: float = 0.08


def _as_placement(item) -> TagPlacement:
    """Accept a `TagPlacement` or the triple `(tag_id, T_world_tag, size_m)`."""
    if isinstance(item, TagPlacement):
        return item
    tag_id, pose, size_m = item
    return TagPlacement(int(tag_id), np.asarray(pose, dtype=np.float64), float(size_m))


# --------------------------------------------------------------------------
# Camera and poses
# --------------------------------------------------------------------------


def synthetic_calibration(
    image_size: tuple[int, int] = (1600, 1200),
    *,
    focal_px: float | None = None,
    frame_id: str = "cam_synth",
) -> CameraCalibration:
    """Ideal pinhole camera: principal point at image centre, zero distortion.

    `focal_px` unset = image width, i.e. roughly 53 degrees horizontal FOV.
    The result feeds both rendering and the calibration file, so image and
    calibration can never drift apart.
    """
    width, height = int(image_size[0]), int(image_size[1])
    focal = float(focal_px) if focal_px else float(width)
    matrix = np.array(
        [[focal, 0.0, (width - 1) / 2.0], [0.0, focal, (height - 1) / 2.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    return CameraCalibration(
        camera_matrix=matrix,
        distortion=np.zeros(5, dtype=np.float64),
        image_size=(width, height),
        frame_id=frame_id,
        calibration_id=f"{frame_id}@synthetisch",
        rms_reprojection_error=0.0,
        sample_count=0,
        board={"type": "synthetisch"},
    )


def _scaled_calibration(calibration: CameraCalibration, factor: int) -> CameraCalibration:
    """Intrinsics for rendering on the supersampled grid.

    At factor `s`, target-image point `x` maps to `s*x + (s-1)/2` in the
    large image -- exactly the mapping `cv2.resize` with `INTER_AREA` later
    reverses. Without the `(s-1)/2` offset, the final image would sit half a
    pixel off from the calibration.
    """
    if factor <= 1:
        return calibration
    matrix = np.asarray(calibration.camera_matrix, dtype=np.float64).copy()
    matrix[0, 0] *= factor
    matrix[1, 1] *= factor
    matrix[0, 2] = matrix[0, 2] * factor + (factor - 1) / 2.0
    matrix[1, 2] = matrix[1, 2] * factor + (factor - 1) / 2.0
    width, height = calibration.image_size
    return replace(
        calibration,
        camera_matrix=matrix,
        distortion=np.zeros(5, dtype=np.float64),
        image_size=(width * factor, height * factor),
    )


def look_at(eye, target, up=(0.0, 0.0, 1.0)) -> Pose:
    """Return the camera pose `T_world_cam` looking from `eye` toward `target`.

    Result in the OpenCV optical frame: +Z along the view direction, +X
    right, +Y down. `up` is only a hint for image orientation; if it's
    parallel to the view direction, world axis +Y is used instead.
    """
    eye = np.asarray(eye, dtype=np.float64).reshape(3)
    target = np.asarray(target, dtype=np.float64).reshape(3)
    up = np.asarray(up, dtype=np.float64).reshape(3)
    forward = target - eye
    norm = float(np.linalg.norm(forward))
    if norm < 1e-9:
        raise ValueError("Kamera und Zielpunkt fallen zusammen")
    forward = forward / norm
    if abs(float(np.dot(up, forward))) > 0.999:
        up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    down = -(up - float(np.dot(up, forward)) * forward)
    down = down / float(np.linalg.norm(down))
    right = np.cross(down, forward)
    return from_rotation_translation(np.column_stack([right, down, forward]), eye)


def tag_pose(position, yaw_deg: float = 0.0, pitch_deg: float = 0.0) -> Pose:
    """Tag pose in the world: lies flat (+Z up), rotated about Z then X."""
    yaw = rotation_from_rvec([0.0, 0.0, math.radians(yaw_deg)])
    pitch = rotation_from_rvec([math.radians(pitch_deg), 0.0, 0.0])
    return from_rotation_translation(yaw @ pitch, position)


def default_tag_layout() -> list[TagPlacement]:
    """Return the example cell: four reference tags and one module tag.

    Sizes are deliberately different -- an assumed global tag size would
    then stand out immediately instead of silently scaling the distance.
    """
    return [
        TagPlacement(0, tag_pose((0.000, 0.000, 0.000), 0.0), 0.070),
        TagPlacement(1, tag_pose((0.130, 0.000, 0.000), 15.0), 0.070),
        TagPlacement(2, tag_pose((0.130, 0.110, 0.000), -20.0), 0.060),
        TagPlacement(3, tag_pose((0.000, 0.110, 0.000), 0.0), 0.060),
        TagPlacement(7, tag_pose((0.065, 0.220, 0.020), 40.0), 0.055),
    ]


def orbit_views(
    count: int,
    *,
    center=(0.065, 0.105, 0.0),
    radius: float = 0.30,
    height: float = 0.46,
    start_deg: float = -90.0,
    sweep_deg: float = 260.0,
) -> list[Pose]:
    """Return camera poses on an arc above the scene.

    The oblique view angle is deliberate: head-on to a square marker, tilt
    is poorly determined (exactly the ambiguity `TagPose.ambiguity_ratio`
    reports). At roughly 30 degrees off-axis, it isn't.
    """
    center = np.asarray(center, dtype=np.float64).reshape(3)
    views = []
    for index in range(max(1, int(count))):
        angle = math.radians(start_deg + sweep_deg * index / max(1, count - 1))
        eye = center + np.array(
            [radius * math.cos(angle), radius * math.sin(angle), height], dtype=np.float64
        )
        views.append(look_at(eye, center))
    return views


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _project(object_points, pose_cam_object: Pose, calibration: CameraCalibration) -> np.ndarray:
    """Project object points into the image using the real intrinsics."""
    import cv2

    rvec, tvec = to_rvec_tvec(pose_cam_object)
    projected, _ = cv2.projectPoints(
        np.asarray(object_points, dtype=np.float64),
        rvec,
        tvec,
        np.asarray(calibration.camera_matrix, dtype=np.float64),
        np.asarray(calibration.distortion, dtype=np.float64),
    )
    return projected.reshape(-1, 2)


def _paste(canvas, patch, source_quad, target_quad) -> None:
    """Paste `patch` into the image perspectively: `source_quad` maps to `target_quad`.

    Computed only in the target's bounding rectangle rather than the whole
    canvas -- with supersampling, that's the difference between seconds and
    minutes. Edges are softly blended via a co-warped mask so the patch
    border doesn't leave a visible step.
    """
    import cv2

    matrix = cv2.getPerspectiveTransform(
        np.asarray(source_quad, dtype=np.float32), np.asarray(target_quad, dtype=np.float32)
    )
    height, width = patch.shape[:2]
    outline = np.array(
        [[-0.5, -0.5], [width - 0.5, -0.5], [width - 0.5, height - 0.5], [-0.5, height - 0.5]],
        dtype=np.float64,
    )
    mapped = cv2.perspectiveTransform(outline.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    left = max(0, int(math.floor(mapped[:, 0].min())))
    top = max(0, int(math.floor(mapped[:, 1].min())))
    right = min(canvas.shape[1], int(math.ceil(mapped[:, 0].max())) + 1)
    bottom = min(canvas.shape[0], int(math.ceil(mapped[:, 1].max())) + 1)
    if right <= left or bottom <= top:
        return
    shift = np.array([[1.0, 0.0, -left], [0.0, 1.0, -top], [0.0, 0.0, 1.0]]) @ matrix
    size = (right - left, bottom - top)
    warped = cv2.warpPerspective(patch, shift, size, flags=cv2.INTER_LINEAR, borderValue=0)
    alpha = cv2.warpPerspective(
        np.full((height, width), 255, dtype=np.uint8), shift, size,
        flags=cv2.INTER_LINEAR, borderValue=0,
    )
    weight = alpha.astype(np.float64) / 255.0
    region = canvas[top:bottom, left:right].astype(np.float64)
    blended = region * (1.0 - weight) + warped.astype(np.float64) * weight
    canvas[top:bottom, left:right] = np.clip(blended, 0.0, 255.0).astype(np.uint8)


def marker_patch(family: str, tag_id: int, marker_px: int, quiet_px: int):
    """Return a marker image with a white quiet zone: `(image, marker corners)`.

    The quiet zone is required, not cosmetic: without a white border no
    detector finds the black marker frame. Returned corners sit on the
    **outer edge** of the black frame (hence the half pixels) -- that's the
    edge `size_m` refers to and that the detector measures.
    """
    from tagloc.detector import aruco_dictionary, generate_marker

    marker_px = int(marker_px)
    marker = generate_marker(aruco_dictionary(family), tag_id, marker_px)
    quiet_px = int(quiet_px)
    patch = np.full((marker_px + 2 * quiet_px, marker_px + 2 * quiet_px), 255, dtype=np.uint8)
    patch[quiet_px : quiet_px + marker_px, quiet_px : quiet_px + marker_px] = marker
    low = quiet_px - 0.5
    high = quiet_px + marker_px - 0.5
    return patch, [(low, low), (high, low), (high, high), (low, high)]


def _inside(points, image_size: tuple[int, int], margin: float = 2.0) -> bool:
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    width, height = image_size
    return bool(
        points[:, 0].min() >= margin
        and points[:, 1].min() >= margin
        and points[:, 0].max() <= width - 1 - margin
        and points[:, 1].max() <= height - 1 - margin
    )


def _render_canvas(calibration: CameraCalibration, image_size, supersample: int, background: int):
    """Prolog beider Renderer: `(Leinwand, Render-Intrinsik, Faktor, Zielgroesse)`.

    Die Leinwand ist um `supersample` feiner als das Zielbild, die
    Intrinsik passend dazu (siehe `_scaled_calibration`).
    """
    width, height = tuple(image_size or calibration.image_size)
    factor = max(1, int(supersample))
    render_calibration = _scaled_calibration(
        synthetic_from(calibration, (width, height)), factor
    )
    canvas = np.full((height * factor, width * factor), int(background), dtype=np.uint8)
    return canvas, render_calibration, factor, (width, height)


def _downsample(canvas, size: tuple[int, int], factor: int):
    """Epilog beider Renderer: auf die Zielgroesse mitteln, als BGR zurueck."""
    import cv2

    image = canvas
    if factor > 1:
        image = cv2.resize(canvas, size, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)


def render_tags(
    placements,
    pose_world_cam: Pose,
    calibration: CameraCalibration,
    *,
    image_size: tuple[int, int] | None = None,
    family: str = "tag36h11",
    marker_px: int = 320,
    quiet_px: int = 48,
    background: int = 235,
    supersample: int = 2,
):
    """Render one view of the scene and return its ground truth.

    Returns `(image in BGR, {tag_id: T_cam_tag})`. The truth dict only
    contains tags fully visible in the image -- a test shouldn't fail on a
    tag it couldn't possibly see.

    `supersample` renders on an integer-finer grid and downsamples with
    `INTER_AREA` afterward. This substitutes for the edge smoothing a real
    camera's optics provide; without it, marker edges alias and corners get
    less accurate than the task requires.
    """
    canvas, render_calibration, factor, size = _render_canvas(
        calibration, image_size, supersample, background
    )
    pose_cam_world = invert(pose_world_cam)

    prepared = [_as_placement(item) for item in placements]
    # Draw back to front so overlapping tags occlude correctly instead of
    # by list order.
    prepared.sort(key=lambda item: -float(compose(pose_cam_world, item.pose_world_tag)[2, 3]))

    truth: dict[int, Pose] = {}
    for placement in prepared:
        pose_cam_tag = compose(pose_cam_world, placement.pose_world_tag)
        if float(pose_cam_tag[2, 3]) <= 1e-3:
            continue  # behind or inside the camera
        corners = _project(tag_object_points(placement.size_m), pose_cam_tag, render_calibration)
        if not _inside(corners, render_calibration.image_size, margin=factor * 2.0):
            continue
        patch, quad = marker_patch(family, placement.tag_id, marker_px, quiet_px)
        _paste(canvas, patch, quad, corners)
        truth[placement.tag_id] = pose_cam_tag

    return _downsample(canvas, size, factor), truth


def synthetic_from(calibration: CameraCalibration, image_size) -> CameraCalibration:
    """Adapt intrinsics to a different image size without scaling.

    If image size is given separately from calibration, the calibration is
    authoritative and the size is just a crop. Anything else would be a
    silent rescale -- exactly the error `check_resolution` warns about.
    """
    size = (int(image_size[0]), int(image_size[1]))
    if tuple(calibration.image_size) == size:
        return calibration
    return replace(
        calibration,
        camera_matrix=np.asarray(calibration.camera_matrix, dtype=np.float64).copy(),
        distortion=np.asarray(calibration.distortion, dtype=np.float64).copy(),
        image_size=size,
    )


# --------------------------------------------------------------------------
# Chessboard
# --------------------------------------------------------------------------


def _chessboard_patch(spec: BoardSpec, square_px: int, margin_squares: int):
    """Return the board as an image: `(image, board area corners)`.

    A board with `cols` x `rows` **inner** corners has `cols+1` x `rows+1`
    squares. The white border around it is required: `findChessboardCorners`
    won't find a board cropped flush to its edge.
    """
    across = spec.cols + 1
    down = spec.rows + 1
    offset = int(margin_squares)
    square_px = int(square_px)
    patch = np.full(
        ((down + 2 * offset) * square_px, (across + 2 * offset) * square_px), 255, dtype=np.uint8
    )
    for column in range(across):
        for row in range(down):
            if (column + row) % 2:
                continue
            top = (row + offset) * square_px
            left = (column + offset) * square_px
            patch[top : top + square_px, left : left + square_px] = 0
    low_x = offset * square_px - 0.5
    low_y = offset * square_px - 0.5
    high_x = (offset + across) * square_px - 0.5
    high_y = (offset + down) * square_px - 0.5
    return patch, [(low_x, low_y), (high_x, low_y), (high_x, high_y), (low_x, high_y)]


def _chessboard_outline(spec: BoardSpec, expand_squares: float = 0.0) -> np.ndarray:
    """Return the four outer corners of the board area, in the board frame.

    The origin sits on the first inner corner, one square width from the
    edge -- as `boards.chessboard_object_points` assumes. `expand_squares`
    grows the rectangle to include the white quiet zone, which must be in
    the image or `findChessboardCorners` won't find the board.
    """
    square = float(spec.square_size_m)
    grow = float(expand_squares) * square
    left = -square - grow
    top = -square - grow
    right = spec.cols * square + grow
    bottom = spec.rows * square + grow
    return np.array(
        [[left, top, 0.0], [right, top, 0.0], [right, bottom, 0.0], [left, bottom, 0.0]],
        dtype=np.float64,
    )


def render_chessboard(
    pose_cam_board: Pose,
    calibration: CameraCalibration,
    spec: BoardSpec = BoardSpec(),
    *,
    image_size: tuple[int, int] | None = None,
    square_px: int = 48,
    margin_squares: int = 1,
    background: int = 235,
    supersample: int = 2,
):
    """Render a chessboard view. Returns `(image in BGR, T_cam_board)`.

    The pose is the ground truth; returning it saves a test from having to
    thread the input through again.
    """
    canvas, render_calibration, factor, size = _render_canvas(
        calibration, image_size, supersample, background
    )
    corners = _project(_chessboard_outline(spec), pose_cam_board, render_calibration)
    patch, quad = _chessboard_patch(spec, square_px, margin_squares)
    _paste(canvas, patch, quad, corners)
    return _downsample(canvas, size, factor), np.asarray(pose_cam_board, dtype=np.float64)


def chessboard_views(
    count: int,
    calibration: CameraCalibration,
    spec: BoardSpec = BoardSpec(),
    *,
    distance_m: float = 0.55,
    seed: int = 20260917,
    image_size: tuple[int, int] | None = None,
) -> list[Pose]:
    """Return board poses for a calibration series: tilted, shifted, rotated.

    Without tilt, calibration is underdetermined -- all views would carry
    the same information, and focal length and distortion couldn't be
    separated. Each pose is checked: if the board plus its white border
    sticks out of frame, it's discarded and redrawn.
    """
    size = tuple(image_size or calibration.image_size)
    view_calibration = synthetic_from(calibration, size)
    centre = chessboard_object_points(spec, np.float64).mean(axis=0)
    generator = random.Random(int(seed))

    poses: list[Pose] = []
    attempts = 0
    while len(poses) < int(count) and attempts < int(count) * 60:
        attempts += 1
        rotation = rotation_from_rvec(
            [
                math.radians(generator.uniform(-32.0, 32.0)),
                math.radians(generator.uniform(-32.0, 32.0)),
                math.radians(generator.uniform(-180.0, 180.0)),
            ]
        )
        depth = distance_m * generator.uniform(0.85, 1.25)
        # Lateral offset out to the image edge: a calibration taken only from
        # the centre gets a good RMS but useless distortion coefficients.
        target = np.array(
            [
                depth * generator.uniform(-0.22, 0.22),
                depth * generator.uniform(-0.18, 0.18),
                depth,
            ],
            dtype=np.float64,
        )
        pose = from_rotation_translation(rotation, target - rotation @ centre)
        # Check more than just the inner corners: a cropped board is only
        # usable if its white border is still in frame too.
        outline = _project(_chessboard_outline(spec, 1.0), pose, view_calibration)
        if _inside(outline, size, margin=2.0):
            poses.append(pose)
    if len(poses) < int(count):
        raise RuntimeError(
            f"Nur {len(poses)} von {count} Board-Ansichten passen ins Bild "
            "-- groesseren Abstand (--distance-m) oder kleineres Board waehlen."
        )
    return poses


# --------------------------------------------------------------------------
# Writing folders
# --------------------------------------------------------------------------


def write_tag_scene(
    out_dir: Path,
    count: int,
    calibration: CameraCalibration,
    *,
    placements=None,
    family: str = "tag36h11",
    supersample: int = 2,
    min_tags: int = 2,
) -> dict:
    """Write `count` tag views plus `wahrheit.json`."""
    import cv2

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prepared = [_as_placement(item) for item in (placements or default_tag_layout())]

    views = []
    for index, pose_world_cam in enumerate(orbit_views(count), start=1):
        image, truth = render_tags(
            prepared, pose_world_cam, calibration, family=family, supersample=supersample
        )
        if len(truth) < int(min_tags):
            continue
        name = IMAGE_PATTERN.format(index=index)
        cv2.imwrite(str(out_dir / name), image)
        views.append(
            {
                "file": name,
                "poseWorldCam": pose_to_dict(pose_world_cam),
                "tagsInCamera": {
                    str(tag_id): pose_to_dict(pose) for tag_id, pose in sorted(truth.items())
                },
            }
        )

    truth_document = {
        "schema": SCHEMA,
        "mode": "tags",
        "tagFamily": family,
        "imageSize": list(calibration.image_size),
        "anchorTagId": prepared[0].tag_id if prepared else 0,
        "tags": [
            {
                "tagId": placement.tag_id,
                "sizeM": placement.size_m,
                "poseInWorld": pose_to_dict(placement.pose_world_tag),
            }
            for placement in sorted(prepared, key=lambda item: item.tag_id)
        ],
        "views": views,
    }
    write_json(out_dir / TRUTH_FILE, truth_document)
    return truth_document


def write_chessboard_scene(
    out_dir: Path,
    count: int,
    calibration: CameraCalibration,
    spec: BoardSpec = BoardSpec(),
    *,
    distance_m: float = 0.55,
    supersample: int = 2,
    seed: int = 20260917,
) -> dict:
    """Write `count` chessboard views plus `wahrheit.json`."""
    import cv2

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    views = []
    poses = chessboard_views(count, calibration, spec, distance_m=distance_m, seed=seed)
    for index, pose_cam_board in enumerate(poses, start=1):
        image, pose = render_chessboard(
            pose_cam_board, calibration, spec, supersample=supersample
        )
        name = IMAGE_PATTERN.format(index=index)
        cv2.imwrite(str(out_dir / name), image)
        views.append({"file": name, "poseCamBoard": pose_to_dict(pose)})

    truth_document = {
        "schema": SCHEMA,
        "mode": "chessboard",
        "imageSize": list(calibration.image_size),
        "board": spec.as_dict(),
        "views": views,
    }
    write_json(out_dir / TRUTH_FILE, truth_document)
    return truth_document


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synthetische Bilder mit bekannter Wahrheit rendern (ohne Kamera)"
    )
    parser.add_argument("--out", type=Path, required=True, help="Zielordner der Bilder")
    parser.add_argument("--count", type=int, default=20, help="Anzahl der Ansichten")
    parser.add_argument(
        "--mode", choices=("tags", "chessboard"), default="tags", help="Was gerendert wird"
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=None,
        help="Die passende synthetische Kalibrierung hierhin schreiben",
    )
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1200)
    parser.add_argument(
        "--focal-px", type=float, default=None, help="Brennweite in Pixeln (Standard: Bildbreite)"
    )
    parser.add_argument("--frame-id", default="cam_synth", help="Bezugsrahmen der Kalibrierung")
    parser.add_argument("--family", default="tag36h11", help="Tag-Familie")
    parser.add_argument(
        "--supersample", type=int, default=2, help="Ueberabtastung beim Rendern (1 = aus)"
    )
    parser.add_argument("--cols", type=int, default=9, help="Schachbrett: innere Ecken je Zeile")
    parser.add_argument("--rows", type=int, default=6, help="Schachbrett: innere Ecken je Spalte")
    parser.add_argument("--square-size-m", type=float, default=0.030)
    parser.add_argument(
        "--distance-m", type=float, default=0.55, help="Schachbrett: mittlerer Abstand"
    )
    parser.add_argument("--seed", type=int, default=20260917, help="Zufallssaat der Board-Posen")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.count < 1:
        raise SystemExit("--count muss mindestens 1 sein")

    calibration = synthetic_calibration(
        (args.width, args.height), focal_px=args.focal_px, frame_id=args.frame_id
    )
    if args.calibration is not None:
        save_calibration(args.calibration, calibration)
        print(f"Kalibrierung geschrieben: {args.calibration}")

    if args.mode == "tags":
        document = write_tag_scene(
            args.out, args.count, calibration, family=args.family, supersample=args.supersample
        )
        print(f"{len(document['views'])} Ansichten mit {len(document['tags'])} Tags")
        print("Anker-Tag:", document["anchorTagId"])
    else:
        spec = BoardSpec(
            type="chessboard",
            cols=args.cols,
            rows=args.rows,
            square_size_m=args.square_size_m,
        )
        document = write_chessboard_scene(
            args.out,
            args.count,
            calibration,
            spec,
            distance_m=args.distance_m,
            supersample=args.supersample,
            seed=args.seed,
        )
        print(f"{len(document['views'])} Schachbrettansichten, Board {spec.cols}x{spec.rows}")

    print(f"Bilder und {TRUTH_FILE} in: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

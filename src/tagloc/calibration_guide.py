"""Guidance while collecting calibration views: is this view new, what is missing.

`tagloc.cli.calibrate` leaves that judgement to the operator at the keyboard.
A remote calibration (vision server + frontend) has no keyboard next to the
camera, so the rules have to be explicit -- and then they may as well be
testable.

The heuristic follows ROS `camera_calibration`: each view is reduced to four
numbers -- where the board sits (`x`, `y`), how large it appears (`size`,
i.e. distance) and how skewed its outline is (`skew`, i.e. tilt). A view is
*new* if these differ enough from every view already taken, and the set is
well conditioned once each number has spanned a sufficient range. Tilt is
the one operators forget: without it focal length and distortion cannot be
separated, and the calibration still converges -- to wrong values
(doc/apriltag-e2e-test.md 3.3).

Pure numpy, no cv2. The board outline comes from a homography fitted here,
which also works for a ChArUco board that sticks partly out of the image.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .boards import CHARUCO, BoardSample, BoardSpec

PARAM_NAMES = ("x", "y", "size", "skew")

#: Range each parameter has to span over all views before it counts as
#: covered. Position and size as in ROS `camera_calibration`. Skew is lower
#: than ROS's 0.5: tilting the board by the +-30 degrees that
#: doc/apriltag-e2e-test.md asks for reaches about 0.3 in this metric
#: (measured on `tools/make_synthetic_scene.py` views), and 0.5 would keep
#: asking for more tilt after the documented procedure was followed.
PARAM_RANGES = (0.7, 0.7, 0.4, 0.3)

#: Minimum L1 distance of the four parameters to every earlier view.
MIN_PARAM_DISTANCE = 0.2

#: Mean corner shift between two analysed frames, as a fraction of the image
#: diagonal, below which the board counts as held still. About 6 px at
#: 1280x720 -- motion blur shifts corners sub-pixel and spoils the RMS.
STILL_TOLERANCE = 0.004

GRID_CELLS = 3

#: Acceptance thresholds, the same as in doc/apriltag-e2e-test.md.
RMS_GOOD_PX = 0.5
RMS_USABLE_PX = 1.0
COVERAGE_GOOD = 0.7
COVERAGE_USABLE = 0.6

_ROW_NAMES = ("oben", "Mitte", "unten")
_COL_NAMES = ("links", "Mitte", "rechts")


@dataclass(frozen=True)
class ViewParams:
    """The four numbers a calibration view is judged by, each in [0, 1]."""

    x: float
    y: float
    size: float
    skew: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.size, self.skew)


@dataclass(frozen=True)
class Hint:
    """What the operator should do next: a stable code plus a German sentence."""

    code: str
    text: str


def board_plane_points(sample: BoardSample, spec: BoardSpec) -> np.ndarray | None:
    """Return the board-plane coordinates (in squares) of the detected corners.

    Same order as `sample.corners`. ChArUco corners carry their id, laid out
    row-major over the `(cols-1) x (rows-1)` inner corners; a chessboard is
    only ever found complete, in grid order. `None` if the sample does not
    fit the spec.
    """
    count = sample.count()
    if spec.type == CHARUCO:
        if sample.ids is None:
            return None
        ids = np.asarray(sample.ids).reshape(-1).astype(int)
        per_row = spec.cols - 1
        if len(ids) != count or per_row < 1:
            return None
        return np.column_stack([ids % per_row + 1, ids // per_row + 1]).astype(np.float64)
    if count != spec.cols * spec.rows:
        return None
    return np.mgrid[0 : spec.cols, 0 : spec.rows].T.reshape(-1, 2).astype(np.float64)


def _normalisation(points: np.ndarray) -> np.ndarray:
    """Hartley normalisation: centroid to the origin, mean distance sqrt(2)."""
    centre = points.mean(axis=0)
    spread = np.sqrt(((points - centre) ** 2).sum(axis=1)).mean()
    scale = math.sqrt(2.0) / spread if spread > 1e-12 else 1.0
    return np.array(
        [[scale, 0.0, -scale * centre[0]], [0.0, scale, -scale * centre[1]], [0.0, 0.0, 1.0]]
    )


def fit_homography(plane: np.ndarray, image: np.ndarray) -> np.ndarray | None:
    """Fit the homography plane -> image (normalised DLT). `None` if degenerate."""
    plane = np.asarray(plane, dtype=np.float64).reshape(-1, 2)
    image = np.asarray(image, dtype=np.float64).reshape(-1, 2)
    if len(plane) < 4 or len(plane) != len(image):
        return None
    t_plane = _normalisation(plane)
    t_image = _normalisation(image)
    source = np.column_stack([plane, np.ones(len(plane))]) @ t_plane.T
    target = np.column_stack([image, np.ones(len(image))]) @ t_image.T
    rows = []
    for (x, y, _), (u, v, _) in zip(source, target):
        rows.append([-x, -y, -1.0, 0.0, 0.0, 0.0, u * x, u * y, u])
        rows.append([0.0, 0.0, 0.0, -x, -y, -1.0, v * x, v * y, v])
    _, singular, vt = np.linalg.svd(np.asarray(rows))
    # Collinear points leave a second null direction -- no unique homography.
    if singular[-2] < 1e-9 * max(singular[0], 1e-12):
        return None
    normalised = vt[-1].reshape(3, 3)
    homography = np.linalg.inv(t_image) @ normalised @ t_plane
    if abs(homography[2, 2]) < 1e-12:
        return None
    return homography / homography[2, 2]


def board_outline(sample: BoardSample, spec: BoardSpec) -> np.ndarray | None:
    """Return the image quad (4x2) spanned by the board's outermost inner corners.

    Projected through the fitted homography, so a partly visible ChArUco
    board still yields its full outline -- possibly reaching outside the
    image, which is exactly the "board at the edge" case.
    """
    plane = board_plane_points(sample, spec)
    if plane is None:
        return None
    homography = fit_homography(plane, np.asarray(sample.corners).reshape(-1, 2))
    if homography is None:
        return None
    if spec.type == CHARUCO:
        low, high_x, high_y = 1.0, spec.cols - 1.0, spec.rows - 1.0
    else:
        low, high_x, high_y = 0.0, spec.cols - 1.0, spec.rows - 1.0
    corners = np.array(
        [[low, low, 1.0], [high_x, low, 1.0], [high_x, high_y, 1.0], [low, high_y, 1.0]]
    )
    projected = corners @ homography.T
    if np.any(np.abs(projected[:, 2]) < 1e-12) or np.any(projected[:, 2] * projected[0, 2] <= 0):
        return None  # the plane's horizon runs through the board
    return projected[:, :2] / projected[:, 2:3]


def _quad_area(quad: np.ndarray) -> float:
    x, y = quad[:, 0], quad[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _skew(quad: np.ndarray) -> float:
    """Mean deviation of the four corner angles from 90 degrees, scaled to [0, 1]."""
    deviations = []
    for index in range(4):
        corner = quad[index]
        before = quad[index - 1] - corner
        after = quad[(index + 1) % 4] - corner
        norm = float(np.linalg.norm(before) * np.linalg.norm(after))
        if norm < 1e-12:
            return 1.0
        cosine = float(np.clip(np.dot(before, after) / norm, -1.0, 1.0))
        deviations.append(abs(math.pi / 2.0 - math.acos(cosine)))
    return min(1.0, 2.0 * float(np.mean(deviations)))


def view_params(
    sample: BoardSample, spec: BoardSpec, image_size: tuple[int, int]
) -> ViewParams | None:
    """Reduce a view to position, size and skew. `None` if its outline is degenerate."""
    quad = board_outline(sample, spec)
    if quad is None:
        return None
    width, height = float(image_size[0]), float(image_size[1])
    area = _quad_area(quad)
    if area <= 0.0 or width <= 0.0 or height <= 0.0:
        return None
    border = math.sqrt(area)
    centre = quad.mean(axis=0)
    # As in ROS: shrink the image by half the board on every side, otherwise a
    # large board could never reach x = 0 or 1 and would never count as covered.
    def _position(value: float, extent: float) -> float:
        span = extent - border
        if span <= 1e-9:
            return 0.5
        return min(1.0, max(0.0, (value - border / 2.0) / span))

    return ViewParams(
        x=_position(float(centre[0]), width),
        y=_position(float(centre[1]), height),
        size=min(1.0, math.sqrt(area / (width * height))),
        skew=_skew(quad),
    )


def param_distance(first: ViewParams, second: ViewParams) -> float:
    """L1 distance of two views' parameters."""
    return float(sum(abs(a - b) for a, b in zip(first.as_tuple(), second.as_tuple())))


def is_novel(
    params: ViewParams,
    captured: Sequence[ViewParams],
    min_distance: float = MIN_PARAM_DISTANCE,
) -> bool:
    """Whether a view adds information over all views taken so far."""
    return all(param_distance(params, earlier) > min_distance for earlier in captured)


def _matched_corners(
    current: BoardSample, previous: BoardSample, spec: BoardSpec
) -> tuple[np.ndarray, np.ndarray] | None:
    now = np.asarray(current.corners, dtype=np.float64).reshape(-1, 2)
    before = np.asarray(previous.corners, dtype=np.float64).reshape(-1, 2)
    if spec.type == CHARUCO:
        if current.ids is None or previous.ids is None:
            return None
        now_ids = [int(value) for value in np.asarray(current.ids).reshape(-1)]
        before_index = {
            int(value): index for index, value in enumerate(np.asarray(previous.ids).reshape(-1))
        }
        pairs = [(i, before_index[k]) for i, k in enumerate(now_ids) if k in before_index]
        if len(pairs) < 4:
            return None
        return now[[i for i, _ in pairs]], before[[j for _, j in pairs]]
    if len(now) != len(before):
        return None
    return now, before


def corner_shift(
    current: BoardSample,
    previous: BoardSample | None,
    spec: BoardSpec,
    image_size: tuple[int, int],
) -> float | None:
    """Mean corner movement between two frames, as a fraction of the image diagonal.

    `None` if the two frames cannot be compared. A chessboard may be found in
    reversed corner order from one frame to the next (the pattern is
    point-symmetric), so the smaller of both orders is taken.
    """
    if previous is None:
        return None
    matched = _matched_corners(current, previous, spec)
    if matched is None:
        return None
    now, before = matched
    shift = float(np.linalg.norm(now - before, axis=1).mean())
    if spec.type != CHARUCO:
        reversed_shift = float(np.linalg.norm(now - before[::-1], axis=1).mean())
        shift = min(shift, reversed_shift)
    diagonal = math.hypot(float(image_size[0]), float(image_size[1]))
    return shift / diagonal if diagonal > 0 else None


def is_still(
    current: BoardSample,
    previous: BoardSample | None,
    spec: BoardSpec,
    image_size: tuple[int, int],
    tolerance: float = STILL_TOLERANCE,
) -> bool:
    """Whether the board has barely moved since the previous analysed frame."""
    shift = corner_shift(current, previous, spec, image_size)
    return shift is not None and shift <= tolerance


def progress(captured: Sequence[ViewParams]) -> dict[str, float]:
    """How much of the required range each parameter has spanned, in [0, 1]."""
    if not captured:
        return {name: 0.0 for name in PARAM_NAMES}
    values = np.array([params.as_tuple() for params in captured], dtype=np.float64)
    spans = values.max(axis=0) - values.min(axis=0)
    return {
        name: round(min(1.0, float(span) / required), 3)
        for name, span, required in zip(PARAM_NAMES, spans, PARAM_RANGES)
    }


def coverage_grid(
    samples: Sequence[BoardSample], image_size: tuple[int, int], cells: int = GRID_CELLS
) -> list[list[int]]:
    """Count, per image cell, the views that put at least one corner into it.

    Row-major, `grid[row][col]`, row 0 at the top. Distortion is estimated
    from points near the image border -- exactly where the modules sit --
    so empty border cells are the most useful thing to point out.
    """
    width, height = max(float(image_size[0]), 1.0), max(float(image_size[1]), 1.0)
    grid = [[0] * cells for _ in range(cells)]
    for sample in samples:
        points = np.asarray(sample.corners, dtype=np.float64).reshape(-1, 2)
        cols = np.clip((points[:, 0] / width * cells).astype(int), 0, cells - 1)
        rows = np.clip((points[:, 1] / height * cells).astype(int), 0, cells - 1)
        for row, col in set(zip(rows.tolist(), cols.tolist())):
            grid[row][col] += 1
    return grid


def region_name(row: int, col: int, cells: int = GRID_CELLS) -> str:
    """German name of a 3x3 image region, e.g. "oben links", "rechts", "Bildmitte"."""
    if cells != 3:
        return f"Zeile {row + 1}, Spalte {col + 1}"
    vertical, horizontal = _ROW_NAMES[row], _COL_NAMES[col]
    if vertical == "Mitte" and horizontal == "Mitte":
        return "Bildmitte"
    if vertical == "Mitte":
        return horizontal
    if horizontal == "Mitte":
        return vertical
    return f"{vertical} {horizontal}"


def _empty_cells_by_priority(grid: Sequence[Sequence[int]]) -> list[tuple[int, int]]:
    """Empty cells, image corners first, then edges, then the centre."""
    cells = len(grid)
    last = cells - 1

    def rank(cell: tuple[int, int]) -> int:
        row, col = cell
        border_hits = (row in (0, last)) + (col in (0, last))
        return 2 - border_hits

    empty = [(row, col) for row in range(cells) for col in range(cells) if grid[row][col] == 0]
    return sorted(empty, key=lambda cell: (rank(cell), cell))


def next_hint(
    *,
    camera_ok: bool,
    board_visible: bool,
    board_still: bool,
    novel: bool,
    sample_count: int,
    target_samples: int,
    grid: Sequence[Sequence[int]],
    covered: dict[str, float],
    board_type: str,
    auto_capture: bool = True,
) -> Hint:
    """Tell the operator the single most useful next move."""
    if not camera_ok:
        return Hint("no_camera", "Warte auf das Kamerabild ...")
    if sample_count >= target_samples:
        return Hint("enough", "Genug Aufnahmen. Jetzt „Berechnen“ drücken.")
    if not board_visible:
        if board_type == CHARUCO:
            return Hint(
                "show_board",
                "Board ins Kamerabild halten. Es darf teilweise aus dem Bild ragen.",
            )
        return Hint(
            "show_board",
            "Board vollständig ins Kamerabild halten. Beim Schachbrett müssen alle "
            "Ecken sichtbar sein.",
        )
    if not board_still:
        if auto_capture:
            return Hint("hold_still", "Board ruhig halten, bis die Aufnahme ausgelöst wird.")
        return Hint("hold_still", "Board ruhig halten und dann „Aufnehmen“ drücken.")
    if novel:
        if auto_capture:
            return Hint("capturing", "Gut so, Board ruhig halten.")
        return Hint("capturing", "Neue Ansicht. Jetzt „Aufnehmen“ drücken.")

    empty = _empty_cells_by_priority(grid) if sample_count > 0 else []
    if empty:
        row, col = empty[0]
        return Hint(
            "move_to_region",
            f"Board in den Bildbereich {region_name(row, col, len(grid))} bewegen.",
        )
    if covered.get("skew", 0.0) < 1.0:
        return Hint(
            "tilt",
            "Board schräg halten: um etwa 30–45° kippen, abwechselnd um die "
            "waagerechte und die senkrechte Achse.",
        )
    if covered.get("size", 0.0) < 1.0:
        return Hint(
            "vary_distance",
            "Abstand ändern: Board einmal nah an die Kamera, einmal weiter weg halten.",
        )
    if covered.get("x", 0.0) < 1.0:
        return Hint("move_horizontal", "Board weiter an den linken und rechten Bildrand führen.")
    if covered.get("y", 0.0) < 1.0:
        return Hint("move_vertical", "Board weiter an den oberen und unteren Bildrand führen.")
    return Hint("new_pose", "Diese Ansicht gibt es schon. Position, Abstand oder Neigung ändern.")


def rate_calibration(
    rms_px: float, coverage: tuple[float, float], covered: dict[str, float] | None = None
) -> tuple[str, list[str]]:
    """Judge a computed calibration: `("good" | "usable" | "poor", notes)`.

    The RMS alone says little -- a calibration taken only from the image
    centre has a good RMS and useless distortion at the border, which is
    where the modules sit. Hence coverage counts as much as the RMS.
    """
    notes: list[str] = []
    min_coverage = min(float(coverage[0]), float(coverage[1]))
    if not math.isfinite(rms_px):
        return "poor", ["Kein gültiger Reprojektionsfehler."]
    if rms_px < RMS_GOOD_PX and min_coverage >= COVERAGE_GOOD:
        quality = "good"
    elif rms_px < RMS_USABLE_PX and min_coverage >= COVERAGE_USABLE:
        quality = "usable"
    else:
        quality = "poor"
    if rms_px >= RMS_GOOD_PX:
        notes.append(
            f"RMS {rms_px:.2f} px liegt über {RMS_GOOD_PX} px. Ist das Board eben und "
            "stimmt die eingegebene Feldgröße?"
        )
    if min_coverage < COVERAGE_GOOD:
        notes.append(
            f"Abdeckung nur {min_coverage * 100:.0f} %. Die Verzeichnung am Bildrand ist "
            "dann unsicher. Board auch in die Bildecken führen."
        )
    if covered is not None and covered.get("skew", 1.0) < 1.0:
        notes.append("Wenig gekippte Ansichten. Die Brennweite ist dann schlecht bestimmt.")
    return quality, notes


__all__ = [
    "GRID_CELLS",
    "Hint",
    "MIN_PARAM_DISTANCE",
    "PARAM_NAMES",
    "PARAM_RANGES",
    "STILL_TOLERANCE",
    "ViewParams",
    "board_outline",
    "board_plane_points",
    "corner_shift",
    "coverage_grid",
    "fit_homography",
    "is_novel",
    "is_still",
    "next_hint",
    "param_distance",
    "progress",
    "rate_calibration",
    "region_name",
    "view_params",
]

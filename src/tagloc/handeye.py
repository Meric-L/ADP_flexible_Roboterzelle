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
from pathlib import Path

import numpy as np

from .geometry import (
    Pose,
    average_poses,
    compose,
    from_rotation_translation,
    invert,
    pose_from_dict,
    pose_to_dict,
    rotation_distance_rad,
    rotation_from_rvec,
    rvec_from_rotation,
    translation_distance_m,
)
from .jsonio import finite_or_none, utc_now_z

SCHEMA = "wsc.vision.handeye/1"
#: Stellen, auf die `save_hand_eye` die Kennzahlen in der Datei rundet.
FILE_DIGITS = 6

#: Hand-Auge braucht Bewegung um mehr als eine Achse, sonst ist die Rotation
#: nicht bestimmt. Drei Posen sind das rechnerische Minimum; die Kalibrierfahrt
#: sollte deutlich mehr aufnehmen.
MIN_SAMPLES = 3

#: Unterhalb dieses Eigenwerts gilt die Drehachsenverteilung als entartet --
#: der Roboter hat dann im Wesentlichen um nur eine Achse gedreht.
_DEGENERATE_EIGENVALUE = 1e-12

#: Ab so viel Drehung um eine Achse zaehlt sie als "deutlich gedreht".
#: Unterhalb davon bestimmt das Rauschen der Tag-Messung die Rotation mit.
AXIS_THRESHOLD_DEG = 5.0
#: Mindestzahl deutlich gedrehter Achsen, damit `T_flansch_cam` bestimmt ist.
MIN_AXES = 3


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
    #: Ab 28.10.2026, nur bei Brett-Kalibrierung mit gemeinsamer Verfeinerung
    #: (`refine_hand_eye`); sonst NaN bzw. leer, aeltere Dateien haben sie nicht.
    #: Reprojektion aller Brettecken nach der Verfeinerung (px).
    refined_reprojection_rms_px: float = float("nan")
    #: Geschaetzter Brettmassstab gegen das Nennmass (1.0 = passt).
    board_scale: float = float("nan")
    #: Leave-one-out-Vorhersagefehler (RMS ueber die Aufnahmen), px und m.
    loo_error_rms_px: float = float("nan")
    loo_error_rms_m: float = float("nan")
    #: Woher die Intrinsik stammte (`calibration`, `realsense_factory`, ...).
    intrinsics_source: str = ""


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


# --- Gemeinsame Verfeinerung gegen die Pixel (nur Brett) -----------------------

#: Huber-Schwelle in Pixeln: darunter quadratisch, darueber linear gewichtet.
HUBER_DELTA_PX = 1.0
REFINE_MAX_ITERATIONS = 60
#: Schrittweite der numerischen Jacobi-Matrix: Rad / m / Massstab.
_JACOBIAN_STEP = 1e-6


@dataclass(frozen=True)
class HandEyeRefinement:
    """Ergebnis von `refine_hand_eye`."""

    #: `X = T_flansch_cam`.
    pose_flange_cam: Pose
    #: `T_base_brett` (Brett-Ursprung = erste Innenecke), wie das Brett im
    #: Basis-KS liegt -- im Status `targetInBase`.
    pose_base_board: Pose
    #: Massstab der Feldgroesse gegen den Nennwert (1.0 = Brett wie angegeben).
    #: Bei `estimate_scale=False` bleibt er auf dem Startwert.
    board_scale: float
    #: RMS des Pixelabstands (2D-Betrag) ueber alle Ecken aller Aufnahmen,
    #: ungewichtet.
    reprojection_rms_px: float
    iterations: int
    converged: bool
    #: Je Aufnahme der RMS-Pixelfehler (in der Reihenfolge der Eingabe).
    sample_rms_px: tuple[float, ...] = ()


@dataclass(frozen=True)
class LeaveOneOutResult:
    """Ergebnis von `leave_one_out_errors`."""

    #: Je Aufnahme: RMS-Pixelfehler der vorhergesagten Ecken.
    error_px: tuple[float, ...]
    #: Je Aufnahme: mittlerer 3D-Abstand der vorhergesagten Ecken zu den Ecken
    #: der gemessenen `T_cam_brett`, im Kamera-KS in Metern.
    error_m: tuple[float, ...]
    rms_px: float
    rms_m: float


def _project_all(
    inverse_flanges: np.ndarray,
    object_points: np.ndarray,
    camera_matrix: np.ndarray,
    flange_cam: Pose,
    base_board: Pose,
    scale: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Ecken aller Aufnahmen: `(Pixel (n, m, 2), Kamera-KS (n, m, 3))`.

    `inv(F_i · X) · T_base_brett · (s · P)`, ohne Verzeichnung.
    """
    to_camera = invert(flange_cam)[None, :, :] @ inverse_flanges @ base_board[None, :, :]
    points = scale * object_points
    camera = np.einsum("nij,mj->nmi", to_camera[:, :3, :3], points) + to_camera[:, None, :3, 3]
    depth = np.where(np.abs(camera[..., 2]) < 1e-9, 1e-9, camera[..., 2])
    pixels = np.stack(
        [
            camera_matrix[0, 0] * camera[..., 0] / depth + camera_matrix[0, 2],
            camera_matrix[1, 1] * camera[..., 1] / depth + camera_matrix[1, 2],
        ],
        axis=-1,
    )
    return pixels, camera


def _apply_increment(pose: Pose, delta: np.ndarray) -> Pose:
    """Lokaler Zuwachs `(rvec, t)`: `R' = exp(rvec) · R`, `t' = t + t_delta`."""
    rotation = rotation_from_rvec(delta[:3]) @ pose[:3, :3]
    return from_rotation_translation(rotation, pose[:3, 3] + delta[3:6])


def _huber_cost(errors: np.ndarray, delta: float) -> float:
    """Huber-Kosten ueber den 2D-Betrag je Ecke."""
    quadratic = errors <= delta
    return float(
        np.sum(np.where(quadratic, 0.5 * errors**2, delta * (errors - 0.5 * delta)))
    )


def refine_hand_eye(
    poses_base_flange: Sequence[Pose],
    corners_px: Sequence[np.ndarray],
    object_points: np.ndarray,
    camera_matrix: np.ndarray,
    *,
    initial_flange_cam: Pose,
    initial_base_board: Pose,
    initial_scale: float = 1.0,
    estimate_scale: bool = True,
    huber_delta_px: float = HUBER_DELTA_PX,
    max_iterations: int = REFINE_MAX_ITERATIONS,
) -> HandEyeRefinement:
    """Verfeinert `X`, `T_base_brett` und den Brettmassstab gegen die Pixel.

    Die Park-Martin-Loesung legt die Rotation fest und rechnet die Translation
    daraus; ein kleiner Rotationsfehler verschiebt `t_X` um Millimeter. Hier
    gehen alle Brettecken aller Aufnahmen **gemeinsam** ein:

        u_ik = K · (inv(F_i · X) · T_base_brett · (s · P_k))   (perspektivisch)

    verglichen mit den entzerrten Pixeln der Aufnahme. Unbekannte: `X` (6),
    `T_base_brett` (6) und optional `s` (1). Levenberg-Marquardt mit
    numerischer Jacobi-Matrix; die Updates sind lokale Zuwaechse (Drehvektor
    und Verschiebung) auf der laufenden Pose. Huber-Gewichtung (IRLS) haelt
    einzelne schlechte Ecken oder Aufnahmen aus der Loesung.

    `corners_px`: je Aufnahme `(N, 2)` **entzerrte** Ecken in Pixeln
    (`lens.undistort_points`, `P=K`), in der Reihenfolge von `object_points`
    `(N, 3)` (Brett-KS, Nennmass). Startwerte: `initial_flange_cam` aus
    `solve_hand_eye_robust`, `initial_base_board` aus `estimate_base_board`.
    """
    flanges = [np.asarray(f, dtype=np.float64) for f in poses_base_flange]
    if len(flanges) != len(corners_px):
        raise ValueError(f"Verfeinerung: {len(flanges)} Roboterposen zu {len(corners_px)} Eckensaetzen")
    if len(flanges) < MIN_SAMPLES:
        raise ValueError(f"Verfeinerung braucht mindestens {MIN_SAMPLES} Aufnahmen")
    observed = np.stack([np.asarray(c, dtype=np.float64).reshape(-1, 2) for c in corners_px])
    points = np.asarray(object_points, dtype=np.float64).reshape(-1, 3)
    if observed.shape[1] != points.shape[0]:
        raise ValueError("Verfeinerung: Eckenzahl passt nicht zu den Brettpunkten")
    matrix = np.asarray(camera_matrix, dtype=np.float64).reshape(3, 3)
    inverse_flanges = np.stack([invert(f) for f in flanges])

    flange_cam = np.asarray(initial_flange_cam, dtype=np.float64).copy()
    base_board = np.asarray(initial_base_board, dtype=np.float64).copy()
    scale = float(initial_scale)
    free = 13 if estimate_scale else 12

    def residuals(x_pose, board_pose, s) -> np.ndarray:
        pixels, _ = _project_all(inverse_flanges, points, matrix, x_pose, board_pose, s)
        return pixels - observed  # (n, m, 2)

    def step(x_pose, board_pose, s, delta):
        return (
            _apply_increment(x_pose, delta[:6]),
            _apply_increment(board_pose, delta[6:12]),
            s + (delta[12] if estimate_scale else 0.0),
        )

    current = residuals(flange_cam, base_board, scale)
    cost = _huber_cost(np.linalg.norm(current, axis=-1), huber_delta_px)
    damping = 1e-3
    converged = False
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        flat = current.reshape(-1)
        jacobian = np.empty((flat.size, free), dtype=np.float64)
        for column in range(free):
            offset = np.zeros(13, dtype=np.float64)
            offset[column] = _JACOBIAN_STEP
            plus = residuals(*step(flange_cam, base_board, scale, offset)).reshape(-1)
            minus = residuals(*step(flange_cam, base_board, scale, -offset)).reshape(-1)
            jacobian[:, column] = (plus - minus) / (2.0 * _JACOBIAN_STEP)
        norms = np.linalg.norm(current, axis=-1)
        # IRLS: Huber-Gewicht je Ecke, fuer beide Pixelkomponenten gleich.
        corner_weight = np.where(norms <= huber_delta_px, 1.0, huber_delta_px / np.maximum(norms, 1e-12))
        weights = np.repeat(corner_weight.reshape(-1), 2)
        weighted = jacobian * weights[:, None]
        normal = jacobian.T @ weighted
        gradient = weighted.T @ flat
        improved = False
        while damping < 1e12:
            try:
                solved = np.linalg.solve(normal + damping * np.diag(np.diag(normal) + 1e-12), -gradient)
            except np.linalg.LinAlgError:
                damping *= 10.0
                continue
            delta = np.zeros(13, dtype=np.float64)
            delta[:free] = solved
            trial = step(flange_cam, base_board, scale, delta)
            trial_residuals = residuals(*trial)
            trial_cost = _huber_cost(np.linalg.norm(trial_residuals, axis=-1), huber_delta_px)
            if trial_cost < cost:
                relative = (cost - trial_cost) / max(cost, 1e-18)
                flange_cam, base_board, scale = trial
                current, cost = trial_residuals, trial_cost
                damping = max(damping / 3.0, 1e-12)
                improved = True
                converged = relative < 1e-10 or float(np.max(np.abs(delta[:free]))) < 1e-10
                break
            damping *= 4.0
        if not improved:
            # Kein Schritt senkt die Kosten mehr: Minimum erreicht.
            converged = True
            break
        if converged:
            break

    errors = np.linalg.norm(current, axis=-1)
    return HandEyeRefinement(
        pose_flange_cam=flange_cam,
        pose_base_board=base_board,
        board_scale=float(scale),
        reprojection_rms_px=float(np.sqrt(np.mean(errors**2))),
        iterations=iterations,
        converged=converged,
        sample_rms_px=tuple(float(np.sqrt(np.mean(row**2))) for row in errors),
    )


def rotation_diversity(poses_base_flange: list[Pose]) -> tuple[float, int]:
    """`(groesste Drehung zwischen zwei Posen in Grad, Zahl der Drehachsen)`.

    Die Achsen: die Drehvektoren aller Bewegungspaare, ihre Singulaerwerte
    normiert auf die Zahl der Paare. Dreht der Roboter nur um eine Achse,
    liegen alle Vektoren auf einer Linie -- dann ist `T_flansch_cam` nicht
    bestimmt, egal wie viele Aufnahmen.
    """
    count = len(poses_base_flange)
    if count < 2:
        return 0.0, 0
    vectors = []
    spread = 0.0
    for first in range(count):
        for second in range(first + 1, count):
            motion = compose(invert(poses_base_flange[first]), poses_base_flange[second])
            vectors.append(rvec_from_rotation(motion[:3, :3]).reshape(3))
            spread = max(
                spread, rotation_distance_rad(poses_base_flange[first], poses_base_flange[second])
            )
    singular = np.linalg.svd(np.asarray(vectors), compute_uv=False) / math.sqrt(len(vectors))
    axes = int(np.sum(singular >= math.radians(AXIS_THRESHOLD_DEG)))
    return math.degrees(spread), axes


#: Untergrenze der Ausreissergrenze der Verfeinerung, in Pixeln: darunter ist
#: kein Bild ein Ausreisser, auch wenn die MAD fast null ist.
REFINE_OUTLIER_FLOOR_PX = 1.0


def refine_hand_eye_robust(
    poses_base_flange: Sequence[Pose],
    corners_px: Sequence[np.ndarray],
    poses_cam_board: Sequence[Pose],
    object_points: np.ndarray,
    camera_matrix: np.ndarray,
    *,
    estimate_scale: bool = True,
    reject: bool = True,
    huber_delta_px: float = HUBER_DELTA_PX,
) -> tuple[HandEyeRefinement, tuple[int, ...], HandEyeSolution]:
    """Verfeinerung mit Ausreisserpruefung **in Pixeln** (nur Brett).

    Die Park-Martin-Streuung der Brettlage taugt dafuer nicht: ein Massstabs-
    fehler des Bretts bläht sie auf und lieferte gute Aufnahmen als
    Ausreisser. Hier gilt der RMS-Pixelfehler je Aufnahme nach der gemeinsamen
    Verfeinerung (die den Massstab mitschaetzt).

    Start: `solve_hand_eye_robust(..., reject=False)` und die gemittelte
    Brettlage. Dann alle Aufnahmen verfeinern; ist der schlechteste RMS ueber
    `max(REFINE_OUTLIER_FLOOR_PX, Median + OUTLIER_MAD_FACTOR · 1,4826 · MAD)`,
    fliegt diese Aufnahme raus und es wird (Warmstart) neu verfeinert. Hoechstens
    `OUTLIER_MAX_FRACTION` der Aufnahmen, nie unter `MIN_KEPT` verbleibende, und
    nie so, dass die Rest-Posen um weniger als `MIN_AXES` Achsen drehen.

    Liefert `(Verfeinerung der verbleibenden, Indizes der verworfenen
    Eingabe-Aufnahmen in der Reihenfolge des Verwerfens, Park-Martin-Start)`.
    `HandEyeRefinement.sample_rms_px` bezieht sich auf die verbleibenden.

    Raises:
        ValueError: wie `solve_hand_eye`.
    """
    flanges = list(poses_base_flange)
    corners = list(corners_px)
    boards = list(poses_cam_board)
    initial = solve_hand_eye_robust(flanges, boards, reject=False)

    def refine(indices: list[int], start: HandEyeRefinement | None) -> HandEyeRefinement:
        pose_x = start.pose_flange_cam if start else initial.pose_flange_cam
        board = (
            start.pose_base_board
            if start
            else estimate_base_board(
                [flanges[i] for i in indices], [boards[i] for i in indices], pose_x
            )
        )
        return refine_hand_eye(
            [flanges[i] for i in indices],
            [corners[i] for i in indices],
            object_points,
            camera_matrix,
            initial_flange_cam=pose_x,
            initial_base_board=board,
            initial_scale=start.board_scale if start else 1.0,
            estimate_scale=estimate_scale,
            huber_delta_px=huber_delta_px,
        )

    kept = list(range(len(flanges)))
    rejected: list[int] = []
    refinement = refine(kept, None)
    max_rejected = int(len(flanges) * OUTLIER_MAX_FRACTION)
    while reject and len(rejected) < max_rejected and len(kept) > max(MIN_SAMPLES, MIN_KEPT):
        errors = list(refinement.sample_rms_px)
        threshold = _threshold(errors, REFINE_OUTLIER_FLOOR_PX)
        worst = int(np.argmax(errors))
        if errors[worst] <= threshold:
            break
        trial = [i for i in kept if i != kept[worst]]
        if rotation_diversity([flanges[i] for i in trial])[1] < MIN_AXES:
            break
        try:
            trial_refinement = refine(trial, refinement)
        except (ValueError, np.linalg.LinAlgError):
            break
        rejected.append(kept[worst])
        kept, refinement = trial, trial_refinement
    return refinement, tuple(rejected), initial


def estimate_base_board(
    poses_base_flange: Sequence[Pose], poses_cam_board: Sequence[Pose], pose_flange_cam: Pose
) -> Pose:
    """Startlage des Bretts: Mittel von `F_i · X · C_i` (wie `target_spread`)."""
    return average_poses(_reconstruct(poses_base_flange, poses_cam_board, pose_flange_cam))


def leave_one_out_errors(
    poses_base_flange: Sequence[Pose],
    corners_px: Sequence[np.ndarray],
    poses_cam_board: Sequence[Pose],
    object_points: np.ndarray,
    camera_matrix: np.ndarray,
    full: HandEyeRefinement,
    *,
    estimate_scale: bool = True,
    huber_delta_px: float = HUBER_DELTA_PX,
) -> LeaveOneOutResult:
    """Vorhersagefehler je Aufnahme: ohne sie verfeinern, ihre Ecken vorhersagen.

    Fuer jede Aufnahme `i` wird `refine_hand_eye` ohne `i` gerechnet (Warmstart
    aus der Gesamtloesung `full`) und damit vorhergesagt, wo die Ecken in `i`
    liegen muessten. Das ist ein ehrlicheres Mass als die Streuung: ein
    fester Versatz, der in allen Aufnahmen gleich wirkt, bleibt dort unsichtbar,
    ein schlecht bestimmtes `X` sagt aber eine fremde Ansicht schlecht voraus.

    `error_px`: RMS-Abstand zu den entzerrten Pixelecken von `i`.
    `error_m`: mittlerer 3D-Abstand im Kamera-KS zwischen den vorhergesagten
    Ecken (`inv(F_i · X) · T_base_brett · s · P`) und den Ecken der gemessenen
    Brettpose, `s · C_i · P`. Der Faktor `s`: ein Brett, das um `s` groesser ist
    als das Nennmass, erscheint der PnP-Schaetzung mit Nennmass um `1/s`
    verkleinert im Kamera-KS; ohne ihn ginge der Massstabsfehler als Abstand
    ein, nicht der Vorhersagefehler.

    Raises:
        ValueError: weniger als `MIN_SAMPLES + 1` Aufnahmen.
    """
    count = len(poses_base_flange)
    if count < MIN_SAMPLES + 1:
        raise ValueError(f"Leave-one-out braucht mindestens {MIN_SAMPLES + 1} Aufnahmen")
    points = np.asarray(object_points, dtype=np.float64).reshape(-1, 3)
    matrix = np.asarray(camera_matrix, dtype=np.float64).reshape(3, 3)
    homogeneous = np.hstack([points, np.ones((points.shape[0], 1))])
    errors_px: list[float] = []
    errors_m: list[float] = []
    for index in range(count):
        keep = [j for j in range(count) if j != index]
        partial = refine_hand_eye(
            [poses_base_flange[j] for j in keep],
            [corners_px[j] for j in keep],
            points,
            matrix,
            initial_flange_cam=full.pose_flange_cam,
            initial_base_board=full.pose_base_board,
            initial_scale=full.board_scale,
            estimate_scale=estimate_scale,
            huber_delta_px=huber_delta_px,
        )
        flange = np.asarray(poses_base_flange[index], dtype=np.float64)
        pixels, predicted = _project_all(
            invert(flange)[None, :, :],
            points,
            matrix,
            partial.pose_flange_cam,
            partial.pose_base_board,
            partial.board_scale,
        )
        observed = np.asarray(corners_px[index], dtype=np.float64).reshape(-1, 2)
        errors_px.append(float(np.sqrt(np.mean(np.sum((pixels[0] - observed) ** 2, axis=1)))))
        measured = partial.board_scale * (np.asarray(poses_cam_board[index], dtype=np.float64) @ homogeneous.T).T[:, :3]
        errors_m.append(float(np.mean(np.linalg.norm(predicted[0] - measured, axis=1))))
    return LeaveOneOutResult(
        error_px=tuple(errors_px),
        error_m=tuple(errors_m),
        rms_px=float(np.sqrt(np.mean(np.square(errors_px)))),
        rms_m=float(np.sqrt(np.mean(np.square(errors_m)))),
    )


def reconstruction_spread(
    poses_base_flange: Sequence[Pose],
    poses_cam_target: Sequence[Pose],
    pose_flange_cam: Pose,
    used: Sequence[int],
) -> tuple[tuple[tuple[float, float], ...], float, float, float, float]:
    """Streuung von `T_base_ziel` fuer ein gegebenes `X`, wie `solve_hand_eye_robust`.

    Liefert `(Residuen je Aufnahme (m, Grad), RMS m, RMS Grad, max m, max Grad)`;
    Mittel und Statistik nur ueber die Indizes `used`, die Residuen fuer alle.
    Fuer ein verfeinertes `X`, das nicht aus dem Park-Martin-Solver stammt.
    """
    reconstructed = _reconstruct(poses_base_flange, poses_cam_target, pose_flange_cam)
    centre = average_poses([reconstructed[i] for i in used])
    residuals = tuple(_distance(centre, pose) for pose in reconstructed)
    kept = [residuals[i] for i in used]
    return (
        residuals,
        math.sqrt(sum(m * m for m, _ in kept) / len(kept)),
        math.sqrt(sum(d * d for _, d in kept) / len(kept)),
        max(m for m, _ in kept),
        max(d for _, d in kept),
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
        refined_reprojection_rms_px=_or_nan(data.get("refinedReprojectionRmsPx")),
        board_scale=_or_nan(data.get("boardScale")),
        loo_error_rms_px=_or_nan(data.get("looErrorRmsPx")),
        loo_error_rms_m=_or_nan(data.get("looErrorRmsM")),
        intrinsics_source=str(data.get("intrinsicsSource") or ""),
    )


def save_hand_eye(path, hand_eye: HandEye) -> None:
    """Write a hand-eye calibration as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = utc_now_z()
    payload = {
        "schema": SCHEMA,
        "frameId": hand_eye.frame_id,
        "calibrationId": hand_eye.calibration_id or f"{hand_eye.frame_id or path.stem}@{now}",
        "poseFlangeCam": pose_to_dict(hand_eye.pose_flange_cam),
        "rmsPositionM": finite_or_none(hand_eye.rms_position_m, FILE_DIGITS),
        "rmsRotationDeg": finite_or_none(hand_eye.rms_rotation_deg, FILE_DIGITS),
        "maxPositionM": finite_or_none(hand_eye.max_position_m, FILE_DIGITS),
        "maxRotationDeg": finite_or_none(hand_eye.max_rotation_deg, FILE_DIGITS),
        "sampleCount": int(hand_eye.sample_count),
        "createdAt": now,
    }
    # Nur wenn vorhanden: Dateien ohne Verfeinerung bleiben, wie sie waren.
    for key, value in (
        ("refinedReprojectionRmsPx", hand_eye.refined_reprojection_rms_px),
        ("boardScale", hand_eye.board_scale),
        ("looErrorRmsPx", hand_eye.loo_error_rms_px),
        ("looErrorRmsM", hand_eye.loo_error_rms_m),
    ):
        rounded = finite_or_none(value, FILE_DIGITS)
        if rounded is not None:
            payload[key] = rounded
    if hand_eye.intrinsics_source:
        payload["intrinsicsSource"] = hand_eye.intrinsics_source
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _or_nan(value) -> float:
    return float("nan") if value is None else float(value)


__all__ = [
    "AXIS_THRESHOLD_DEG",
    "MIN_AXES",
    "MIN_SAMPLES",
    "SCHEMA",
    "HUBER_DELTA_PX",
    "HandEye",
    "HandEyeRefinement",
    "HandEyeSolution",
    "LeaveOneOutResult",
    "RobotAnchor",
    "anchor_world_base",
    "camera_pose_from_robot",
    "estimate_base_board",
    "leave_one_out_errors",
    "load_hand_eye",
    "reconstruction_spread",
    "refine_hand_eye",
    "refine_hand_eye_robust",
    "rotation_diversity",
    "save_hand_eye",
    "solve_hand_eye",
    "solve_hand_eye_robust",
    "target_spread",
]

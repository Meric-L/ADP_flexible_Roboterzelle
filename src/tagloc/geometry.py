"""Posen, Komposition, Quaternionen. Reine Mathematik, kein OpenCV.

Konventionen im ganzen Projekt:

* Eine Pose ist ein `np.ndarray` der Form `(4, 4)`, `float64`, homogen.
  Kein eigener Typ -- numpy genuegt, und jeder Funktionsname sagt, welches
  Koordinatensystem gemeint ist.
* `T_a_b` bedeutet "Pose von b, ausgedrueckt in a". Komposition:
  `T_a_c = compose(T_a_b, T_b_c)`.
* Laengen in **Metern**, Winkel in **Radiant**, ohne Ausnahme.
* Quaternionen in der Reihenfolge **xyzw** -- passend zur Deklaration der
  Nutzdaten (`"rotation": "quaternion_xyzw"`) und zu dem, was three.js
  erwartet. Nicht wxyz.

Rodrigues ist hier ausgeschrieben, statt `cv2.Rodrigues` aufzurufen, damit
dieses Modul ohne OpenCV importierbar bleibt und die Mathematik ueberall
testbar ist.
"""

import math
from collections.abc import Sequence

import numpy as np

#: Eine homogene 4x4-Transformation.
Pose = np.ndarray

#: Unterhalb dieses Drehwinkels wird die Kleinwinkelnaeherung benutzt.
_ANGLE_EPS = 1e-12


def identity() -> Pose:
    """Liefert die Identitaetspose."""
    return np.eye(4, dtype=np.float64)


def from_rotation_translation(rotation, translation) -> Pose:
    """Baut eine Pose aus einer 3x3-Rotationsmatrix und einem Translationsvektor."""
    rotation = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
    translation = np.asarray(translation, dtype=np.float64).reshape(3)
    pose = np.eye(4, dtype=np.float64)
    pose[:3, :3] = rotation
    pose[:3, 3] = translation
    return pose


def skew(vector) -> np.ndarray:
    """Liefert die schiefsymmetrische Matrix eines 3-Vektors."""
    x, y, z = np.asarray(vector, dtype=np.float64).reshape(3)
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]], dtype=np.float64)


def rotation_from_rvec(rvec) -> np.ndarray:
    """Rodrigues formula: rotation vector -> rotation matrix."""
    rvec = np.asarray(rvec, dtype=np.float64).reshape(3)
    theta = float(np.linalg.norm(rvec))
    if theta < _ANGLE_EPS:
        # Kleinwinkelnaeherung; konvergiert gegen die Identitaet fuer theta -> 0.
        return np.eye(3, dtype=np.float64) + skew(rvec)
    axis = rvec / theta
    k = skew(axis)
    return np.eye(3, dtype=np.float64) + math.sin(theta) * k + (1.0 - math.cos(theta)) * (k @ k)


def rvec_from_rotation(rotation) -> np.ndarray:
    """Kehrt `rotation_from_rvec` um.

    Theta nahe pi wird getrennt behandelt: Der schiefsymmetrische Anteil
    verschwindet dort, und die uebliche Formel verliert die Achse.
    """
    rotation = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
    cos_theta = (np.trace(rotation) - 1.0) / 2.0
    cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
    theta = math.acos(cos_theta)
    if theta < 1e-8:
        return np.zeros(3, dtype=np.float64)
    if math.pi - theta < 1e-6:
        # Achse aus der Diagonale von R + I; das Vorzeichen ist bei pi ohnehin frei.
        diagonal = np.clip((np.diag(rotation) + 1.0) / 2.0, 0.0, None)
        axis = np.sqrt(diagonal)
        largest = int(np.argmax(axis))
        if axis[largest] < 1e-9:
            return np.zeros(3, dtype=np.float64)
        signs = np.ones(3, dtype=np.float64)
        for index in range(3):
            if index != largest and rotation[largest, index] < 0.0:
                signs[index] = -1.0
        axis = axis * signs
        axis = axis / float(np.linalg.norm(axis))
        return axis * theta
    factor = theta / (2.0 * math.sin(theta))
    return factor * np.array(
        [
            rotation[2, 1] - rotation[1, 2],
            rotation[0, 2] - rotation[2, 0],
            rotation[1, 0] - rotation[0, 1],
        ],
        dtype=np.float64,
    )


def from_rvec_tvec(rvec, tvec) -> Pose:
    """Baut eine Pose aus dem rvec/tvec-Paar, das solvePnP liefert."""
    return from_rotation_translation(rotation_from_rvec(rvec), np.asarray(tvec).reshape(3))


def to_rvec_tvec(pose: Pose) -> tuple[np.ndarray, np.ndarray]:
    """Zerlegt eine Pose in `(rvec, tvec)`, wie `cv2.drawFrameAxes` es erwartet."""
    pose = np.asarray(pose, dtype=np.float64)
    return rvec_from_rotation(pose[:3, :3]), pose[:3, 3].copy()


def quaternion_from_rotation(rotation) -> np.ndarray:
    """Rotationsmatrix -> Quaternion xyzw, Vorzeichen auf `w >= 0` normiert.

    Ohne Vorzeichennormierung wuerden zwei mathematisch gleiche Rotationen
    verschiedene Zahlen liefern, was Vergleiche gegen einen Referenzwert
    bricht. Nutzt das Verfahren von Shepperd: Immer den groessten der vier
    Nenner waehlen, um die Division durch nahezu null zu vermeiden.
    """
    rotation = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
    trace = float(np.trace(rotation))
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * scale
        x = (rotation[2, 1] - rotation[1, 2]) / scale
        y = (rotation[0, 2] - rotation[2, 0]) / scale
        z = (rotation[1, 0] - rotation[0, 1]) / scale
    elif rotation[0, 0] > rotation[1, 1] and rotation[0, 0] > rotation[2, 2]:
        scale = math.sqrt(1.0 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2]) * 2.0
        w = (rotation[2, 1] - rotation[1, 2]) / scale
        x = 0.25 * scale
        y = (rotation[0, 1] + rotation[1, 0]) / scale
        z = (rotation[0, 2] + rotation[2, 0]) / scale
    elif rotation[1, 1] > rotation[2, 2]:
        scale = math.sqrt(1.0 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2]) * 2.0
        w = (rotation[0, 2] - rotation[2, 0]) / scale
        x = (rotation[0, 1] + rotation[1, 0]) / scale
        y = 0.25 * scale
        z = (rotation[1, 2] + rotation[2, 1]) / scale
    else:
        scale = math.sqrt(1.0 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1]) * 2.0
        w = (rotation[1, 0] - rotation[0, 1]) / scale
        x = (rotation[0, 2] + rotation[2, 0]) / scale
        y = (rotation[1, 2] + rotation[2, 1]) / scale
        z = 0.25 * scale
    quaternion = np.array([x, y, z, w], dtype=np.float64)
    quaternion = quaternion / float(np.linalg.norm(quaternion))
    if quaternion[3] < 0.0:
        quaternion = -quaternion
    return quaternion


def rotation_from_quaternion(quaternion) -> np.ndarray:
    """Wandelt ein Quaternion xyzw in eine Rotationsmatrix um."""
    x, y, z, w = np.asarray(quaternion, dtype=np.float64).reshape(4)
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm < _ANGLE_EPS:
        raise ValueError("Quaternion mit Laenge null")
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def from_position_quaternion(position, orientation_xyzw) -> Pose:
    """Baut eine Pose aus einer Position (m) und einem Quaternion xyzw."""
    return from_rotation_translation(rotation_from_quaternion(orientation_xyzw), position)


def to_position_quaternion(
    pose: Pose,
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """Zerlegt eine Pose in die Felder `position` und `orientation` der Nutzdaten."""
    pose = np.asarray(pose, dtype=np.float64)
    position = tuple(float(value) for value in pose[:3, 3])
    orientation = tuple(float(value) for value in quaternion_from_rotation(pose[:3, :3]))
    return position, orientation  # type: ignore[return-value]


def compose(*poses: Pose) -> Pose:
    """Verkettet Posen von links nach rechts: `compose(T_a_b, T_b_c) == T_a_c`."""
    result = identity()
    for pose in poses:
        result = result @ np.asarray(pose, dtype=np.float64)
    return result


def invert(pose: Pose) -> Pose:
    """Invertiert eine Pose analytisch statt ueber `np.linalg.inv`.

    Exakt und guenstiger fuer eine starre Transformation: Die Inverse einer
    Rotationsmatrix ist ihre Transponierte.
    """
    pose = np.asarray(pose, dtype=np.float64)
    rotation = pose[:3, :3]
    translation = pose[:3, 3]
    inverse = np.eye(4, dtype=np.float64)
    inverse[:3, :3] = rotation.T
    inverse[:3, 3] = -rotation.T @ translation
    return inverse


def translation_distance_m(first: Pose, second: Pose) -> float:
    """Liefert den Abstand der beiden Urspruenge in Metern."""
    first = np.asarray(first, dtype=np.float64)
    second = np.asarray(second, dtype=np.float64)
    return float(np.linalg.norm(first[:3, 3] - second[:3, 3]))


def rotation_distance_rad(first: Pose, second: Pose) -> float:
    """Liefert den verbleibenden Drehwinkel zwischen zwei Posen in Radiant."""
    first = np.asarray(first, dtype=np.float64)
    second = np.asarray(second, dtype=np.float64)
    relative = first[:3, :3].T @ second[:3, :3]
    cos_theta = (np.trace(relative) - 1.0) / 2.0
    return float(math.acos(float(np.clip(cos_theta, -1.0, 1.0))))


def average_poses(poses: Sequence[Pose], weights: Sequence[float] | None = None) -> Pose:
    """Mittelt mehrere Posen desselben Objekts.

    Translation arithmetisch, Rotation nach Markley: Eigenvektor zum
    groessten Eigenwert von `sum(w q qT)`. Quaternionen komponentenweise zu
    mitteln waere falsch -- das Ergebnis waere nicht normiert, und `q` und
    `-q` (dieselbe Rotation) wuerden sich gegenseitig ausloeschen.

    `weights` ist optional und gewichtet standardmaessig gleich, sodass jeder
    bestehende Aufrufer sein exaktes Ergebnis behaelt. Es existiert wegen der
    vier Welttags: Sie werden aus sehr verschiedenen Abstaenden und Winkeln
    gesehen, und ein ungewichtetes Mittel laesst den schlechtesten von ihnen
    die Kamerapose ziehen.

    Negative oder nicht endliche Gewichte werden abgelehnt, und Gewichte mit
    Summe null fallen auf Gleichgewichtung zurueck -- ein entarteter
    Gewichtsvektor darf nicht still eine Pose ergeben, die bloss der
    numerisch groesste Rest ist.
    """
    if not poses:
        raise ValueError("average_poses braucht mindestens eine Pose")
    if weights is not None and len(weights) != len(poses):
        raise ValueError(f"average_poses: {len(weights)} Gewichte zu {len(poses)} Posen")
    if len(poses) == 1:
        return np.asarray(poses[0], dtype=np.float64).copy()

    if weights is None:
        weight_vector = np.ones(len(poses), dtype=np.float64)
    else:
        weight_vector = np.asarray(weights, dtype=np.float64).reshape(len(poses))
        if not np.all(np.isfinite(weight_vector)) or np.any(weight_vector < 0.0):
            raise ValueError("average_poses: Gewichte muessen endlich und >= 0 sein")
        if weight_vector.sum() <= 0.0:
            weight_vector = np.ones(len(poses), dtype=np.float64)
    weight_vector = weight_vector / weight_vector.sum()

    translations = np.array([np.asarray(p, dtype=np.float64)[:3, 3] for p in poses])
    quaternions = np.array(
        [quaternion_from_rotation(np.asarray(p, dtype=np.float64)[:3, :3]) for p in poses]
    )
    # Keine Vorzeichenangleichung noetig: `q` und `-q` sind dieselbe Rotation,
    # und der Akkumulator ist dagegen unempfindlich, da `q qT == (-q)(-q)T` --
    # anders als ein komponentenweises Mittel, weshalb das hier eigenvektorbasiert bleiben muss.
    accumulator = (quaternions * weight_vector[:, None]).T @ quaternions
    eigenvalues, eigenvectors = np.linalg.eigh(accumulator)
    mean_quaternion = eigenvectors[:, int(np.argmax(eigenvalues))]
    if mean_quaternion[3] < 0.0:
        mean_quaternion = -mean_quaternion
    return from_rotation_translation(
        rotation_from_quaternion(mean_quaternion), weight_vector @ translations
    )


def pose_to_dict(pose: Pose) -> dict[str, list[float]]:
    """Wandelt eine Pose fuer JSON in `{"position": [...], "orientation": [x, y, z, w]}` um."""
    position, orientation = to_position_quaternion(pose)
    return {"position": list(position), "orientation": list(orientation)}


def pose_from_dict(data) -> Pose:
    """Kehrt `pose_to_dict` um."""
    return from_position_quaternion(data["position"], data["orientation"])

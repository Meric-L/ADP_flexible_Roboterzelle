# Hand-Auge und Ankern: einmal am Welttag, dann alle Module in Reichweite

**Status:** fertig — 22.09.2026
**Verantwortlich:** `Agent: Hand-Auge-Kalibrierung und Ankern am Welttag`
**Thema:** apriltag
**Branch:** apriltag

## Ziel

Der Roboter ankert sich **einmal zu Beginn eines Lokalisierungsvorgangs** am
Welttag und misst danach **alle Module in seiner Reichweite** fein ein — ohne
dass dabei noch ein Welttag im Bild sein muss. Die Lücke zwischen zwei
Welttag-Sichtungen schließt die Kinematik.

Vorher ging das nicht: `localize_camera` braucht einen Welttag **im selben
Bild** wie das Modul. Steht die Handkamera 0,3 m vor einem Modul, liegt der
nächste Welttag ein bis zwei Meter entfernt außerhalb des Bildfelds — die Posen
blieben dann im Kamera-KS und damit unbrauchbar für „immer auf die Welttags
bezogen".

## Die Rechnung

Die Kamera sitzt **starr** am Roboter; nur der Roboter als Ganzes bewegt sich.
`T_flansch_cam` ist deshalb eine Konstante und wird **einmal** kalibriert.

```
Einmalig (Hand-Auge):   T_flansch_cam

Einmal je Vorgang:      T_world_base = T_world_cam · inv(T_flansch_cam) · inv(T_base_flansch)
   (Ankern am Welttag)   mit T_world_cam optisch aus dem Welttag

Je Modul danach:        T_world_cam  = T_world_base · T_base_flansch(jetzt) · T_flansch_cam
   (kein Welttag noetig)
```

Kommt doch wieder ein Welttag ins Bild, wird neu geankert. Die Abweichung
zwischen optischem und weitergerechnetem Wert ist die gemessene Drift — sie
wird berichtet, nicht verschwiegen.

## Bereits gelesen

- `CLAUDE.md`, `doc/arbeitsplaene/README.md` (kein offener `apriltag`-Plan)
- `doc/arbeitsplaene/apriltag-welttag-konzept.md` (Vorgänger, `fertig`)
- `doc/apriltag-lokalisierung.md`, `doc/apriltag-referenz.md`
- `doc/vision-server-interface.md` (Payload, Methoden, `frameId`-Regel)
- `doc/vision-system-integration.md` Abschnitt 6.2 (Hand-Auge, `auto_execute`)
- `doc/altlasten.md` (Hand-Auge als offener Punkt)

## Betroffene Dateien

- `src/tagloc/handeye.py` (neu) — Datei-Format, Ankerrechnung, Solver
- `src/vision_server/profiles.py` — `hand_eye_path`
- `src/vision_server/server.py` — Hand-Auge nur fuer `cam_flange`
- `src/tagloc/localize.py` — `CameraLocalization.origin`, Anker-Pfad
- `src/tagloc/cli/calibrate_handeye.py` (neu) — die einmalige Kalibrierfahrt
- `src/vision_server/detection/apriltag.py` — Anker halten, Roboterpose je Job
- `tests/test_handeye.py` (neu), `tests/test_layering.py`
- `doc/apriltag-lokalisierung.md`, `doc/apriltag-referenz.md`

## Schnittstellen

### Daten — `data/handeye/cam_flange.json`

Maschinenspezifisch wie die Kalibrierung, deshalb unter `data/` und nicht
versioniert. Schema **`wsc.vision.handeye/1`**:

```json
{
  "schema": "wsc.vision.handeye/1",
  "frameId": "cam_flange",
  "calibrationId": "cam_flange@2026-09-22T10:15:00Z",
  "poseFlangeCam": {
    "position": [0.041, -0.012, 0.087],
    "orientation": [0.0, 0.0, 0.0, 1.0]
  },
  "rmsPositionM": 0.0012,
  "rmsRotationDeg": 0.08,
  "sampleCount": 14,
  "createdAt": "2026-09-22T10:15:00Z"
}
```

`rmsPositionM`/`rmsRotationDeg` sind die Streuung des rekonstruierten
Zielorts über alle Aufnahmen: steht der Tag fest, muss
`T_base_flansch(i) · T_flansch_cam · T_cam_tag(i)` für alle `i` dieselbe Pose
ergeben. Tut es das nicht, stimmt die Kalibrierung nicht.

### Python

```python
# Modul: src/tagloc/handeye.py   (nur numpy, kein cv2 -- siehe Abweichungen)
SCHEMA = "wsc.vision.handeye/1"

@dataclass(frozen=True)
class HandEye:
    pose_flange_cam: Pose
    frame_id: str = ""
    calibration_id: str = ""
    rms_position_m: float = float("nan")
    rms_rotation_deg: float = float("nan")
    sample_count: int = 0

def load_hand_eye(path) -> HandEye
def save_hand_eye(path, hand_eye: HandEye) -> None

@dataclass(frozen=True)
class RobotAnchor:
    """Wo die Roboterbasis im Welt-KS steht, samt Herkunft."""
    pose_world_base: Pose
    world_tag_id: int          # Welttag, an dem geankert wurde
    spread_m: float            # Guete der Welttag-Lokalisierung beim Ankern
    spread_rad: float

def anchor_world_base(pose_world_cam: Pose, pose_base_flange: Pose,
                      pose_flange_cam: Pose) -> Pose:
    """T_world_base = T_world_cam · inv(T_flansch_cam) · inv(T_base_flansch)."""

def camera_pose_from_robot(pose_world_base: Pose, pose_base_flange: Pose,
                           pose_flange_cam: Pose) -> Pose:
    """T_world_cam = T_world_base · T_base_flansch · T_flansch_cam."""

def target_spread(poses_base_flange, poses_cam_tag, pose_flange_cam
                  ) -> tuple[float, float]:
    """Streuung des rekonstruierten Tag-Orts -- das Guetemass."""

def solve_hand_eye(poses_base_flange, poses_cam_tag) -> tuple[Pose, float, float]:
    """Park und Martin ueber N Roboterposen auf einen festen Tag, in numpy.

    Liefert T_flansch_cam und die Streuung des rekonstruierten Zielorts.
    Braucht mindestens 3 Posen mit Drehung um mehrere Achsen.
    """

# Modul: src/tagloc/localize.py
ORIGIN_WORLD_TAGS = "world_tags"   # optisch, genau
ORIGIN_ROBOT_POSE = "robot_pose"   # aus Anker + Kinematik weitergerechnet
# CameraLocalization bekommt `origin: str`

def anchor_from_localization(localization, pose_base_flange, hand_eye) -> RobotAnchor
def localize_camera_from_anchor(anchor, pose_base_flange, hand_eye) -> CameraLocalization
def anchor_drift(anchor, localization, pose_base_flange, hand_eye) -> tuple[float, float]

# Modul: src/vision_server/detection/apriltag.py
def robot_pose_from_parameters(parameters) -> Pose | None
```

CLI der einmaligen Kalibrierfahrt:

```
python -m tagloc.cli.calibrate_handeye --samples fahrt/aufnahmen.json \
    --calibration data/calibration/cam_flange.json \
    --out data/handeye/cam_flange.json
```

Aufnahmeliste `wsc.vision.handeye.samples/1`: je Eintrag ein Bild und die
zugehoerige Roboterpose; der Tag muss waehrend der ganzen Fahrt liegen bleiben.

### Annahme zur Roboterpose (bestätigungsbedürftig)

`T_base_flansch` kommt als **Job-Parameter** an den Hand-Pi, über das bereits
vorhandene Feld `DetectionRequest.parameters` — sieben Floats
`(x, y, z, qx, qy, qz, qw)`, Meter und Quaternion xyzw wie überall. Begründung:
der Roboter steht während der `samples_per_job` Aufnahmen ohnehin still, damit
entfällt jedes Zeitstempel-Abgleichproblem, und es entsteht keine neue
Netzabhängigkeit zwischen Hand-Pi und Roboter-Server.

Die Alternative — der Hand-Pi abonniert `urn:plcm:robot-server:ur5e` als
OPC-UA-Client — bleibt additiv nachrüstbar, ohne dass sich der Payload ändert.

Fehlt der Parameter und ist kein Welttag im Bild, bleibt alles wie bisher im
Kamera-KS. Kein Raten.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Keine Hand-Auge-Datei | Ankern nicht möglich; nur der optische Pfad wirkt, Warnung beim Öffnen |
| Noch nicht geankert, kein Welttag im Bild | Posen im Kamera-KS, `frameId = cam_flange` |
| Roboterpose fehlt am Job | wie „nicht geankert" — es wird nicht geraten |
| Welttag sichtbar **und** geankert | optischer Wert gewinnt, Drift gegen den Anker wird als Attribut berichtet |
| `solve_hand_eye` mit < 3 Posen | `ValueError` mit Klartext |

## Vorgehen

1. `handeye.py`: Format, `anchor_world_base`, `camera_pose_from_robot`, Solver.
2. `localize.py`: `origin`, `RobotAnchor`-Anbindung.
3. `detection/apriltag.py`: Anker halten, Roboterpose je Job auswerten.
4. CLI `calibrate_handeye` für die einmalige Kalibrierfahrt.
5. Tests: synthetische Kette, Solver gegen bekannte Wahrheit, Drift.
6. Fach-MDs nachziehen; `auto_execute` bleibt trotzdem `False`.

## Offene Fragen

- Transport der Roboterpose (siehe Annahme oben) — Job-Parameter vs. OPC-UA-Abo.
- Ob nach jedem Modul oder nur am Ende eines Vorgangs nachgeankert wird, sobald
  ein Welttag zufällig wieder im Bild ist. Vorerst: immer, wenn er da ist.

## Abweichungen vom Plan

- **Der Solver rechnet in numpy statt über `cv2.calibrateHandEye`.** Die
  Funktion ist in OpenCV 5.0 nicht mehr nach Python exportiert — nur die
  `CALIB_HAND_EYE_*`-Konstanten sind übrig, im venv dieses Rechners
  (OpenCV 5.0.0) also nicht aufrufbar. Das Repo trägt bewusst 4.x **und** 5.x
  (`doc/altlasten.md` D3), und eine Versionsverzweigung an einer Stelle, die
  einmal im Leben der Zelle läuft, wäre schlechter als dreißig Zeilen
  Mathematik nach Park und Martin. Nebeneffekt: `tagloc.handeye` bleibt
  komplett OpenCV-frei und steht in `test_layering.PURE_MODULES` statt in
  `LAZY_CV2_MODULES`.
- **`RobotAnchor` liegt in `handeye.py`, nicht in `localize.py`.** Sonst hätten
  sich die beiden Module gegenseitig importiert. `localize.py` legt nur die
  Hülle drum (`anchor_from_localization`, `localize_camera_from_anchor`,
  `anchor_drift`).
- **`AprilTagProfileConfig.hand_eye_path` ist standardmäßig `None`.** Nur der
  Hand-Pi bekommt in `server.py` einen Pfad gesetzt; die Deckenkamera sitzt
  nicht am Roboter, ein Pfad für sie wäre eine Datei, die nie entsteht, und
  erzeugte bei jedem Start eine Warnung.
- **`NUMERIC_NOISE_RAD = 1e-6` im Test.** Bei rechnerisch exakten Eingaben
  bleibt eine Winkelstreuung von rund 2e-08 rad (1,2e-06 Grad) aus der
  Eigenwertzerlegung in `average_poses` übrig. Ein echter Fehler liegt
  Größenordnungen darüber, was `test_the_spread_exposes_a_wrong_transform` an
  einem um 10 mm verschobenen Versatz zeigt.

## Nach Abschluss

- [x] Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- [x] Zeile im Index `doc/arbeitsplaene/README.md`.
- [x] Konzept in `doc/apriltag-lokalisierung.md` (1.2, neue 1.4, 1.5, 9),
      `doc/apriltag-referenz.md`, `doc/vision-server-interface.md`,
      `doc/altlasten.md`.
- [x] Testlauf: **316 Tests, OK** (vorher 291; `tests/test_handeye.py` bringt
      25 neue).

**Offen und bewusst so:** `T_flansch_cam` ist an der echten Hardware noch nicht
gemessen. Bis `data/handeye/cam_flange.json` existiert, wirkt nur der optische
Weg — ohne Welttag im Bild bleiben die Posen im Kamera-KS. `auto_execute` bleibt
`False`, bis die Kalibrierung am Roboter belegt ist.

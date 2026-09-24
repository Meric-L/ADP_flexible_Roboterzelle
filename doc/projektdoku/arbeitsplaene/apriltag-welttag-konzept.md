# Welttag-Konzept: vier feste Welttags, alles andere beweglich

**Status:** fertig — 22.09.2026
**Verantwortlich:** `Agent: Koordinatentransformation auf das Welttag-Konzept ziehen`
**Thema:** apriltag
**Branch:** apriltag

## Ziel

Die Koordinatenkette bildet ab, wie die Zelle wirklich aufgebaut ist: **nur die
vier Welttags im Randbereich der Zelle stehen fest**, alles andere ist
beweglich — auch der Roboter, der selbst ein Modul ist und immer verwendet
wird. Die Deckenkamera sieht Welttags, Roboter und Module gleichzeitig und
liefert daraus die Grobübersicht samt der Antwort, **welcher Welttag dem
Roboter am nächsten steht**. Die Hand-in-Eye-Kamera lokalisiert sich
anschließend selbst an genau diesem Welttag und misst die Module genau — immer
auf die Welttags bezogen.

Vorher ging das nicht: `REFERENCE_ROLES` erklärte neben `world` auch
`robot_table` und `reference` zu **festen** Ankern. Der Robotertisch galt damit
als unbeweglich, der Roboter kam als Modul überhaupt nicht vor, und es gab
weder einen Begriff für „der nächste Welttag" noch eine Zusammenführung von
Decken- und Handmessung.

## Bereits gelesen

- `CLAUDE.md` (verbindliche Regeln, Sprache, Arbeitsplanpflicht)
- `doc/arbeitsplaene/README.md` (Doppelarbeits-Check — kein `apriltag`-Plan offen)
- `doc/apriltag-lokalisierung.md` (Konzept, Abschnitte 1–2, 6, 9)
- `doc/apriltag-referenz.md` (Konventionen, `tagloc`-Schnittstellen, Tag-Map-Schema)
- `doc/apriltag-e2e-test.md` (Toleranzen, Abnahme Layer 1/2)
- `doc/vision-server-interface.md` (Payload, `frameId`-Regel, Ergebnisknoten)
- `doc/vision-system.md`, `doc/vision-system-next-steps.md` (Ist-Stand, offene Punkte)

## Betroffene Dateien

- `src/tagloc/tagmap.py` — Rollen, Validierung, `nearest_world_tag`
- `src/tagloc/geometry.py` — `average_poses` mit optionalen Gewichten
- `src/tagloc/localize.py` — `CameraLocalization`, Welttag-Bezug, Fusion
- `src/vision_server/detection/apriltag.py` — Herkunft, neue Attribute
- `src/tagloc/cli/transform.py`, `src/tagloc/cli/detect.py` — Aufrufstellen
- `config/tagmap.example.json` — vier Welttags, Robotertags, Modul
- `doc/apriltag-lokalisierung.md`, `doc/apriltag-referenz.md` — Konzept nachziehen
- `tests/test_tag_map.py`, `tests/test_tag_pipeline.py`, `tests/test_apriltag_source.py`
- `tests/test_welttags.py` (neu) — Rollen, nächster Welttag, Fusion

## Schnittstellen

### Rollen in der Tag-Map

| Rolle | Weltpose | Bedeutung |
| --- | --- | --- |
| `world` | **fest, Pflicht** | Einer der genau vier Welttags im Randbereich der Zelle. Nur diese Rolle liefert `T_world_cam`. |
| `robot` | **`null`** | Tag am Roboter. Beweglich. Mehrere Tags dürfen dieselbe `moduleId` tragen; sie werden auf ein Roboter-KS gemittelt. |
| `module` | **`null`** | Tag an einem Modul. Beweglich. |

Die Rollen `robot_table` und `reference` entfallen ersatzlos — sie waren die
Ursache des falschen Konzepts. Der Robotertisch ist kein fester Anker mehr.

### Daten / Payload

Tag-Map, Schema **`wsc.vision.tagmap/2`** (`config/tagmap.example.json`):

```json
{
  "schema": "wsc.vision.tagmap/2",
  "frameId": "world",
  "anchorTagId": 0,
  "tagFamily": "tag36h11",
  "tags": [
    { "tagId": 0, "role": "world", "sizeM": 0.1, "poseInWorld": { "position": [0,0,0], "orientation": [0,0,0,1] } },
    { "tagId": 1, "role": "world", "sizeM": 0.1, "poseInWorld": { "position": [2.4,0,0], "orientation": [0,0,0,1] } },
    { "tagId": 2, "role": "world", "sizeM": 0.1, "poseInWorld": { "position": [2.4,1.8,0], "orientation": [0,0,0,1] } },
    { "tagId": 3, "role": "world", "sizeM": 0.1, "poseInWorld": { "position": [0,1.8,0], "orientation": [0,0,0,1] } },
    { "tagId": 20, "role": "robot", "sizeM": 0.08, "moduleId": "UR5e", "instanceId": "ur5e-1",
      "tagToModule": { "position": [0,0,-0.12], "orientation": [0,0,0,1] }, "poseInWorld": null },
    { "tagId": 21, "role": "robot", "sizeM": 0.08, "moduleId": "UR5e", "instanceId": "ur5e-1",
      "tagToModule": { "position": [-0.15,0,-0.12], "orientation": [0,0,0,1] }, "poseInWorld": null },
    { "tagId": 7, "role": "module", "sizeM": 0.05, "moduleId": "MOD-A", "instanceId": "mod-a-1",
      "tagToModule": { "position": [0,0,-0.04], "orientation": [0,0,0,1] }, "poseInWorld": null }
  ]
}
```

Schema `/1` wird **migriert** (`reference` → `world`, `robot_table` → `robot`
ohne Weltpose), jede Umdeutung einzeln protokolliert. Ursprünglich war es eine
harte Ablehnung; das legte in der Praxis die ganze Erkennung still — siehe
`apriltag-tagmap-robustheit.md`.

Ergebnis-Payload je Detektion, zusätzliche `attributes` (additiv, bestehende
Felder unverändert):

```json
{
  "source": "ceiling | flange",
  "referenceTagId": 2,
  "referencePosition": [0.31, -0.08, 0.0],
  "referenceOrientation": [0.0, 0.0, 0.0, 1.0],
  "worldTagIds": [0, 1, 2, 3],
  "cameraSpreadM": 0.0021,
  "cameraSpreadDeg": 0.14
}
```

`referenceTagId` ist der Welttag, der **diesem Modul** am nächsten steht;
`referencePosition`/`referenceOrientation` sind `T_worldtag_module`. Das
gemeinsame Welt-KS bleibt in `position`/`orientation` erhalten — die vier
Welttags spannen **ein** Welt-KS auf, einmalig per `build_tagmap` vermessen.

### Python

```python
# Modul: src/tagloc/tagmap.py
WORLD_ROLE = "world"; ROBOT_ROLE = "robot"; MODULE_ROLE = "module"
REFERENCE_ROLES = frozenset({WORLD_ROLE})      # nur der Welttag ist fest
MOVABLE_ROLES  = frozenset({MODULE_ROLE, ROBOT_ROLE})
EXPECTED_WORLD_TAG_COUNT = 4

def validate_tag_map(tag_map: TagMap) -> list[str]:
    """Liefert die Beanstandungen der Karte, leer = in Ordnung.

    Prueft: genau vier Welttags, jeder mit Weltpose; kein bewegliches Tag mit
    Weltpose; Anker ist ein Welttag.
    """

def nearest_world_tag(tag_map: TagMap, pose_world: Pose) -> tuple[int, float] | None:
    """Welttag mit dem kleinsten Abstand zu `pose_world`, und dieser Abstand in m.

    `None`, wenn die Karte keinen Welttag mit Weltpose kennt.
    """

# Modul: src/tagloc/localize.py
@dataclass(frozen=True)
class CameraLocalization:
    pose_world_cam: Pose
    world_tag_ids: tuple[int, ...]   # alle beitragenden Welttags
    primary_tag_id: int              # der naechste/beste Welttag
    spread_m: float                  # Streuung der Einzelschaetzungen
    spread_rad: float

def localize_camera(tag_poses, tag_map, *, max_reprojection_error_px=3.0
                    ) -> CameraLocalization | None:
    """`T_world_cam` aus den sichtbaren Welttags. `None` ohne Welttag im Bild.

    Gewichtet nach Konfidenz; `primary_tag_id` ist der Welttag mit dem
    kleinsten Kameraabstand — der, an dem sich die Handkamera ausrichtet.
    """

def locate_modules(tag_poses, tag_map, *, localization, frame_id,
                   source="", max_reprojection_error_px=3.0) -> list[ModuleLocation]:
    """T_world_module = T_world_cam @ T_cam_tag @ T_tag_module. Einzige Kettenstelle."""

def merge_by_module(locations) -> list[ModuleLocation]:
    """Mittelt Messungen gleicher (moduleId, instanceId) — mehrere Robotertags."""

def merge_locations(*groups) -> list[ModuleLocation]:
    """Fuehrt Decken- und Handergebnis zusammen; die genauere Quelle gewinnt."""
```

`ModuleLocation` erhält `reference_tag_id: int`, `pose_in_reference_tag: Pose | None`
und `source: str`.

### Netz / Betrieb

Unverändert. Beide Pis publizieren weiter eigenständig unter ihren bisherigen
Endpunkten; die Fusion ist eine reine Funktion in `tagloc`, die der Konsument
(Zellserver/Frontend) aufruft. Keine neue Verbindung zwischen den Pis.
`config/tagmap.json` bleibt für beide Pis dieselbe Datei.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Kein Welttag im Bild | `localize_camera` → `None`; Posen bleiben im Kamera-KS, `frameId` = Kamerarahmen (Regel unverändert) |
| Karte hat nicht genau vier Welttags | `validate_tag_map` beanstandet; `open()` protokolliert eine Warnung, startet aber — eine Zelle im Aufbau darf messen |
| Bewegliches Tag mit `poseInWorld` | Beanstandung; die Weltpose wird **ignoriert**, nicht verwendet |
| Tag-Map-Schema `/1` | `ValueError` mit Migrationshinweis beim Laden |
| Welttags widersprechen sich | `spread_m`/`spread_rad` im Payload; kein Abbruch — die Zahl gehört zum Ergebnis |

## Vorgehen

1. `tagmap.py`: Rollen, Schema `/2`, `validate_tag_map`, `nearest_world_tag`.
2. `localize.py`: `CameraLocalization`, `localize_camera`, Welttag-Bezug in
   `ModuleLocation`, `merge_by_module`, `merge_locations`.
3. `detection/apriltag.py`: Herkunft aus `frame_id`, neue Attribute, Mittelung
   mehrerer Robotertags.
4. `config/tagmap.example.json` auf das neue Schema.
5. Tests: `tests/test_welttags.py` neu, bestehende Tests nachziehen.
6. Fach-MDs `apriltag-lokalisierung.md` und `apriltag-referenz.md` nachziehen.

## Offene Fragen

- Hand-Auge-Kalibrierung (`T_flansch_cam`) bleibt weiterhin offen. Sie wird für
  dieses Konzept **nicht gebraucht**: die Handkamera lokalisiert sich rein
  optisch am Welttag. Sie bleibt nur nötig, wenn der Roboter aus der Kamerapose
  auf seine eigene Flanschpose schließen soll.
- Die realen Weltposen der vier Welttags werden per
  `python -m tagloc.cli.build_tagmap` einmalig vermessen; die Zahlen in der
  Beispielkarte sind Platzhalter.

## Abweichungen vom Plan

- **`geometry.average_poses` bekam einen optionalen Parameter `weights`.** Im
  Plan stand „gewichtet nach Konfidenz", ohne zu sagen wo. Die Markley-Mittelung
  kannte keine Gewichte. Der Parameter ist optional und ohne ihn rechnet die
  Funktion bitgleich wie vorher — alle 26 bestehenden Geometrie-Tests laufen
  unverändert durch. Dafür stand `geometry.py` nicht in „Betroffene Dateien";
  hier nachgetragen.
- **`ModuleLocation` bekam zusätzlich `role`.** Ohne die Rolle im Ergebnis
  ließe sich der Roboter nachgelagert nicht von einem Modul unterscheiden, und
  `world_tag_for_robot` hätte raten müssen.
- **Reihenfolge in `acquire_and_detect`:** `expected_but_missing` muss **vor**
  `merge_by_module` laufen. Es vergleicht Tag-IDs, und das Zusammenfassen
  mehrerer Robotertags zu einem Ergebnis ließe die geschluckten Tags sonst als
  fehlend erscheinen.
- **`SPREAD_TOLERANCE_M = 0.008` statt der zunächst angenommenen 3 mm.** Über
  alle vier Ansichten der synthetischen Szene gemessen: Streuung im
  schlechtesten Fall 4,21 mm bei 0,444° (Ansicht 0, die schrägste), gegen einen
  Fehler der **gemittelten** Kamerapose von 0,77 mm. Die Streuung ist eben kein
  Genauigkeitsmaß, sondern die Uneinigkeit der Einzelschätzungen — die
  gewichtete Mittelung ist deutlich besser als ihr schlechtestes Glied.
- **Zusätzlich angefasste Fach-MDs:** `doc/apriltag-e2e-test.md`,
  `doc/vision-server-interface.md` und `doc/praesentation.md` trugen die alten
  Rollen noch in Tabellen und Anleitungen.

## Nach Abschluss

- [x] Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- [x] Zeile im Index `doc/arbeitsplaene/README.md` aktualisiert.
- [x] Konzept in `doc/apriltag-lokalisierung.md` (Abschnitte 1.1–1.4, 3.2, 9)
      und `doc/apriltag-referenz.md` (Abschnitte 5, 7) übernommen.
- [x] Testlauf: `PYTHONPATH=src .venv/bin/python3 -m unittest discover -s tests -t .`
      — **291 Tests, OK** (vorher 260; `tests/test_welttags.py` bringt 31 neue).

Offen bleibt die einmalige Vermessung der vier echten Welttags per
`python -m tagloc.cli.build_tagmap`; `config/tagmap.json` existiert noch nicht,
nur die Vorlage `config/tagmap.example.json`.

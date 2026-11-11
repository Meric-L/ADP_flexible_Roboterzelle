# Experiment: Kinematik-Test mit festem AprilTag (Lage über Gelenkstellungen)

**Status:** in Arbeit — 11.11.2026 (Code fertig, Messung am Roboter steht aus)
**Verantwortlich:** Meric, `Agent: Experiment Kinematik-Test`
**Thema:** vision
**Branch:** `roboter-wiederholgenauigkeit` (ADP, dieses Repo: Auswertung, Pi-Aufruf, Plan); `wiederholgenauigkeit-apriltag` (webskillcomposition: Frontend-Panel)

## Ziel

Prüfen, ob die gemessene Lage eines festen AprilTags in der Roboterbasis von der Gelenkstellung des Roboters abhängt. Das deutet auf einen Kinematik- oder Hand-Auge-Fehler hin. Ein Tag (tag36h11, Standard 80 mm) liegt fest auf dem Tisch. Zuerst wird eine Referenz in einer Stellung gemessen (mehrere Bilder). Danach führt der Bediener den Arm von Hand in andere Gelenkstellungen, in denen die Kamera ungefähr gleich auf den Tag schaut, und nimmt je Stellung mehrere Bilder auf. Ergebnis ist eine Messreihe je Pose für den Bericht.

## Bereits gelesen

- `CLAUDE.md`, `arbeitsplaene/README.md`, `arbeitsplaene/_vorlage.md`
- `arbeitsplaene/experiment-tag-anfahren.md` (Formatvorlage, Experiment am selben Tag)
- `arbeitsplaene/roboter-wiederholgenauigkeit.md` (Aufnahmeformat, Methode `SaveRepeatabilityImage`, Fehlercodes)
- `doc/auswertung/distanztest_20261110.md` (Bezug: unsichtbares Absacken in der Ruhestellung)

## Betroffene Dateien

Dieses Repo (ADP):

- `doc/projektdoku/arbeitsplaene/experiment-kinematik-tag.md` (neu, dieser Plan)
- `doc/projektdoku/arbeitsplaene/README.md` (eine Zeile im Index)
- `src/repeatability_eval/kinematics.py` (neu): Auswertung einer Session (JSON-Quelle, Bericht; PNG-Neuerkennung)
- `src/vision_server/kinematics_autorun.py` (neu): Hintergrundlauf je Session (`SessionRunCoalescer`)
- `src/vision_server/runner/save_repeatability_image.py` (geändert, Hand-Pi): stößt nach jedem Bild einer `_kinematik`-Session die Auswertung an; Signatur und Rückgabe unverändert
- `tools/wiederholgenauigkeit/kinematik.py` (neu): CLI, Standard JSON-Quelle, `--neu-erkennen` für Offline-Neuerkennung
- `tests/test_repeatability_kinematics.py` (neu, 23 Tests inkl. Coalescing)

webskillcomposition (Frontend, `frontend/src/features/cell-modules/`):

- `model/kinematicsTest.ts` (neu): reine Logik (`kinematicsSessionName`, `evaluateSeries`, `evaluatePose`, `summarizePoses`, `messreiheCsv`)
- `model/useKinematicsTest.ts` (neu): Hook
- `components/KinematicsTestPanel.tsx` (neu): Panel „Kinematik-Test“ in der Autolocate-Spalte; zeigt Session-Ordner auf dem Hand-Pi und die drei Dateinamen der Auswertung (Commit 0e4efe9)
- `model/repeatabilityPi.ts` (neu): aus `useRepeatabilityRun.ts` herausgezogene Pi-Aufrufe, ohne Verhaltensänderung
- `model/repeatabilityRecord.ts`: optionales Feld `extra`
- `model/kinematicsTest.test.ts` (neu, 16 Tests)

## Schnittstellen

### OPC UA

Keine Änderung an Methodensignatur, Argumenten oder Rückgabe. Genutzt wird die vorhandene Methode `SaveRepeatabilityImage` auf dem Hand-Pi. Intern startet nach dem Speichern eines Bildes einer `_kinematik`-Session ein Hintergrundlauf der Auswertung (siehe Nachtrag); die Methodenantwort wartet nicht darauf.

| Element | NodeId / BrowsePath | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| `SaveRepeatabilityImage` | `ns=<eigener Namespace>;s=<VisionSystemName>.SaveRepeatabilityImage` | Methode | Aufruf | Speichert das aktuelle Kamerabild einer Phase als PNG plus JSON und liefert die Live-Messung |

**Eingangsargumente** (alle `String`):

| Name | Format |
| --- | --- |
| `Session` | `YYYY-MM-DD_HHMMSS_kinematik`, Muster `^[0-9A-Za-z_-]{1,64}$` |
| `Phase` | `rauschen` (Referenzbilder) oder `zyklen_hand` (Posen) |
| `MetadataJson` | JSON-Text des Clients, Schema `wsc.repeatability.record/1` (siehe Daten) |

**Ausgangsargumente:** `FileName` (String, Pfad des PNG, relativ zum Repo; bei Fehler leer), `ResultJson` (String, Live-Messung; bei Fehler leer), `Error` (Int32, `VisionErrorCode`).

**Fehlercodes:** 0 `OK`, 1 `INVALID_STATE` (kein frisches Kamerabild), 2 `INVALID_ARGUMENT` (Session oder Phase ungültig), 3 `BUSY` (ein Job läuft), 6 `INTERNAL` (PNG- oder Schreibfehler).

### Python

```python
# Modul: src/repeatability_eval/kinematics.py
# Neuerkennung aus den PNGs (CLI --neu-erkennen)
def measure_session(session_dir: Path, tag_id: int | None = None,
                    tag_size_m: float = 0.08, calibration=None,
                    hand_eye=None) -> SessionMeasurement:
    """Wertet einen Session-Ordner aus. Je Bild T_base_tag = T_base_flange · T_flange_cam · T_cam_tag
    (Subpixel-Ecken; Intrinsik und Hand-Auge aus der Record-JSON oder per Option).
    Bilder mit movedDuringCapture oder ohne Erkennung gehen nicht ins Mittel.
    Wirft ValueError, wenn keine Bilder der Phasen rauschen/zyklen_hand oder keine Detektor-Einstellungen vorliegen."""

def summarize_poses(images) -> dict: ...
# Weitere Funktionen des Moduls: pose_table, render_report, plot_deltas, csv_rows

# Standardquelle: JSONs (kein PNG, keine Erkennung)
def measure_session_from_json(session_dir: Path) -> SessionMeasurement:
    """T_cam_tag aus messung.tagInCamM + messung.tagInCamQuat [x,y,z,w];
    T_base_tag = T_base_flange(client.actual.flangeInBase) · T_flange_cam(pi.hand_eye.content.poseFlangeCam) · T_cam_tag.
    Warnung bei Abweichung > 0,01 mm von messung.tagInBaseM.
    Bilder ohne messung.ok: Ausschlussgrund messung.grund bzw. "keine Pi-Messung"."""

def write_session_report(session_dir: Path, *, source: str,  # "json" | "bilder"
                         tag_id: int | None = None, tag_size_m: float = 0.08,
                         calibration=None, hand_eye=None, out=None, csv_posen=None,
                         plot=None, csv_bilder=None, decimal_comma: bool = True) -> dict:
    """Rückgabe: {summary, report, csvPosen, plot, csvBilder}.
    Wirft ValueError bei unbekannter Quelle oder Session ohne Aufnahmen."""

# Modul: src/vision_server/kinematics_autorun.py (Hand-Pi)
KINEMATICS_SESSION_SUFFIX = "_kinematik"

class SessionRunCoalescer:
    def __init__(self, run): ...
    def trigger(self, session: str) -> None:
        """Kehrt sofort zurück; Lauf in asyncio.to_thread. Je Session höchstens ein Lauf;
        Anstöße während eines Laufs ergeben genau einen Folgelauf. Fehler werden nur geloggt."""
```

### Daten / Payload

Record `wsc.repeatability.record/1` (Argument `MetadataJson`) mit zusätzlichen Feldern auf oberster Ebene. Der Pi legt ihn in der Bild-JSON (`adp.repeatability.image/1`) unverändert unter `client` ab, daneben `pi` (Intrinsik, Hand-Auge) und `messung` (Live-Messung) — daher `client.actual.flangeInBase` in der Auswertung:

```json
{
  "test": "kinematik",
  "poseIndex": "0 = Referenz, 1, 2, …",
  "poseLabel": "Referenz | Pose n",
  "target": { "type": "apriltag", "tagId": "int", "tagSizeM": "float" },
  "taught": { "P": "Referenzstellung", "V": null },
  "start": null,
  "actual": {
    "joints": "Gelenkwinkel",
    "flangeInBase": "Flanschpose in der Basis",
    "settled": "bool",
    "movedDuringCapture": "bool",
    "jointSpreadRad": "float"
  }
}
```

`actual.*` wie beim Wiederholgenauigkeitstest. Der Pi rechnet `tagInBaseM` aus `actual.flangeInBase`. Die Auswertung aus den JSONs liest zusätzlich `messung.tagInCamM`, `messung.tagInCamQuat`, `messung.tagInBaseM`, `messung.ok`, `messung.grund` und `pi.hand_eye.content.poseFlangeCam`.

**Ablage auf dem Pi:** `data/rohbilder/roboterwiederholgenauigkeit/<session>/`

- Referenzbilder: Phase `rauschen`, Ordner `02_rauschen`, `cycle` = laufende Bildnummer.
- Posen: Phase `zyklen_hand`, Ordner `04_zyklen_hand`, `cycle` = Posennummer (1, 2, …), `burstIndex` / `burstCount` = Bild innerhalb der Pose.

**Auswertung (ADP):** Ausgabe im Session-Ordner, Standard (Quelle: JSONs). Der Pi schreibt dieselben drei Dateien nach jeder Aufnahme selbst (siehe Nachtrag):

- `kinematik_bericht.md`: Kopf (Session, Tag, Datum, Kalibrier- und Hand-Auge-ID), Messreihe je Pose, Δ gegen Referenz in Position und Winkel (inkl. Gieren um Basis-z), Zusammenfassung (RMS und max |Δ|, Spannweite je Achse), Pearson-Korrelation Gelenk-Δ gegen Δ ab 4 Posen.
- `kinematik_posen.csv`: eine Zeile je Pose, Semikolon, Dezimalkomma.
- `kinematik_posen.png`: Δx/Δy/Δz/|Δ| je Pose.

CLI:

```
PYTHONPATH=src .venv/bin/python tools/wiederholgenauigkeit/kinematik.py <session_ordner> \
  [--tag-id N] [--tag-size-mm 80] [--out] [--csv-posen] [--plot] [--csv] \
  [--dezimalpunkt] [--stdout] [--calibration cam.json] [--handeye he.json] [--neu-erkennen]
```

Standard ist die JSON-Quelle. Mit `--neu-erkennen` werden die Tags offline aus den PNGs neu erkannt; dann gelten `--tag-id`, `--tag-size-mm`, `--calibration` und `--handeye`.

**CSV des Frontend-Panels:** Semikolon, Dezimalkomma. Der Export-Knopf heißt „Messreihe als CSV (lokal)“. Spalten: Pose-Nr, Label, Zeit, Aufnahmen gültig/gesamt, q1..q7 [Grad], Flansch x/y/z [mm], Tag Basis x/y/z [mm], Δx/Δy/Δz/|Δ| [mm], Streuung [µm], bewegt, Tag Kamera x/y/z [mm]. Live-Werte nur Position, der Pi liefert keine Orientierung zurück.

**Session-Name im Frontend:** `YYYY-MM-DD_HHMMSS_kinematik`. Einstellungen in localStorage `wsc.kinematicsTest.settings/1`, Session in `wsc.kinematicsTest.session/1`.

### Netz / Betrieb

Keine neuen Ports, Services oder systemd-Units. Die Frontend-Seite nutzt den bestehenden Pi-Zugriff. Damit die Automatik wirkt, muss der Hand-Pi diesen Branch haben: `git pull` im Repo-Checkout, dann `sudo systemctl restart opcua-server.service` (siehe `deploy/README.md`).

### Fehlerfälle

- Pi nicht verbunden oder alte Software: Buttons gesperrt, Hinweis im Panel.
- Drei Pi-Fehler in Folge: die Serie bricht ab.
- Stopp-Knopf: Serie wird angehalten.
- Referenz ohne Tag in der Basis: Fehlermeldung, keine Referenz wird gesetzt.
- Arm bewegt sich beim Aufnehmen: Pose wird als „bewegt“ markiert.
- Auswertung: `ValueError` bei fehlenden Bildern oder Detektor-Einstellungen.
- Bild ohne Pi-Messung (`messung.ok` falsch): wird von der JSON-Auswertung mit Grund ausgeschlossen.
- Hintergrundauswertung auf dem Pi schlägt fehl: nur Log-Eintrag; die Antwort von `SaveRepeatabilityImage` bleibt unverändert.

Der Roboter fährt in diesem Panel nie; es werden nur Gelenkwinkel gelesen.

## Vorgehen

1. Roboter eingeschaltet lassen, nicht in Ruhestellung oder Bremsen (dort etwa 10 mm unsichtbares Absacken, siehe `doc/auswertung/distanztest_20261110.md`).
2. Panel „Kinematik-Test“ (unter dem Wiederholgenauigkeits-Panel, Autolocate-Spalte): „Neue Session“.
3. Tag-ID (leer = einziger Tag) und Größe einstellen.
4. Arm so stellen, dass der Tag gut im Bild ist, dann „Referenz hier messen“ (Standard 10 Bilder).
5. Arm von Hand führen und loslassen, dann „Pose hier aufnehmen“ (2 s Beruhigung, Standard 5 Bilder). Das Panel zeigt Δx/Δy/Δz/|Δ| zur Referenz sowie die Abstands- und seitliche Abweichung der Kamerasicht auf den Tag.
6. Schritt 5 wiederholen, sinnvoll mit mindestens 4 Posen (dann rechnet die Auswertung zusätzlich die Korrelation mit den Gelenken).
7. Die Auswertung entsteht automatisch auf dem Pi (siehe Nachtrag). „Messreihe als CSV (lokal)“ ist nur ein lokaler Export. `tools/wiederholgenauigkeit/kinematik.py` nur bei Bedarf auf dem Session-Ordner ausführen.

Ergebnis liegt im Session-Ordner auf dem Pi (`kinematik_bericht.md`, `kinematik_posen.csv`, `kinematik_posen.png`). Übernahme nach `doc/auswertung/` wie geplant.

## Offene Fragen

- Messung am Roboter steht aus.
- Die Orientierung eines einzelnen 80-mm-Tags ist schwach bestimmt (Neigung etwa 2,5° in der Ausgangsstellung). Winkel-Δ deshalb vorsichtig deuten; Positions-Δ ist die Hauptgröße.
- Smoke-Test der Auswertung auf der vorhandenen Session `2026-11-10_153201` (Tag 0, nur Referenz): Tag-Mitte in der Basis 421,83 / 16,20 / −2,34 mm, Streuung 3D 52,6 µm, Abstand 350,9 mm. Passt zu `doc/auswertung/distanztest_20261110.md` (421,9 / 16,3 / −2,2 mm). Dieser Test ersetzt nicht die Messung mit Posen.

## Abweichungen vom Plan

- Auswertung läuft automatisch auf dem Pi (nicht im ursprünglichen Plan), siehe Nachtrag.
- Standardquelle der Auswertung sind die JSONs statt der PNGs; die PNG-Erkennung ist nur noch über `--neu-erkennen`.

Wird nach der Messung am Roboter ergänzt.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
- Dauerhaftes Fachwissen in die passende Fach-MD unter `doc/` übernommen (nicht nur hier im Plan stehen lassen).

## Nachtrag 11.11.2026: Auswertung automatisch auf dem Pi

**Wunsch:** Die Messreihe entsteht automatisch, sobald im Frontend getestet wird, ohne manuellen Aufruf auf dem Pi. Die Auswertung liegt neben jedem Bild.

**Ablauf auf dem Pi:**

- `SaveRepeatabilityImage` (`src/vision_server/runner/save_repeatability_image.py`): Signatur und Rückgabe unverändert. Nach jedem erfolgreich gespeicherten Bild einer Session mit Namensende `_kinematik` wird im Hintergrund `write_session_report(<session-ordner>, source="json")` angestoßen. Die Methodenantwort wartet nicht darauf.
- `src/vision_server/kinematics_autorun.py`: `KINEMATICS_SESSION_SUFFIX = "_kinematik"`, `SessionRunCoalescer(run)` mit `trigger(session)`. Kehrt sofort zurück, der Lauf läuft in `asyncio.to_thread`. Je Session höchstens ein Lauf gleichzeitig; Anstöße während eines Laufs ergeben genau einen Folgelauf. Fehler werden nur geloggt.

**Ergebnis** im Session-Ordner `data/rohbilder/roboterwiderholgenauigkeit/<session>/`, nach jeder Aufnahme aktuell, atomar geschrieben (`.tmp` + `os.replace`):

- `kinematik_bericht.md`: Kopf mit „Quelle: Pi-Messung aus den JSONs“ und „Stand: <Zeit>“.
- `kinematik_posen.csv`
- `kinematik_posen.png`: nur wenn matplotlib auf dem Pi installiert ist, sonst übersprungen.

**Auswertung aus den JSONs** (`src/repeatability_eval/kinematics.py`):

- `measure_session_from_json(session_dir) -> SessionMeasurement`: kein PNG, keine Erkennung. Siehe Schnittstellen.
- `write_session_report(session_dir, *, source="json"|"bilder", ...) -> dict`: Rückgabe `summary`, `report`, `csvPosen`, `plot`, `csvBilder`. `ValueError` bei unbekannter Quelle oder Session ohne Aufnahmen.

**Frontend** (webskillcomposition, Commit 0e4efe9): Das Panel zeigt den Session-Ordner auf dem Hand-Pi (aus dem `fileName` der ersten Aufnahme, Funktion `sessionFolderOf`) mit den drei Dateinamen. Der CSV-Knopf heißt „Messreihe als CSV (lokal)“.

**Betrieb:** Der Hand-Pi muss diesen Branch haben: `git pull` im Repo-Checkout, dann `sudo systemctl restart opcua-server.service` (siehe `deploy/README.md`).

**Prüfung:**

- JSON-Quelle und Offline-Neuerkennung liefern an synthetischen Bildern, die über den echten Pi-Speicherweg abgelegt wurden (Hand-Auge mit 90°-Drehung und Versatz), dieselben Pose-Mittel (≤ 1e-6 mm).
- Gemeldeter 10-mm-Flanschversatz erscheint als Δx = 10,000 mm.
- `tests/test_repeatability_kinematics.py`: 23 Tests inkl. Coalescing.
- Session `2026-11-10_153201`: Die JSON-Quelle meldet korrekt 10/10 Bilder ohne Pi-Messung (damals Ziel ChArUco eingestellt). `--neu-erkennen` liefert 421,83 / 16,20 / −2,34 mm. Im Kinematik-Panel ist das Ziel immer AprilTag, der Fall tritt dort nicht auf.

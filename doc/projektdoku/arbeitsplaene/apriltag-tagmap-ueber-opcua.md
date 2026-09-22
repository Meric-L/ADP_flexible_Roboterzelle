# Tag-Map über OPC UA setzen, und die Größenregel der Tags

**Status:** fertig — 22.09.2026
**Verantwortlich:** `Agent: Tag-Map per OPC-UA-Methode setzen`
**Thema:** apriltag
**Branch:** apriltag

## Ziel

Ein neues Modul einzufügen kostet **keinen Eingriff auf dem Pi**. Zwei Teile:

1. **Größenregel festschreiben.** Die Welttags werden in einer anderen Größe
   gedruckt als alle übrigen Tags; alle Modultags haben untereinander dieselbe
   Größe. Damit steht die Modulgröße einmal im Profil (`tag_size_m`), und in
   der Tag-Map stehen nur noch die Welttags — mit ihrer abweichenden `sizeM`.
   Ein neues Modul braucht dann genau einen Eintrag im Frontend-Katalog.
2. **Variante A als Erweiterungsweg.** Sollte doch einmal ein Tag in einer
   dritten Größe dazukommen, muss der Pi das wissen. Statt dafür eine Datei
   von Hand zu bearbeiten, setzt das Backend die Tag-Map über eine
   OPC-UA-Methode. Der Pi schreibt sie lokal weg und benutzt sie sofort.

## Warum die Karte trotzdem auf dem Pi liegt

`localize_camera` braucht `T_world_tag` und `sizeM` **je Bild** im
Erkennungsjob. Läge die Karte im Backend, müsste der Pi rohe `T_cam_tag`
liefern, `frameId` wäre immer das Kamera-KS, und jeder Konsument bräuchte die
Verkettung — gegen die Regel aus `apriltag-lokalisierung.md` Abschnitt 1.2,
dass genau **eine** Funktion sie ausführt. Die lokale Datei bleibt außerdem der
Grund, warum der Pi ohne Backend messen kann.

Variante A ändert daran nichts: das Backend wird zur **Pflegestelle**, die
Datei bleibt der lokale Zwischenspeicher.

## Bereits gelesen

- `CLAUDE.md`, `doc/projektdoku/arbeitsplaene/README.md` (kein offener `apriltag`-Plan)
- `doc/projektdoku/apriltag-lokalisierung.md` (Abschnitte 1.2, 3.2)
- `doc/projektdoku/apriltag-referenz.md` (Tag-Map-Schema, `tagloc`-Schnittstellen)
- `doc/projektdoku/vision-server-interface.md` (Adressraum, Methoden, Fehlercodes)
- `doc/projektdoku/arbeitsplaene/apriltag-tagmap-robustheit.md` (Vorgänger)

## Betroffene Dateien

- `src/tagloc/tagmap.py` — `tag_map_from_json` / `tag_map_to_json`
- `src/vision_server/detection/apriltag.py` — `apply_tag_map`
- `src/vision_server/runner.py` — Methode `SetTagMap`, Knoten `TagMapJson`
- `src/vision_server/address_space.py` — Knoten `TagMapJson`
- `src/vision_server/server.py` — `tag_size_m` je Pi (Modultag-Größe)
- `tests/test_tag_map.py`, `tests/test_apriltag_source.py`
- `doc/projektdoku/apriltag-lokalisierung.md`, `apriltag-referenz.md`,
  `vision-server-interface.md`

## Schnittstellen

### Größenregel

| Tag | Größe woher | Änderungsort |
| --- | --- | --- |
| **Welttag** | `sizeM` des Eintrags in `config/tagmap.json` | Tag-Map (ändert sich nur bei Umbau der Zelle) |
| **Modultag** | `tag_size_m` des Profils, `PI_APRILTAG_PRESETS` | einmalig, gilt für alle Module |
| **abweichender Tag** | eigener Eintrag mit `sizeM` in der Tag-Map | über `SetTagMap`, kein Dateizugriff nötig |

`TagMap.size_for(tag_id, default_m)` setzt das bereits um: steht der Tag in der
Karte, gewinnt dessen `sizeM`, sonst gilt der Profilwert. Neu ist nur, dass
`tag_size_m` ab jetzt ausdrücklich die **Modultag-Größe** bezeichnet.

Eine falsche Tag-Größe skaliert die gemessene Distanz linear mit und fällt
sonst nicht auf — deshalb gehört sie an genau eine Stelle je Sorte.

### OPC UA

| Element | NodeId | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| `SetTagMap` | `ns=<vision>;s=<VisionSystemName>.SetTagMap` | Methode | Aufruf | Setzt die Tag-Map der Zelle |
| `TagMapJson` | `ns=<vision>;s=VisionMachine.TagMapJson` | Variable (String) | Lesen | Die aktuell geladene Karte, als JSON |

```
SetTagMap(
    TagMapJson : String     -- vollstaendige Karte, Schema wsc.vision.tagmap/2
) -> (
    Error : Int32           -- VisionErrorCode
)
```

| `Error` | Wann |
| --- | --- |
| `OK` (0) | Karte übernommen, Datei geschrieben, Quelle nutzt sie sofort |
| `INVALID_ARGUMENT` (2) | kein gültiges JSON, unbekanntes Schema, unbekannte Rolle |
| `BUSY` (3) | ein Job oder eine Kalibrier-Session läuft |
| `INTERNAL` (6) | Datei nicht schreibbar |

Die Karte wird **erst geprüft, dann geschrieben**: eine abgelehnte Karte lässt
die laufende unangetastet. Beanstandungen aus `validate_tag_map` (z. B. nur ein
Welttag statt vier) sind **kein** Fehler — sie werden protokolliert, die Karte
wird übernommen.

### Python

```python
# Modul: src/tagloc/tagmap.py
def tag_map_from_json(text: str, *, source: str = "<string>") -> TagMap:
    """Karte aus JSON-Text. Wirft ValueError bei Schema/Rolle, wie load_tag_map."""

def tag_map_to_json(tag_map: TagMap) -> str:
    """Karte als JSON-Text, Schema /2, nach Tag-Id sortiert."""

# Modul: src/vision_server/detection/apriltag.py
def apply_tag_map(self, tag_map: TagMap) -> None:
    """Uebernimmt eine neue Karte in die laufende Quelle.

    Verwirft den Anker: er wurde gegen die alte Karte gerechnet und waere
    gegenueber der neuen stumm falsch.
    """
```

### Daten

Payload der Methode ist exakt der Dateiinhalt von `config/tagmap.json`
(Schema `wsc.vision.tagmap/2`). Damit ist die Methode und die Datei dasselbe
Format — was das Backend schickt, kann man unverändert in die Datei legen und
umgekehrt.

### Betrieb

Geschrieben wird nach `config/tagmap.json` (Pfad aus
`AprilTagProfileConfig.tag_map_path`). Nach einem Neustart ist die zuletzt
gesetzte Karte also weiterhin aktiv — der Pi bleibt ohne Backend arbeitsfähig.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Job oder Kalibrierung läuft | `BUSY`, Karte unverändert |
| Ungültiges JSON / Schema / Rolle | `INVALID_ARGUMENT`, Karte unverändert |
| Datei nicht schreibbar | `INTERNAL`, **Karte trotzdem aktiv** — messen geht vor Persistenz, beim nächsten Start gilt wieder die alte |
| Karte ohne Welttag | `OK` mit Protokollhinweis; Posen bleiben dann im Kamera-KS |
| Anker der Handkamera | wird verworfen, siehe `apply_tag_map` |

## Vorgehen

1. `tagmap.py`: `tag_map_from_json` / `tag_map_to_json`, `load`/`save` darauf ziehen.
2. `detection/apriltag.py`: `apply_tag_map`.
3. `runner.py` + `address_space.py`: Methode `SetTagMap`, Knoten `TagMapJson`.
4. `server.py`: `tag_size_m` als Modultag-Größe kommentieren.
5. Tests.
6. Fach-MDs: Größenregel und Methode nachtragen.

## Offene Fragen

- Die tatsächlichen Kantenlängen (Welttag vs. Modultag) sind noch nicht
  gemessen. Bis dahin bleiben die bisherigen Werte stehen.

## Abweichungen vom Plan

- **`TagMapJson` und `SetTagMap` hängen an der `apriltag`-Quelle, nicht an der
  Kalibrier-Session.** Im Plan stand nur „wenn es eine Quelle gibt"; im Code
  war der nächstgelegene Aufhänger der Block der Kalibriermethoden. Die
  Methode wird deshalb über dieselbe `calibration_methods`-Sammlung auch unter
  `VisionProgram` gespiegelt — das ist kein Zufall, sondern derselbe Weg, den
  die Kalibriermethoden schon nehmen.
- **`tag_size_m` wurde nicht geändert.** Die echten Kantenlängen sind noch
  nicht gemessen; ein geratener Wert wäre schlechter als der bisherige. Die
  Regel steht in der Fach-MD, die Zahl tragt ihr nach dem Messen ein.

## Nach Abschluss

- [x] Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- [x] Zeile im Index `doc/projektdoku/arbeitsplaene/README.md`.
- [x] Größenregel in `apriltag-lokalisierung.md` Abschnitt 3.2, Methode in
      `vision-server-interface.md` Abschnitt 12.5a/12.5b und im Adressraum-Baum.
- [x] Testlauf: **333 Tests, OK** (vorher 328).

Offen bleibt das Eintragen der **gemessenen** Kantenlängen: `sizeM` der
Welttags in `config/tagmap.json`, `tag_size_m` der Modultags im Profil.

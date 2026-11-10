# Vision-System

Kamerakalibrierung, Marker-Erkennung und Pose-Schätzung. Endet dort, wo eine
Pose fertig berechnet ist — die Auslieferung über OPC UA gehört zu
[`opcua.md`](opcua.md).

## Kernfakten

- Aufbau auf einem **Raspberry Pi mit angeschlossener Kamera**.
- Drei Einstiegsskripte in `src/apriltag/`:
  [`caputure.py`](../src/apriltag/caputure.py) (einfache Bildaufnahme, Dateiname
  im Repo mit Tippfehler), [`calibrate_camera.py`](../src/apriltag/calibrate_camera.py),
  [`detect_apriltags.py`](../src/apriltag/detect_apriltags.py).
- **Diese Skripte sind laut `MANUAL_TEST.md` noch nicht mit echter Kamera
  getestet.** Wer sie zuerst gegen die Hardware laufen lässt, hält das
  Ergebnis unten im Log fest.
- `calibrate_camera.py`: Live-UI, Board-Typ oben per `USE_CHARUCO` umschaltbar,
  Kalibrierung ab **15 Samples**, Ergebnis wird als `calibration.yaml`
  gespeichert.
- `detect_apriltags.py`: Live-Erkennung mit Pose-Overlay, **braucht
  `calibration.yaml` im selben Ordner** — ohne Kalibrierung keine Pose.
- Konfiguration jeweils oben im Skript: `CAMERA_SOURCE`, `TAG_FAMILY`,
  `TAG_SIZE_M`.
- Zwei Erkennungsebenen, siehe [`aufbau.md`](aufbau.md): Layer 1 grobe
  Modulposition über Deckenkamera, Layer 2 genaue Pose über AprilTag am Modul.

## Tiefendokumente

| Dokument | Inhalt |
| --- | --- |
| [`src/apriltag/MANUAL_TEST.md`](../src/apriltag/MANUAL_TEST.md) | Bedienung der Skripte, Tastenbelegung, Setup |
| [`requirements.txt`](../requirements.txt) | Abhängigkeiten |
| [`aufbau.md`](aufbau.md) | Kamerapositionen, Welt- und Tischtags, Ablaufkette |
| [`opcua.md`](opcua.md) | Wie die berechnete Pose als Ergebnis-Payload ausgeliefert wird |

## Offene Fragen

- Tag-Familie und Tag-Größe sind konfigurierbar, aber noch nicht projektweit
  festgelegt. Sobald entschieden: Log-Eintrag hier.
- Kalibrierung: ChArUco oder klassisches Schachbrett — `USE_CHARUCO` ist ein
  Schalter, keine Entscheidung.
- Wo `calibration.yaml` dauerhaft liegt und ob sie versioniert wird, ist offen.

## Log

<!-- Neue Einträge unten anhängen. Format siehe .claude/skills/projektwissen/SKILL.md -->

### 2026-09-13 — John — Gemeinsame Wissensstruktur eingeführt
**Status:** erledigt
**Betrifft:** aufbau, vision, opcua

Siehe [`aufbau.md`](aufbau.md) für den vollständigen Eintrag. Kurz: Erkenntnisse
aus einzelnen Chats gehören ab jetzt in das Log der passenden Bereichsdatei.

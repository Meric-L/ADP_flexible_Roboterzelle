# Frontend-Integration: Kalibrierung + Livestream

Schnellstart-Briefing für einen Agenten/Entwickler, der die
Frontend-Seite von "Kalibrieren im Settings-Menü" (Board bewegen, Live-Bild
mit Abdeckung sehen, Ergebnis bekommen) umsetzt. Ersetzt keine der
bestehenden Dokus, sondern destilliert genau das, was für **diese** Aufgabe
gebraucht wird, und verlinkt für Details.

**Kanonische Referenz für alles, was dieses Dokument nicht abdeckt:**
[`vision-server-interface.md`](vision-server-interface.md) — Verbindungsdaten
(Abschnitt 2), was ein Backend generell können muss (Abschnitt 3),
Fehlercodes (Abschnitt 5), Livestream (Abschnitt 10), die komplette
Kalibrier-Schnittstelle im Detail (Abschnitt 12). Dieses Dokument hier ist
ein Auszug + Ablauf-Fahrplan, kein Ersatz.

## 1. Architektur in einem Satz

Der Vision-Server (ein Prozess pro Pi/Kamera) spricht **OPC-UA**, nicht
HTTP/WebSocket direkt mit dem Browser. Falls dein Frontend nicht selbst
OPC-UA kann: das bestehende Backend abonniert Knoten bereits generisch
("Was das Backend können muss", Abschnitt 3 in `vision-server-interface.md`)
— die Knoten-/Methodennamen unten sind das, was du durch diese
Backend-Abstraktion hindurch ansprichst, keine neue Protokoll-Logik.

## 2. Verbindung

```
Namespace-URI: http://launch-rm.de/vision
```
**Nie den Namespace-Index hart codieren** — immer zur Laufzeit über
`get_namespace_index("http://launch-rm.de/vision")` auflösen (er verschiebt
sich, sobald am Server etwas dazukommt — aktuell ns=7, war schon mal ns=3/4).
Alle Knoten unten liegen unter diesem Index, mit dem stabilen String-Präfix
`VisionMachine.` — z. B. `ns=<vision>;s=VisionMachine.StartCalibration`.

## 3. Kalibrier-Workflow — genau das, was der Button im Settings-Menü braucht

```
Frontend                                          Vision-Server
   |  1. StartCalibration() -----------------------> Error: Int32
   |     (BUSY=3, wenn Job laeuft ODER schon         (0 = OK, Session laeuft)
   |      eine Session offen ist)
   |
   |  2. LatestCameraFrame + CameraStreamMode="calibration" anzeigen
   |     (Board-Ecken + Abdeckung sind im Bild schon eingezeichnet)
   |
   |  3. CaptureCalibrationSample() -----------------> Error: Int32
   |     bei Klick auf "Aufnahme" o.ae.               (0 = Board gefunden,
   |     (kein Auto-Capture -- der Nutzer               5 = nicht gefunden,
   |      entscheidet, wann er ausloest)                 einfach nochmal)
   |     ... wiederholen, Board dabei bewegen/kippen ...
   |
   |  4. CalibrationProgress beobachten (subscriben oder pollen)
   |     -> running, samples, minSamples, coverageX, coverageY
   |     -> sobald "result" auftaucht: FERTIG, Session hat sich selbst
   |        beendet (kein Schritt 5 noetig!)
   |
   |  5. FinishCalibration() ------------------------> Summary: String(JSON)
   |     NUR wenn der Nutzer VOR Erreichen der          Error: Int32
   |     Abdeckungs-Schwelle manuell abbrechen und
   |     trotzdem das bisherige Ergebnis will.
   |     (AbortCalibration() statt dessen, wenn gar
   |      nichts gespeichert werden soll.)
```

### Die Knoten/Methoden im Einzelnen

| Name | NodeId | Typ | Bedeutung |
| --- | --- | --- | --- |
| `StartCalibration` | `ns=<vision>;s=VisionMachine.StartCalibration` | Methode, kein Input, `Error: Int32` | Setzt Samples zurück, startet Session |
| `CaptureCalibrationSample` | `ns=<vision>;s=VisionMachine.CaptureCalibrationSample` | Methode, kein Input, `Error: Int32` | Eine Aufnahme vom aktuellen Bild |
| `FinishCalibration` | `ns=<vision>;s=VisionMachine.FinishCalibration` | Methode, kein Input, `Summary: String, Error: Int32` | Manueller Abschluss (optional, siehe oben) |
| `AbortCalibration` | `ns=<vision>;s=VisionMachine.AbortCalibration` | Methode, kein Input, `Error: Int32` | Verwirft ohne zu speichern |
| `CalibrationProgress` | `ns=<vision>;s=VisionMachine.CalibrationProgress` | String (JSON), **nur lesen** | Live-Fortschritt *einer Session*, siehe unten |
| `ActiveCalibrationInfo` | `ns=<vision>;s=VisionMachine.ActiveCalibrationInfo` | String (JSON), **nur lesen** | Metadaten der **gerade aktiven** Kalibrierung — bleibt stehen, unabhängig von einer laufenden Session, siehe unten |
| `LatestCameraFrame` | `ns=<vision>;s=VisionMachine.LatestCameraFrame` | String (Base64-JPEG), nur lesen | Bild fürs `<img>`/Canvas |
| `CameraStreamMode` | `ns=<vision>;s=VisionMachine.CameraStreamMode` | String, **schreibbar** | `"off"` \| `"apriltag"` \| `"calibration"` — vor/bei Kalibrierstart auf `"calibration"` setzen |

**Kein Board-Parameter wird je vom Frontend gesendet.** Board-Typ,
Maße, cols/rows stehen serverseitig fest (pro Pi konfiguriert). Das
Frontend startet/löst aus/liest, sonst nichts.

### `CalibrationProgress` — das JSON, das du pollst/abonnierst

```jsonc
// waehrend die Session laeuft:
{"running": true, "samples": 12, "minSamples": 15, "coverageX": 0.61, "coverageY": 0.58}

// automatisch beendet, sobald coverageX UND coverageY die Schwelle (Standard 0.7) erreichen:
{
  "running": false, "samples": 18, "minSamples": 15,
  "coverageX": 0.84, "coverageY": 0.9,
  "result": {
    "error": 0, "rms": 0.31, "samples": 18,
    "coverageX": 0.84, "coverageY": 0.9,
    "path": "data/calibration/cam_flange.json"
    // "warning": "..." -- siehe unten, nur wenn RMS zu hoch
  }
}
```

**UI-Logik ist damit simpel:** `result` fehlt → Fortschrittsbalken/Prozent
aus `coverageX`/`coverageY` zeigen. `result` erscheint → Ergebnis-Ansicht
zeigen (RMS, Samples, ggf. Warnung), fertig.

### `ActiveCalibrationInfo` — für eine dauerhafte "Zuletzt kalibriert am ..."-Anzeige

`CalibrationProgress` gehört zu *einer* Session und ist bei jedem neuen
`StartCalibration` wieder leer. Für eine feste Anzeige im Settings-Menü (auch
ohne dass gerade jemand kalibriert) diesen Knoten lesen — er wird beim
Serverstart und nach jeder erfolgreichen Kalibrierung aktualisiert und bleibt
sonst unverändert stehen:

```jsonc
{
  "placeholder": false,
  "frameId": "cam_flange",
  "calibrationId": "cam_flange@2026-09-22T10:00:00+00:00",
  "createdAt": "2026-09-22T10:00:00+00:00",
  "rms": 0.2945,
  "samples": 21,
  "board": {"type": "chessboard", "cols": 7, "rows": 9, "squareSizeM": 0.022},
  "path": "data/calibration/cam_flange.json"
}
```

`placeholder: true` heißt: noch nie echt kalibriert, Posen sind nicht
maßhaltig — `rms`/`createdAt` sind dann `null`. Eine erfolgreiche
Kalibrierung wirkt **sofort** hier und in den echten Posen, ganz ohne
Server-Neustart.

## 4. Wichtige Fallstricke

1. **`error`-Feld in `result` ist ein Zahlencode, kein Bool.** `0` = Erfolg.
   Fehlercodes: Abschnitt 5 in `vision-server-interface.md` — die relevanten
   hier sind `1` (`INVALID_STATE`), `3` (`BUSY`), `5` (`DETECTION_FAILED`).

2. **`result.warning` heißt nicht "Fehler".** Die Datei ist trotzdem
   geschrieben. Abdeckung allein sagt nichts über die tatsächliche
   Genauigkeit — ein Board, das nie gekippt wurde, kann trotz 90 % Abdeckung
   einen RMS von >2 px ergeben (selbst erlebt). Die Warnung erscheint, wenn
   RMS > 0,5 px, und sollte im UI sichtbar sein (nicht nur console.log),
   idealerweise mit einem Hinweis "Board stärker kippen und neu versuchen".

3. **`BUSY` (Error=3) bei `StartCalibration` heißt nicht zwangsläufig
   "ein Job läuft".** Es kann auch eine **hängengebliebene Session** eines
   Clients sein, der z. B. den Tab geschlossen hat, ohne
   `FinishCalibration`/`AbortCalibration` aufzurufen — die Session lebt im
   Server-Prozess, nicht im Client. Sinnvolles Frontend-Verhalten: bei
   `BUSY` einmal `AbortCalibration` aufrufen und `StartCalibration` erneut
   versuchen (siehe `src/vision_server/tools/calibration_client.py`, Funktion
   `_start_session`, für die Referenzimplementierung dieses Patterns).

4. **`CaptureCalibrationSample` mit `Error=5` ist kein Fehlerzustand.**
   Heißt nur "in diesem einen Frame kein Board gefunden" — die Session läuft
   normal weiter, kein Reset nötig. Einfach nochmal auslösen.

5. **`FinishCalibration`/`result` mit `Error=5` ist dagegen ein echter
   Fehlschlag, auch bei guter Abdeckung und vielen Samples.** Die
   Berechnung selbst (`cv2.calibrateCamera`) kann numerisch scheitern, wenn
   das Board zwar über das ganze Bild verteilt, aber nie gekippt/im Abstand
   variiert wurde — die 2D-Bildabdeckung sagt darüber nichts aus. `message`
   im Ergebnis nennt den Grund; die Session ist dann beendet, ohne Ergebnis
   — einfach `StartCalibration` neu aufrufen und diesmal das Board deutlich
   kippen (±30°) und im Abstand variieren.

6. **Layer 1 (Deckenkamera) ist über cols/rows/Größe auf dieselbe
   Board-Geometrie wie Layer 2 eingestellt, aber (Stand jetzt) noch nicht
   final vermessen/fest montiert** (siehe Abschnitt 12.7 in
   `vision-server-interface.md`). Für einen ersten Integrationstest ist
   Layer 2 (Hand-Pi, `cam_flange`) der verlässlichere Kandidat.

7. **Mutual Exclusion:** Während eine Kalibrier-Session läuft, lehnt der
   normale Erkennungs-Job (`StartSingleJob`/`StartContinuous`) mit `BUSY` ab,
   und umgekehrt. Beide teilen sich dieselbe Kamera. Falls dein Frontend
   auch den normalen Job-Button zeigt: während `CalibrationProgress.running
   === true` deaktivieren (oder den Fehler einfach anzeigen).

## 5. Referenzimplementierungen (lesen, nicht übersetzen — das Protokoll zählt, nicht die Sprache)

Alle unter `src/vision_server/tools/`, laufen direkt gegen einen echten
Server, Python + `asyncua`, aber der **Aufruf-Ablauf** ist 1:1 das, was das
Frontend nachbilden muss:

- `calibration_client.py` — Start, Fortschritt pollen, automatischen
  Abschluss erkennen (`progress["result"]`), Retry bei `BUSY`.
- `stream_viewer.py` — Bild anzeigen, `CaptureCalibrationSample` auslösen,
  Ergebnis inkl. `warning` ausgeben.
- `diagnose_board.py` — nicht Teil des normalen Ablaufs, nur zur Fehlersuche.

## 6. Stand / was noch nicht existiert

- Kein eigener State-Machine-Zustand für "Kalibrierung läuft" — der Automat
  bleibt in `Ready`, die Sperre läuft rein über `BUSY` (Abschnitt 12.7).
- Kein Event für "Kalibrierung fertig" — nur der Progress-Knoten. Falls das
  Frontend eventgetrieben statt pollend arbeiten will: `CalibrationProgress`
  ganz normal über die bestehende `subscribeNode`-Infrastruktur abonnieren
  (wie `LatestCameraFrame`), kein separates Event-System dafür.

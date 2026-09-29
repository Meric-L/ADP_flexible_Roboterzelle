# Frontend-Integration: Kalibrierung + Livestream

Schnellstart-Briefing für einen Agenten/Entwickler, der die
Frontend-Seite von "Kalibrieren im Settings-Menü" (Board bewegen, Live-Bild
sehen, Aufnahmen auslösen, auswerten lassen, Ergebnis bekommen) umsetzt.
Ersetzt keine der bestehenden Dokus, sondern destilliert genau das, was für
**diese** Aufgabe gebraucht wird, und verlinkt für Details.

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

**Seit 2026-09-28 sind Aufnahme und Auswertung entkoppelt** (live an der
Deckenkamera gefunden: die Ecken-Erkennung auf dem vollen 12-MP-Frame konnte
auf der Pi-Hardware so lange dauern — bis hin zu einem echten
Hängenbleiben —, dass sie nicht mehr in den OPC-UA-Antwortpfad einer
einzelnen Aufnahme passte). `CaptureCalibrationSample` merkt sich jetzt nur
noch das Bild, ohne zu prüfen, ob das Board sichtbar ist. Die eigentliche
Auswertung (Ecken-Erkennung je Bild + Kamerakalibrierung) läuft erst nach
`FinishCalibration`, **im Hintergrund** — `FinishCalibration` ist damit kein
optionaler Schritt mehr, sondern **immer nötig**, um überhaupt ein Ergebnis
zu bekommen, und sein `Summary`/`Error`-Rückgabewert ist **nicht** das
Ergebnis, sondern nur die Bestätigung "Auswertung gestartet". Das Ergebnis
kommt ausschließlich über `CalibrationProgress`.

```
Frontend                                          Vision-Server
   |  1. StartCalibration() -----------------------> Error: Int32
   |     (BUSY=3, wenn Job laeuft ODER schon         (0 = OK, Session laeuft)
   |      eine Session/Auswertung offen ist)
   |
   |  2. LatestCameraFrame + CameraStreamMode="calibration" anzeigen
   |     (Board-Ecken auf dem kleinen Vorschaubild sind eingezeichnet --
   |      reine Positionierungshilfe, sagt nichts darueber, welche
   |      Aufnahmen tatsaechlich uebernommen wurden)
   |
   |  3. CaptureCalibrationSample() -----------------> Error: Int32
   |     bei Klick auf "Aufnahme" o.ae.               (0 = uebernommen,
   |     (kein Auto-Capture -- der Nutzer               1 = keine Session/
   |      entscheidet, wann er ausloest;                    kein Kamerabild)
   |      KEINE Pruefung, ob das Board sichtbar
   |      ist -- das stellt sich erst in Schritt 5
   |      heraus)
   |     ... wiederholen, Board dabei bewegen/kippen ...
   |
   |  4. CalibrationProgress.samples zeigt die Anzahl live
   |
   |  5. FinishCalibration() ------------------------> Summary: String(JSON)
   |     Beendet die Aufnahme-Phase, stoesst die       Error: Int32
   |     Auswertung NUR AN (kehrt sofort zurueck --
   |     Summary ist noch NICHT das Ergebnis!).
   |     (AbortCalibration() stattdessen, wenn gar
   |      nichts ausgewertet/gespeichert werden soll.)
   |
   |  6. CalibrationProgress weiter beobachten (subscriben oder pollen)
   |     -> "processing": true, waehrend die Auswertung im Hintergrund
   |        laeuft (der Livestream pausiert dabei serverseitig kurz)
   |     -> sobald "result" auftaucht: FERTIG (Erfolg oder Fehlschlag,
   |        result.error unterscheidet)
```

### Die Knoten/Methoden im Einzelnen

| Name | NodeId | Typ | Bedeutung |
| --- | --- | --- | --- |
| `StartCalibration` | `ns=<vision>;s=VisionMachine.StartCalibration` | Methode, kein Input, `Error: Int32` | Setzt Aufnahmen zurück, startet Session |
| `CaptureCalibrationSample` | `ns=<vision>;s=VisionMachine.CaptureCalibrationSample` | Methode, kein Input, `Error: Int32` | Merkt sich das aktuelle Bild (keine Erkennung) |
| `FinishCalibration` | `ns=<vision>;s=VisionMachine.FinishCalibration` | Methode, kein Input, `Summary: String, Error: Int32` | **Pflichtschritt**: beendet die Aufnahme, stößt die Auswertung im Hintergrund an — `Summary` ist nicht das Ergebnis, siehe oben |
| `AbortCalibration` | `ns=<vision>;s=VisionMachine.AbortCalibration` | Methode, kein Input, `Error: Int32` | Verwirft ohne auszuwerten/zu speichern |
| `CalibrationProgress` | `ns=<vision>;s=VisionMachine.CalibrationProgress` | String (JSON), **nur lesen** | Live-Fortschritt *einer Session*, einzige Quelle für das Ergebnis, siehe unten |
| `ActiveCalibrationInfo` | `ns=<vision>;s=VisionMachine.ActiveCalibrationInfo` | String (JSON), **nur lesen** | Metadaten der **gerade aktiven** Kalibrierung — bleibt stehen, unabhängig von einer laufenden Session, siehe unten |
| `LatestCameraFrame` | `ns=<vision>;s=VisionMachine.LatestCameraFrame` | String (Base64-JPEG), nur lesen | Bild fürs `<img>`/Canvas — liefert während der Auswertung nur noch das letzte Bild vor der Pause |
| `CameraStreamMode` | `ns=<vision>;s=VisionMachine.CameraStreamMode` | String, **schreibbar** | `"off"` \| `"apriltag"` \| `"calibration"` — vor/bei Kalibrierstart auf `"calibration"` setzen |

**Kein Board-Parameter wird je vom Frontend gesendet.** Board-Typ,
Maße, cols/rows stehen serverseitig fest (pro Pi konfiguriert). Das
Frontend startet/löst aus/liest, sonst nichts.

### `CalibrationProgress` — das JSON, das du pollst/abonnierst

```jsonc
// waehrend die Aufnahme laeuft:
{"running": true, "processing": false, "samples": 12, "minSamples": 15}

// FinishCalibration wurde aufgerufen, Auswertung laeuft im Hintergrund:
{"running": false, "processing": true, "samples": 18, "minSamples": 15}

// fertig, Erfolg:
{
  "running": false, "processing": false, "samples": 18, "minSamples": 15,
  "result": {
    "error": 0, "rms": 0.31, "samples": 16,
    "coverageX": 0.84, "coverageY": 0.9,
    "path": "data/calibration/cam_flange.json"
    // "warning": "..." -- siehe unten, nur wenn RMS zu hoch
  }
}

// fertig, Fehlschlag (z. B. zu wenige der Aufnahmen zeigten das Board):
{
  "running": false, "processing": false, "samples": 18, "minSamples": 15,
  "result": {"error": 5, "message": "Zu wenige verwertbare Aufnahmen: 2"}
}
```

Zwei Zahlen können auseinanderfallen: `samples` im Wurzelobjekt ist die
Anzahl **aufgenommener** Bilder, `result.samples` die Anzahl Bilder, auf
denen das Board bei der Auswertung tatsächlich **gefunden** wurde (kann
kleiner sein — unscharfe Aufnahmen, Board nicht im Bildausschnitt).

**UI-Logik:** `processing` zeigt "wird ausgewertet, bitte warten" (kann ein
paar Sekunden bis über eine Minute dauern, je nach Aufnahmenzahl und
Pi-Hardware). `result` erscheint → Ergebnis-Ansicht zeigen: `result.error`
prüfen (0 = Erfolg, sonst `result.message`), bei Erfolg RMS/Samples/ggf.
Warnung anzeigen.

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
   Der `Error`-Rückgabewert von `FinishCalibration` selbst ist ein anderer,
   engerer Fall: er sagt nur, ob die Auswertung angestoßen wurde (siehe
   Fallstrick 4) — das eigentliche Ergebnis inkl. seines eigenen `error`
   steht in `CalibrationProgress["result"]`.

2. **`result.warning` heißt nicht "Fehler".** Die Datei ist trotzdem
   geschrieben. Abdeckung allein sagt nichts über die tatsächliche
   Genauigkeit — ein Board, das nie gekippt wurde, kann trotz 90 % Abdeckung
   einen RMS von >2 px ergeben (selbst erlebt). Die Warnung erscheint, wenn
   RMS > 0,5 px, und sollte im UI sichtbar sein (nicht nur console.log),
   idealerweise mit einem Hinweis "Board stärker kippen und neu versuchen".
   **Anders als vorher gibt es dafür keine Live-Anzeige mehr während der
   Aufnahme** — die Abdeckung ist erst nach `FinishCalibration` bekannt, das
   Frontend kann den Operator also nicht mehr vorab warnen, nur hinterher.

3. **`BUSY` (Error=3) bei `StartCalibration` heißt nicht zwangsläufig
   "ein Job läuft".** Es kann auch eine **hängengebliebene Session** eines
   Clients sein, der z. B. den Tab geschlossen hat, ohne
   `FinishCalibration`/`AbortCalibration` aufzurufen — die Session lebt im
   Server-Prozess, nicht im Client. Es kann aber auch bedeuten, dass noch
   eine **Auswertung aus einem vorherigen `FinishCalibration` läuft**
   (`CalibrationProgress.processing === true`) — dagegen hilft kein
   `AbortCalibration` (das lehnt dann selbst mit `INVALID_STATE` ab), nur
   abwarten, bis `processing` wieder `false` wird. Sinnvolles
   Frontend-Verhalten: erst `CalibrationProgress.processing` prüfen; ist es
   `false`, bei `BUSY` einmal `AbortCalibration` aufrufen und
   `StartCalibration` erneut versuchen (siehe
   `src/vision_server/tools/calibration_client.py`, Funktion
   `_start_session`, für die Referenzimplementierung dieses Patterns).

4. **`FinishCalibration`s eigener `Error`-Rückgabewert ist NICHT das
   Kalibrierergebnis.** `0` heißt nur "Auswertung wurde angestoßen", `5`
   heißt "weniger als 3 Aufnahmen gemacht, gar nichts angestoßen". Ein
   Frontend, das hier schon "Erfolg" anzeigt, zeigt dem Operator etwas
   Falsches — das eigentliche Ergebnis (inkl. eines möglichen Fehlschlags
   *während* der Auswertung, siehe Fallstrick 5) kommt erst über
   `CalibrationProgress["result"]`.

5. **`CalibrationProgress["result"]` mit `error=5` kann trotz genug
   Aufnahmen auftreten — auf zwei verschiedene Arten.** Entweder zeigten zu
   wenige der aufgenommenen Bilder überhaupt ein Board (`result.samples` <
   3 — vergleiche mit dem `samples`-Feld im Wurzelobjekt, das die Anzahl
   *aufgenommener*, nicht *erkannter* Bilder zeigt), oder die Berechnung
   selbst (`cv2.calibrateCamera`) scheitert numerisch, wenn das Board zwar
   über das ganze Bild verteilt, aber nie gekippt/im Abstand variiert wurde
   — die reine Anzahl sagt darüber nichts aus. `result.message` nennt den
   Grund; einfach `StartCalibration` neu aufrufen und diesmal das Board
   deutlich kippen (±30°) und im Abstand variieren.

6. **Layer 1 (Deckenkamera) ist über cols/rows/Größe auf dieselbe
   Board-Geometrie wie Layer 2 eingestellt, aber (Stand jetzt) noch nicht
   final vermessen/fest montiert** (siehe Abschnitt 12.8 in
   `vision-server-interface.md`). Für einen ersten Integrationstest ist
   Layer 2 (Hand-Pi, `cam_flange`) der verlässlichere Kandidat.

7. **Mutual Exclusion:** Während eine Kalibrier-Session *oder* eine
   Hintergrund-Auswertung läuft, lehnt der normale Erkennungs-Job
   (`StartSingleJob`/`StartContinuous`) mit `BUSY` ab, und umgekehrt (`Start-
   Calibration` lehnt ab, solange ein Job läuft). Beide teilen sich dieselbe
   Kamera. Falls dein Frontend auch den normalen Job-Button zeigt: während
   `CalibrationProgress.running === true` **oder**
   `CalibrationProgress.processing === true` deaktivieren (oder den Fehler
   einfach anzeigen).

## 5. Referenzimplementierungen (lesen, nicht übersetzen — das Protokoll zählt, nicht die Sprache)

Alle unter `src/vision_server/tools/`, laufen direkt gegen einen echten
Server, Python + `asyncua`, aber der **Aufruf-Ablauf** ist 1:1 das, was das
Frontend nachbilden muss:

- `calibration_client.py` — Start, Fortschritt pollen, `FinishCalibration`
  aufrufen und danach weiter auf `progress["result"]` warten (Auswertung
  läuft im Hintergrund), Retry bei `BUSY`.
- `stream_viewer.py` — Bild anzeigen, `CaptureCalibrationSample` auslösen.
- `diagnose_board.py` — nicht Teil des normalen Ablaufs, nur zur Fehlersuche.

## 6. Stand / was noch nicht existiert

- Kein eigener State-Machine-Zustand für "Kalibrierung läuft" — der Automat
  bleibt in `Ready`, die Sperre läuft rein über `BUSY` (Abschnitt 12.7).
- Kein Event für "Kalibrierung fertig" — nur der Progress-Knoten. Falls das
  Frontend eventgetrieben statt pollend arbeiten will: `CalibrationProgress`
  ganz normal über die bestehende `subscribeNode`-Infrastruktur abonnieren
  (wie `LatestCameraFrame`), kein separates Event-System dafür.

## 7. Linsenmodell (seit 29.09.2026)

Die Deckenkamera (Layer 1) wird jetzt mit dem Fisheye-Modell kalibriert, die
Handkamera (Layer 2) weiter mit dem Standardmodell. Das Frontend sendet dafür
**nichts** — das Modell ist serverseitig pro Pi festgelegt. Neu sind nur
zusätzliche, rein informative Felder, jeweils `"fisheye"` oder `"pinhole"`:

- `CalibrationProgress.model`: Modell der laufenden Session — schon während
  der Aufnahme, also vor dem Ergebnis bekannt
- `CalibrationProgress.result.model`: in jedem Ergebnis, auch bei Fehlschlag
- `ActiveCalibrationInfo.model`: Modell der gerade aktiven Kalibrierung —
  die Quelle für die Ruheansicht, weil `CalibrationProgress` ohne Session
  kein `model` trägt

Anzeige-Vorschlag: „Linsenmodell: Fisheye (Kannala-Brandt)“ bzw.
„Standard (Brown-Conrady)“ — in der Aufnahme-Ansicht, neben dem RMS im
Ergebnis und in der Ruheansicht. Bei Fehlschlag z. B. „Fisheye-Kalibrierung
fehlgeschlagen: <message>“. Der RMS-Wert bleibt in Pixeln und ist zwischen
beiden Modellen vergleichbar.
Details: `vision-server-interface.md` Abschnitt 12.10.

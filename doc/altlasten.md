# Altlasten — Stand und Abbaubedingungen

Mitlaufende Liste der Dinge, die wir im Laufe des Projekts eingebaut haben und die
**nicht zur Lokalisierung gehören**: Demo-Aufbauten, Testinstrumente, Platzhalter und
Deployment-Behelfe. Zweck ist, dass nichts davon unbemerkt produktiv wird.

**Es ist bislang nichts entfernt worden.** Jeder Eintrag nennt die Bedingung, unter der
er wegfallen kann. Erst wenn die erfüllt ist, wird gelöscht — und dann auch der Eintrag
hier gestrichen.

Zwei Repos sind betroffen:

- **Zelle** = `ADP_flexible_Roboterzelle` (dieses Repo), Branch `feature/vision-server`
- **WSC** = `webskillcomposition` (Uni-Repo), Branch `Ungetestet`

Stand: 2026-09-10. Tests: `PYTHONPATH=src python3 -m unittest discover -s tests -t .`

---

## A. CPU-Temperatur-Demo

Der ursprüngliche Aufbau, mit dem die OPC-UA-Strecke überhaupt erst zum Laufen gebracht
wurde. Fachlich hat nichts davon mit der Zelle zu tun.

| # | Was | Wo | Warum es da ist | Abbaubedingung |
|---|---|---|---|---|
| A1 | **`2:VisionSystem`** — zweite, leere `VisionSystemType`-Instanz im Adressraum | Zelle: `src/OPCUA/server.py:84-91` | War der erste 40100-Versuch; trägt heute nur noch A2 | Wenn A2 weg ist. **Blockiert bis dahin nichts, ist aber aktiv gefährlich** — siehe Hinweis unten |
| A2 | **`CpuTemperatureResult`** — CPU-Temperatur als `Double` im `ResultContent` eines Vision-Ergebnisknotens | Zelle: `src/OPCUA/server.py:97-105`, geschrieben in `:118` | Bequemer Ort, um einen Live-Wert zu haben, bevor `RaspiDevice` existierte | `RaspiDevice/CpuTemperature` (`ns=2;i=2`) ist der einzige Temperaturweg. Der existiert bereits — es hängt nur noch B1/B4 daran |
| A3 | **`RaspiDevice/Counter`** — 1-Hz-Zähler | Zelle: `src/OPCUA/server.py:72`, `:116` | Sichtprüfung „Server lebt" in UaExpert | Sobald der Liveness-Check im Backend als Beleg reicht. Der Ersatz als Referenzsignal ist da: der Loop-Lag-Watchdog in `runner.py` loggt Blockaden über 750 ms |
| A4 | **`RaspiDevice/Setpoint`** — beschreibbarer `Double` | Zelle: `src/OPCUA/server.py:73-75` | Zielknoten des Temperatur-Relays B2; sonst ohne Funktion | Mit B2 |
| A5 | **1-Hz-Endlosschleife** im Server | Zelle: `src/OPCUA/server.py:111-119` | Hält A2/A3 aktuell | Mit A2 und A3. Achtung: die Schleife läuft im selben Event-Loop wie das Vision-System |
| A6 | **`print_setpoint.py`** — Debug-Client | Zelle: `src/OPCUA/print_setpoint.py` | Zum Mitlesen des Sollwerts während der Relay-Entwicklung | Mit B2. Das Muster ist als Vorlage für einen Event-Loop-Latenztest brauchbar, vorher übernehmen |

> **Hinweis zu A1:** Solange die Altlast-Instanz existiert, liegen **zwei**
> `VisionSystemType`-Instanzen im Adressraum. Ein Client, der per Typ sucht statt die
> feste NodeId `ns=4;s=VisionMachine` zu nehmen, findet beide. Genau das hat schon einmal
> zugeschlagen: ein Aufruf auf `ns=2;i=150` (die **nicht verlinkte** `StartSingleJob` der
> Altlast) beantwortet der Server mit `BadNothingToDo`. Siehe auch
> [`vision-server-interface.md`](vision-server-interface.md) §7.4.

---

## B. Pi-spezifische Einbauten im Uni-Backend/Frontend

Alles Folgende haben wir ins WSC-Repo eingebracht. Es ist auf unsere zwei Pis
festverdrahtet und gehört so nicht in ein Uni-Repo, das andere Gruppen weiterbenutzen.

| # | Was | Wo | Warum es da ist | Abbaubedingung |
|---|---|---|---|---|
| B1 | **Hartcodierte Pi-Adressen** `10.10.38.104` / `.109` und Relay-Knoten-IDs | WSC: `backend/src/backend/config/pi_relay.py:3-9`, `frontend/src/features/opcua-server/config/piServers.ts:1-9` | Schnellster Weg zu einer laufenden Verbindung | Server-Auswahl über die vorhandene `ConnectOpcUa`-Eingabe oder Konfiguration statt Konstanten |
| B2 | **CPU-Temperatur-Relay Pi1↔Pi2** — kopiert 1×/s `ns=2;i=2` → `ns=2;i=4` der jeweils anderen Seite | WSC: `application_service.py:297-384` (`_relay_temperature`, `_ensure_pi_temperature_relays`), `runtime_registry.py:19-28` | Nachweis, dass das Backend gleichzeitig lesen und schreiben kann | Ersatzlos. Nachweis ist erbracht, echte Nutzlast gibt es keine |
| B3 | **„First Layer" / „Second Layer" als Label zweier fester URLs** | WSC: `piServers.ts:4-7`, `ServerManager.tsx:5`, `:100-103` | Zwei Verbindungspunkte in der UI unterscheidbar machen | Wenn die Layer-Zuordnung aus den Serverdaten kommt statt aus der URL |
| B4 | **„Test: First Layer CPU-Temperatur"** — Live-Anzeige im Autolocate-Popup | WSC: `AutolocateModulesModal.tsx:128-149`, `:223-227` | Beweis, dass eine Node-Subscription aus dem Popup heraus funktioniert | Ersatzlos, sobald das Popup echte Modulpositionen zeigt |
| B5 | **`MOCK_MODULES`** — drei erfundene Roboter mit Position 0/0/0 | WSC: `AutolocateModulesModal.tsx:21-36`, `:123` | Füllt den dritten Bildschirm des Popups | Wenn `detections` aus dem Vision-Payload dort landen. Im Code bereits als `TODO` markiert |
| B6 | **`legacy`-Zweig in `resolveVisionBinding`** — NodeIds des alten Smoke-Tests | WSC: `piServers.ts:67-76` | Übergangsphase, als noch nicht beide Pis den VisionMachine-Server hatten | Wenn beide Pis dauerhaft auf dem neuen Server sind und A1 entfernt ist. Der `unknown`-Zweig muss bleiben |
| B7 | **Deployment-Behelf** `if os.getenv("HOST"): mount StaticFiles("./www")` und `host="0.0.0.0"` | WSC: `backend/src/backend/app.py:18-19`, `:30` | Damit das gebaute Frontend mit ausgeliefert wird | Nicht von uns eingeführt — bei einer Rückgabe ans Uni-Repo mit den Betreuern klären |
| B8 | **Branchname `Ungetestet`** | WSC | Ehrlich benannter Arbeitsbranch | Beim Merge nach `dev`. Der Name sagt inzwischen weniger als er soll — Backend und Vision-Strecke sind gegen beide Pis verifiziert, das Frontend hat Typecheck und Unit-Tests |

---

## C. Vision-Server: nicht-fachliche Platzhalter

Im Vision-Server selbst, aber unabhängig davon, wie gut die Erkennung wird.

| # | Was | Wo | Warum es da ist | Abbaubedingung |
|---|---|---|---|---|
| ~~C1~~ | ~~`IsSimulated = True` fest verdrahtet~~ | — | — | **Erledigt**: kommt jetzt von der Erkennungsquelle (`DetectionSource.is_simulated`) |
| C2 | **`IsPartial = False`** fest verdrahtet | Zelle: `src/vision_server/events.py:69`, `result_management.py:91` | Es gibt nur Vollergebnisse | Erst relevant, falls je Teilergebnisse gesendet werden |
| ~~C3~~ | ~~`vision_system_id` auf beiden Pis identisch~~ | — | — | **Erledigt**: `vision_identity()` in `src/OPCUA/server.py` (Env → Hostname-Abbildung → Fallback). Die Hostnamen in `PI_IDENTITIES` sind noch geraten und auf den Pis zu prüfen |
| C4 | **`configuration_id`** wird noch von keiner Quelle gesetzt | Zelle: `src/vision_server/config.py` | Aus dem Entwurf übrig | Weg ist gebaut (`DetectionSource.configuration_id` → `configurationId` und `InternalConfigurationId`); die AprilTag-Quelle muss ihn mit der Kalibrieridentität füllen |
| C5 | **Nodeset-XML unter `src/OPCUA/`** statt beim Paket, das es braucht | Zelle: `src/OPCUA/Opc.Ua.MachineVision.NodeSet2.xml` (786 KB) | Lag da, bevor `vision_server/` existierte | Nach `src/vision_server/nodesets/` verschieben, Pfadkonstanten anpassen |
| C6 | **`caputure.py`** — ein **QR-Scanner** im AprilTag-Ordner, Tippfehler im Dateinamen, von nichts importiert | Zelle: `src/apriltag/caputure.py` | Erster Kameraversuch | Ersatzlos, sobald die Kamera-Abstraktion steht. Vorher den picamera2-Aufruf daraus übernehmen — es ist die einzige Stelle im Repo, die die Pi-Kamera überhaupt anspricht |

---

## D. Umgebung, Build und Repo-Hygiene

Keine Altlasten im engeren Sinn, aber Fallen, die uns bereits Zeit gekostet haben.

| # | Was | Wo | Problem |
|---|---|---|---|
| D1 | **`.gitignore` schluckt ganze Dateitypen** — `*.txt`, `*.sh`, `*.bat`, `concept/`, `data/`, `hardware/` | Zelle: `.gitignore` | Bereits getrackte Dateien laufen weiter, **neue** `.sh`/`.txt` und alles Neue unter `concept/` sind still unsichtbar für `git add`. Betrifft jedes künftige Setup- oder Deploy-Skript |
| D2 | **Kaputtes `.venv/`** — kein `bin/`, kein `pyvenv.cfg`, `asyncua` fehlt, dafür OpenCV **5.0.0.93** | Zelle: `.venv/` | Nicht aktivierbar. OpenCV 5 hat eine andere `aruco`-API als der Pi — lokal entwickelter Code läuft dort nicht |
| D3 | **`requirements.txt` bildet die Realität nicht ab** | Zelle: `requirements.txt` | `picamera2` fehlt (nicht pip-installierbar, kommt per apt), kein Upper Bound auf OpenCV, `pupil-apriltags` fällt mit dem Umstieg auf `cv2.aruco` weg |
| D4 | **`setup.sh` / `setup.bat` sind kein Projekt-Setup** | Zelle | Richten nur das ShareLaTeX-Remote und zwei Git-Aliase ein. Der Name legt etwas anderes nahe; ein echtes Setup-Skript existiert nicht |
| D5 | **systemd-Unit und venv sind nirgends versioniert** | — | `opcua-server.service`, der Repo-Pfad auf dem Pi, die Python-Version und die venv-Erzeugung stehen in keinem Repo. Neuaufsetzen eines Pi ist derzeit undokumentiert |
| D6 | **Node 18 reicht für das WSC-Frontend nicht** | WSC: `frontend/package.json` | `vite`/`vitest` verlangen `^20.19.0 \|\| >=22.12.0`. Unter Node 18 startet vitest nicht, und `npm install` überspringt **stillschweigend** das native `@rolldown/binding-linux-x64-gnu` — man bekommt ein kaputtes `node_modules` ohne Fehlermeldung |
| D7 | **`tests/test_asyncua_discovery.py` bricht beim Collect ab** | WSC: Backend | Importiert `discover_variables`, das es in `asyncua_discovery.py` nicht mehr gibt. `uv run pytest` läuft dadurch gar nicht durch. Nicht von uns verursacht |
| D8 | **Ein fehlschlagender Frontend-Test** | WSC: `entities/robot/model/store.test.ts:188` | Vorbestehend auf dem Branch, reproduziert sich ohne unsere Änderungen. Solange er rot ist, taugt `npm test` nicht als Signal |

---

## Ausdrücklich **nicht** in dieser Liste

Das Folgende sind Platzhalter der Lokalisierung selbst, also die eigentliche Arbeit —
keine Altlasten:

- `detection/hello_world.py` und das Profil `hello_world`. Es bleibt als kamerafreier
  Smoke-Test dauerhaft nützlich, damit das Backend-Team ohne Hardware testen kann.
- `frameId = "world"` in `payload.py:11`, obwohl noch kein Weltsystem existiert.
- Posen fest auf `[0,0,0]` / `[0,0,0,1]`.
- Dass `detect_apriltags.py` die Verzerrungskoeffizienten nicht anwendet.

Diese Punkte stehen im Umsetzungsplan für die AprilTag-Auswertung, nicht hier.

---

## Reihenfolge, wenn abgebaut wird

Die Einträge hängen zusammen. Sinnvolle Kette:

1. **B4** (Temperaturanzeige im Popup) und **B2** (Relay) entfernen — danach liest
   nichts mehr die Temperatur über den Vision-Baum.
2. **A2** (`CpuTemperatureResult`) entfernen, damit **A1** (`2:VisionSystem`) leer ist.
3. **A1** entfernen. Erst danach ist der Adressraum eindeutig und **B6**
   (`legacy`-Zweig) kann fallen.
4. **A4**, **A5**, **A3**, **A6** — der Rest der Demo.
5. **C3** vor dem Zwei-Pi-Betrieb, unabhängig vom Rest.

Der frühere Vorbehalt zu A3/A5 ist erledigt: der Loop-Lag-Watchdog in
`src/vision_server/runner.py` meldet Blockaden des gemeinsamen Event-Loops, `Counter`
wird als Referenzsignal nicht mehr gebraucht.

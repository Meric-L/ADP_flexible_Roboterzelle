# Altlasten — Stand und Abbaubedingungen

Mitlaufende Liste der Dinge, die wir im Laufe des Projekts eingebaut haben und die
**nicht zur Lokalisierung gehören**: Demo-Aufbauten, Testinstrumente, Platzhalter und
Deployment-Behelfe. Zweck ist, dass nichts davon unbemerkt produktiv wird.

Jeder Eintrag nennt die Bedingung, unter der er wegfallen kann. Erst wenn die erfüllt
ist, wird gelöscht — und dann auch der Eintrag hier gestrichen.

**Am 21.09.2026 abgebaut:** die vollständige CPU-Temperatur-Demo (A1–A6) samt ihrer
Gegenstücke im Uni-Repo (B2, B4) und dem `legacy`-Zweig, der nur ihretwegen existierte
(B6). Siehe [`arbeitsplaene/altlasten-abbau-part10-fassade.md`](arbeitsplaene/altlasten-abbau-part10-fassade.md).

Zwei Repos sind betroffen:

- **Zelle** = `ADP_flexible_Roboterzelle` (dieses Repo), Branch `feature/vision-server`
- **WSC** = `webskillcomposition` (Uni-Repo), Branch `Ungetestet`

Stand: 2026-09-21. Tests: `PYTHONPATH=src python3 -m unittest discover -s tests -t .`

---

## A. CPU-Temperatur-Demo — **vollständig entfernt (21.09.2026)**

Der ursprüngliche Aufbau, mit dem die OPC-UA-Strecke überhaupt erst zum Laufen gebracht
wurde. Fachlich hatte nichts davon mit der Zelle zu tun. Alles weg.

| # | Was | Erledigt |
|---|---|---|
| ~~A1~~ | ~~`2:VisionSystem` — zweite, leere `VisionSystemType`-Instanz~~ | Der Adressraum enthält jetzt genau **eine** Instanz; festgehalten in `tests/test_part10_fassade.py` |
| ~~A2~~ | ~~`CpuTemperatureResult`~~ | Mit A1 entfallen |
| ~~A3~~ | ~~`RaspiDevice/Counter`~~ | Ersatz ist der Loop-Lag-Watchdog in `runner.py` |
| ~~A4~~ | ~~`RaspiDevice/Setpoint`~~ | Mit B2 entfallen |
| ~~A5~~ | ~~1-Hz-Endlosschleife im Server~~ | `main()` wartet jetzt nur noch auf das Stopp-Signal |
| ~~A6~~ | ~~`print_setpoint.py`~~ | Gelöscht |

Mit `RaspiDevice` ist auch sein Namensraum `http://launch-rm.de/raspi` gefallen. **Alle
Namespace-Indizes sind dadurch um eins nach unten gerückt** — der Vision-Namespace liegt
jetzt auf ns=6. Wer `get_namespace_index(uri)` benutzt, merkt nichts; wer einen Index
fest verdrahtet hat, greift ins Leere.

`VisionMachine` hängt seitdem im Machinery-Standardordner `Objects/Machines` statt
direkt unter `Objects`. Grund war nicht Technik, sondern Lesbarkeit: unter `Objects`
standen vier gleichrangige Objekte, und in der Betreuung war unklar, welches der
Einstieg ist. Jetzt steht dort projektseitig nur noch `VisionProgram`.

---

## B. Pi-spezifische Einbauten im Uni-Backend/Frontend

Alles Folgende haben wir ins WSC-Repo eingebracht. Es ist auf unsere zwei Pis
festverdrahtet und gehört so nicht in ein Uni-Repo, das andere Gruppen weiterbenutzen.

| # | Was | Wo | Warum es da ist | Abbaubedingung |
|---|---|---|---|---|
| B1 | **Hartcodierte Pi-Adressen** `10.10.38.104` / `.109` und Relay-Knoten-IDs | WSC: `backend/src/backend/config/pi_relay.py:3-9`, `frontend/src/features/opcua-server/config/piServers.ts:1-9` | Schnellster Weg zu einer laufenden Verbindung | Server-Auswahl über die vorhandene `ConnectOpcUa`-Eingabe oder Konfiguration statt Konstanten |
| ~~B2~~ | ~~CPU-Temperatur-Relay Pi1↔Pi2~~ | — | — | **Erledigt** 21.09.2026: ersatzlos entfernt, samt `_relay_tasks` in `runtime_registry.py`. Die Zielknoten gibt es nicht mehr |
| B3 | **„First Layer" / „Second Layer" als Label zweier fester URLs** | WSC: `piServers.ts:4-7`, `ServerManager.tsx:5`, `:100-103` | Zwei Verbindungspunkte in der UI unterscheidbar machen | Wenn die Layer-Zuordnung aus den Serverdaten kommt statt aus der URL |
| ~~B4~~ | ~~„Test: First Layer CPU-Temperatur" im Autolocate-Popup~~ | — | — | **Erledigt** 21.09.2026: ersatzlos entfernt. Der Knoten, den es abonnierte, existiert nicht mehr |
| B5 | **`MOCK_MODULES`** — drei erfundene Roboter mit Position 0/0/0 | WSC: `AutolocateModulesModal.tsx:21-36`, `:123` | Füllt den dritten Bildschirm des Popups | Wenn `detections` aus dem Vision-Payload dort landen. Im Code bereits als `TODO` markiert |
| ~~B6~~ | ~~`legacy`-Zweig in `resolveVisionBinding`~~ | — | — | **Erledigt** 21.09.2026: mit A1 hinfällig. Der `unknown`-Zweig ist geblieben — er unterscheidet weiterhin „noch nichts bekannt" von „falscher Server" |
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
| ~~C4~~ | ~~`configuration_id` wird von keiner Quelle gesetzt~~ | — | — | **Erledigt**: `AprilTagDetectionSource` füllt ihn mit Tag-Familie, Kalibrier- und Tag-Map-Identität (`tagloc.identity`, ohne numpy und ohne cv2) |
| C5 | **Nodeset-XML unter `src/OPCUA/`** statt beim Paket, das es braucht | Zelle: `src/OPCUA/Opc.Ua.MachineVision.NodeSet2.xml` (786 KB) | Lag da, bevor `vision_server/` existierte | Nach `src/vision_server/nodesets/` verschieben, Pfadkonstanten anpassen |
| ~~C6~~ | ~~`caputure.py` — QR-Scanner im AprilTag-Ordner~~ | — | — | **Erledigt**: gelöscht. Die Pi-Kamera spricht jetzt `SharedCamera` an, für CLI-Aufrufe `tagloc.frames.PiCameraSource` |
| C7 | **Adresse des Discovery-Servers fest im Code** — `opc.tcp://10.10.38.27:4840/` | Zelle: `src/ua_lds.py` (`DEFAULT_LDS_URL`) | Ohne Registrierung dort nimmt der Aggregation-Server uns nicht auf, und die Zelle hat genau diesen einen LDS | Wenn die Adresse aus einer Konfigurationsdatei kommt. `OPCUA_LDS_URL` biegt sie bereits ohne Codeänderung um, `OPCUA_LDS_URL=""` schaltet ab — für einen Umzug der Zelle reicht das |

---

## D. Umgebung, Build und Repo-Hygiene

Keine Altlasten im engeren Sinn, aber Fallen, die uns bereits Zeit gekostet haben.

| # | Was | Wo | Problem |
|---|---|---|---|
| D1 | **`.gitignore` schluckt ganze Dateitypen** — `*.txt`, `*.sh`, `*.bat`, `concept/`, `data/`, `hardware/` | Zelle: `.gitignore` | Bereits getrackte Dateien laufen weiter, **neue** `.sh`/`.txt` und alles Neue unter `concept/` sind still unsichtbar für `git add`. Betrifft jedes künftige Setup- oder Deploy-Skript |
| ~~D2~~ | ~~Kaputtes `.venv/`~~ | — | **Erledigt**: neu angelegt aus `requirements.txt` (OpenCV 5.0.0, numpy 2.5.3, asyncua 2.0.1, pupil-apriltags). Die gesamte Suite läuft damit **ohne einen einzigen Skip**. Das defekte Verzeichnis liegt als `.venv.kaputt/` daneben und kann gelöscht werden. Offen bleibt: verifiziert ist nur OpenCV **5.0.0**; auf dem Pi läuft 4.x. `tagloc.detector`/`boards` haben dafür Laufzeitweichen, die dort aber noch niemand ausgeführt hat |
| ~~D3~~ | ~~`requirements.txt` bildet die Realität nicht ab~~ | — | **Teilweise erledigt**: die Datei erklärt jetzt, warum `picamera2` (Decken-Pi) per apt kommt (venv braucht `--system-site-packages`) und wozu `pupil-apriltags` noch dient — seit `tagloc.detector` ein optionales zweites Backend, kein Zwang mehr. `pyrealsense2` (Hand-Pi) ebenso bewusst nicht aufgenommen: auf dem Pi live verifiziert, dass Intel dafür keine ARM/aarch64-Wheels auf PyPI liefert — es muss dort separat installiert sein (bei uns lag es unter `/usr/local/lib/python3.13/dist-packages/`, ebenfalls per `--system-site-packages` sichtbar zu machen). Ein Upper Bound auf OpenCV fehlt weiterhin bewusst, weil die Laufzeitweichen 4.x und 5.x abdecken |
| D4 | **`setup.sh` / `setup.bat` sind kein Projekt-Setup** | Zelle | Richten nur das ShareLaTeX-Remote und zwei Git-Aliase ein. Der Name legt etwas anderes nahe; ein echtes Setup-Skript existiert nicht |
| D5 | **systemd-Unit und venv sind nirgends versioniert** | — | `opcua-server.service`, der Repo-Pfad auf dem Pi, die Python-Version und die venv-Erzeugung stehen in keinem Repo. Neuaufsetzen eines Pi ist derzeit undokumentiert |
| D6 | **Node 18 reicht für das WSC-Frontend nicht** | WSC: `frontend/package.json` | `vite`/`vitest` verlangen `^20.19.0 \|\| >=22.12.0`. Unter Node 18 startet vitest nicht, und `npm install` überspringt **stillschweigend** das native `@rolldown/binding-linux-x64-gnu` — man bekommt ein kaputtes `node_modules` ohne Fehlermeldung |
| D7 | **`tests/test_asyncua_discovery.py` bricht beim Collect ab** | WSC: Backend | Importiert `discover_variables`, das es in `asyncua_discovery.py` nicht mehr gibt. `uv run pytest` läuft dadurch gar nicht durch. Nicht von uns verursacht |
| D8 | **Ein fehlschlagender Frontend-Test** | WSC: `entities/robot/model/store.test.ts:188` | Vorbestehend auf dem Branch, reproduziert sich ohne unsere Änderungen. Solange er rot ist, taugt `npm test` nicht als Signal |
| ~~D9~~ | ~~Die Betreuer-Kurzanleitung zur Discovery stimmt nicht~~ | — | **Erledigt**: Die Anleitung sagte, der Aggregation-Server durchsuche mDNS selbst und eine Anmeldung sei nicht nötig — das hat einen Nachmittag gekostet. Sie ist am 21.09.2026 korrigiert worden und liegt jetzt **im Repo** unter `betreuer/OPC UA-mDNS-Kurzanleitung.md` (vorher nur lokal und untracked), damit andere Gruppen nicht in denselben Nachmittag laufen. Die beiden `.py`-Dateien im selben Ordner bleiben untracked |
| D10 | **`asyncua.Server.register_to_discovery()` registriert `0.0.0.0`** | `asyncua` 2.0.1: `client/client.py`, `register_server` | Die Methode trägt `server.endpoint.geturl()` als DiscoveryUrl ein. Unser Endpoint bindet auf `0.0.0.0`, und der Aggregation-Server übernimmt diese Adresse wörtlich und verbindet ins Leere. Deshalb baut `src/ua_lds.py` den Registrierungsdatensatz selbst, mit der LAN-IPv4 aus `ua_mdns.detect_lan_ipv4()` |

---

## Ausdrücklich **nicht** in dieser Liste

Das Folgende sind Platzhalter der Lokalisierung selbst, also die eigentliche Arbeit —
keine Altlasten:

- `detection/hello_world.py` und das Profil `hello_world`. Es bleibt als kamerafreier
  Smoke-Test dauerhaft nützlich, damit das Backend-Team ohne Hardware testen kann.
- ~~`frameId = "world"`, obwohl noch kein Weltsystem existiert.~~ **Erledigt**: der
  Rahmen folgt jetzt dem Bild — `world`, sobald ein Referenz-Tag aus der Tag-Map
  sichtbar ist, sonst das Kamera-KS der Quelle.
- ~~Posen fest auf `[0,0,0]` / `[0,0,0,1]`.~~ **Erledigt** für `apriltag`; für
  `hello_world` und `calibration` bleiben sie Null, und das ist dort richtig.
- ~~Dass `detect_apriltags.py` die Verzerrungskoeffizienten nicht anwendet.~~
  **Erledigt** in `tagloc.pose`: die Ecken werden vor `solvePnP` entzerrt. Der
  Prototyp `src/apriltag/detect_apriltags.py` steht noch, ist aber als abgelöst
  gekennzeichnet.
- **Offen bleibt die Hand-Auge-Kalibrierung** für Layer 2. Bis sie steht, liefert
  die Flanschkamera im Kamera-KS und `auto_execute` bleibt `False`.

Siehe [`apriltag-lokalisierung.md`](apriltag-lokalisierung.md) und
[`apriltag-e2e-test.md`](apriltag-e2e-test.md).

---

## Reihenfolge, wenn abgebaut wird — **durchlaufen am 21.09.2026**

Die Kette stand hier vorgezeichnet und wurde genau so abgearbeitet:

1. ~~**B4** (Temperaturanzeige im Popup) und **B2** (Relay)~~ — danach las nichts mehr
   eine Temperatur über den Vision-Baum.
2. ~~**A2** (`CpuTemperatureResult`)~~ — damit war **A1** leer.
3. ~~**A1** (`2:VisionSystem`)~~ — seitdem ist der Adressraum eindeutig, und **B6**
   (`legacy`-Zweig) konnte fallen.
4. ~~**A4**, **A5**, **A3**, **A6**~~ — der Rest der Demo, samt Namensraum.
5. **C3** ist bereits erledigt (siehe Abschnitt C).

Offen bleiben **B1**, **B3**, **B5**, **B7**, **B8** im WSC-Repo sowie **C2**, **C5**,
**C7** und der gesamte Abschnitt D. B1 hängt an der mDNS-Umstellung: die Adressen sind
noch Konstanten, obwohl der Server sich inzwischen selbst ankündigt.

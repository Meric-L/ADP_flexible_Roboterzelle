# Part-10-Programm und mDNS — Schnittstelle für Backend und Frontend

Diese Datei beschreibt **zwei neue Fähigkeiten** des Vision-Servers und was
Backend und Frontend dafür tun müssen. Sie ist ohne Kenntnis dieses Repos
benutzbar.

Stand: 2026-09-21, alle Angaben gegen einen laufenden Server verifiziert
(asyncua 2.0.1, Python 3.12).

> **Nichts bricht.** Beides ist **additiv**. Die bestehende OPC-40100-Schnittstelle
> aus [`vision-server-interface.md`](vision-server-interface.md) — `StartSingleJob`,
> `Stop`, die 40100-Events, `LatestResultJson`, das Payload-Schema
> `wsc.vision.detections/1` — bleibt unverändert gültig und funktioniert weiter.
> Ein Backend, das heute läuft, läuft nach diesem Update unverändert weiter.

---

## 1. Warum das hier gebaut wurde

Der Vision-Server ist bisher der Sonderfall im Netz: Um ihn zu bedienen, muss
ein Client OPC 40100 (Machine Vision) verstehen — zwei geschachtelte
Zustandsautomaten, `StartSingleJob` mit fünf getypten Argumenten, eine
projekteigene Fehlercode-Tabelle. Dieses Wissen lässt sich für kein anderes
Modul der Zelle wiederverwenden.

Die übrigen Module benutzen dagegen **OPC-UA Teil 10 „Programs"**. Nachgesehen
am 2026-09-21 im Laborsubnetz:

| Modul | Endpoint | Programme |
| --- | --- | --- |
| Conveyor | `opc.tcp://10.10.38.41:4840/conveyor/` | `RunContinuous`, `MoveDistance`, `MoveUntilSensor` — alle vom Typ `ProgramStateMachineType` |
| CardDispenser | `opc.tcp://10.10.38.40:4840/card-dispenser/` | (DI/Machinery, im geprüften Bereich keine Programme) |

Der Vision-Server bekommt deshalb **zusätzlich** ein Part-10-Programm in
derselben Form. Ein Frontend, das die Conveyor-Skills bedienen kann, kann damit
ohne neuen Code auch die Vision starten, anhalten und ihren Zustand anzeigen.

**Was generisch wird:** Starten, Anhalten, Zurücksetzen, Zustandsanzeige,
Knopf-Freigabe, Zustandswechsel-Events.
**Was vision-spezifisch bleibt:** das Erkennungsergebnis (Posen, Tag-Ids,
Bezugsrahmen) und der Kamera-Livestream. Teil 10 sagt über Ergebnisse nichts;
dafür bleibt der Weg aus `vision-server-interface.md` maßgeblich.

---

## 2. Verbindung: mDNS statt hartcodierter IP

Der Zellserver kündigt sich jetzt selbst im lokalen Netz an. Damit entfallen
die hartcodierten Pi-Adressen (`10.10.38.104` / `.109` in
`backend/src/backend/config/pi_relay.py` und
`frontend/src/features/opcua-server/config/piServers.ts`, siehe Altlast B1).

| | Wert |
| --- | --- |
| Dienst-Typ | `_opcua-tcp._tcp.local.` |
| Instanzname | die Vision-Identität des Pi, z. B. `vision-ceiling-01`, `vision-flange-01` |
| Hostname | `<instanzname>.local.` |
| Port | `4840` |
| TXT `path` | `/raspi/server/` |
| TXT `caps` | `DA` |

**Endpoint-URL zusammenbauen:** `opc.tcp://<adresse>:<port><path>` — also
`opc.tcp://10.10.38.109:4840/raspi/server/`. Den Pfad **aus dem TXT-Eintrag
lesen**, nicht annehmen; die Nachbarn benutzen andere Pfade
(`/conveyor/`, `/card-dispenser/`).

So sieht eine Suche im Netz aus (real gemessen):

```
vision-ceiling-01._opcua-tcp._tcp.local.   -> opc.tcp://10.10.38.109:4840/raspi/server/   caps=DA
Conveyor-Conveyor._opcua-tcp._tcp.local.   -> opc.tcp://10.10.38.41:4840/conveyor/        caps=DA
CardDispenser-Black-CDBlack._opcua-tcp._tcp.local. -> opc.tcp://10.10.38.40:4840/card-dispenser/  caps=DA
```

### Der Aggregation-Server findet uns von selbst

Die Zelle betreibt unter `opc.tcp://10.10.38.27:48400/` einen
`AggregationServer` (`roboteach.plcm.tu-darmstadt.de/agg-server`), der die
Module einsammelt. **Er durchsucht mDNS selbst** — es gibt keine Registrierung,
die ein Modul aktiv senden müsste, und `RegisterServer2` ist nicht nötig. Es
genügt, dass der Server läuft und sich ankündigt.

Dort erscheint ein Modul unter seiner **ApplicationUri**, nicht unter dem
mDNS-Namen. Vorhandene Einträge und unsere:

| Modul | ApplicationUri |
| --- | --- |
| UR5e | `urn:plcm:robot-server:ur5e` |
| CardDispenser | `urn:smart-business-card-factory:card-dispenser-system` |
| Conveyor | `urn:smart-business-card-factory:conveyor-system` |
| **Vision Decke** | **`urn:plcm:camera-server:ceiling-01`** |
| **Vision Roboterhand** | **`urn:plcm:camera-server:roboter-hand-01`** |

Der Aggregation-Server hängt die ApplicationUri an jeden Namespace des Moduls
an (z. B. `http://launch-rm.de/vision/urn:plcm:camera-server:ceiling-01`) und
legt das Modul als Objekt unter `Objects` in seinem eigenen ns=1 ab. Wer über
den Aggregation-Server auf unsere Knoten zugreift, löst den Namensraum also
über die **zusammengesetzte** URI auf — direkt am Pi bleibt es bei
`http://launch-rm.de/vision`.

Zu beachten:

- **Keine feste IP nötig.** Die Adresse wird beim Start ermittelt; eine
  DHCP-Reservierung im Router reicht. Wechselt der Lease im laufenden Betrieb,
  zeigt die Ankündigung bis zum Dienstneustart ins Leere.
- **mDNS endet an der Subnetzgrenze** (UDP-Multicast `224.0.0.251:5353`, TTL 1).
  Aus einem anderen VLAN oder aus dem Gast-WLAN findet man nichts, obwohl der
  Server läuft. Das ist der häufigste „geht nicht"-Fall.
- **Die manuelle Eingabe muss bleiben.** Die vorhandene `ConnectOpcUa`-Eingabe
  ist weiterhin der Rückfallweg, wenn mDNS blockiert ist.
- Beim geordneten Beenden wird die Ankündigung zurückgezogen; nach einem harten
  Abbruch steht sie noch bis zum TTL-Ablauf in den Client-Caches.

---

## 3. Namespace — niemals hartcodieren

| Namespace | URI |
| --- | --- |
| Vision (dieses Dokument) | `http://launch-rm.de/vision` |
| OPC 40100 | `http://opcfoundation.org/UA/MachineVision` |
| Raspi-Interface | `http://launch-rm.de/raspi` |

Immer `await client.get_namespace_index("<uri>")` benutzen.

> **Achtung, der Index hat sich verschoben.** Der Vision-Namespace liegt auf dem
> Zellserver inzwischen auf **ns=7**, nicht mehr auf ns=4 — seit die Part-2-
> Nodesets (DI, Machinery, AMCM) dazugekommen sind. Aktuelle Reihenfolge:

```
ns=0  http://opcfoundation.org/UA/
ns=1  urn:freeopcua:python:server
ns=2  http://launch-rm.de/raspi
ns=3  http://opcfoundation.org/UA/MachineVision          <- OPC 40100
ns=4  http://opcfoundation.org/UA/DI/
ns=5  http://opcfoundation.org/UA/Machinery/
ns=6  http://opcfoundation.org/UA/MachineVision/AMCM/
ns=7  http://launch-rm.de/vision                          <- VisionMachine, VisionProgram
```

Wer ns=4 noch fest verdrahtet hat, greift ab jetzt ins Leere. Das ist der
einzige Punkt in diesem Dokument, der bestehenden Code **brechen kann** — und
er betrifft den 40100-Pfad genauso.

---

## 4. Adressraum des Programms

Alle NodeIds sind sprechende String-Ids und über Neustarts stabil. Browsen ist
möglich, aber nicht nötig.

```
Objects/
├── VisionProgram                     ns=<vision>;s=VisionProgram   (Typ: ProgramStateMachineType, ns=0;i=2391)
│   ├── CurrentState                  LocalizedText: Halted | Ready | Running | Suspended
│   ├── LastTransition                LocalizedText
│   ├── AvailableStates               NodeId[]
│   ├── AvailableTransitions          NodeId[]
│   ├── Start                         Methode, **keine** Eingaben
│   ├── Halt                          Methode, keine Eingaben
│   ├── Reset                         Methode, keine Eingaben
│   ├── Suspend                       Methode, keine Eingaben (lehnt immer ab)
│   ├── Resume                        Methode, keine Eingaben (lehnt immer ab)
│   ├── ParameterSet                  <- Eingaben VOR dem Start hierhin schreiben
│   │   ├── RecipeId     String       **beschreibbar**
│   │   └── Continuous   Boolean      **beschreibbar**
│   └── ResultSet                     <- Ausgaben
│       ├── JobId           String
│       ├── ErrorCode       Int32
│       ├── ExecutionMode   String    idle | single | continuous
│       └── LatestResultJson          Verweis auf denselben Knoten wie unten
└── VisionMachine                     ns=<vision>;s=VisionMachine   (OPC 40100, unverändert)
    └── ... StartSingleJob, Stop, ResultManagement, LatestResultJson, LatestCameraFrame ...
```

Feste NodeIds:

| Knoten | NodeId |
| --- | --- |
| Programm | `ns=<vision>;s=VisionProgram` |
| Zustand | `ns=<vision>;s=VisionProgram` → Kind `0:CurrentState` |
| Start | `ns=<vision>;s=VisionProgram.Start` |
| Halt | `ns=<vision>;s=VisionProgram.Halt` |
| Reset | `ns=<vision>;s=VisionProgram.Reset` |
| Rezept (schreiben) | `ns=<vision>;s=VisionProgram.ParameterSet.RecipeId` |
| Dauerbetrieb (schreiben) | `ns=<vision>;s=VisionProgram.ParameterSet.Continuous` |
| JobId | `ns=<vision>;s=VisionProgram.ResultSet.JobId` |
| Fehlercode | `ns=<vision>;s=VisionProgram.ResultSet.ErrorCode` |
| Ausführungsart | `ns=<vision>;s=VisionProgram.ResultSet.ExecutionMode` |
| Ergebnis-JSON | `ns=<vision>;s=VisionMachine.LatestResultJson` |

**Generisch finden** geht ebenfalls: nach Instanzen von `ProgramStateMachineType`
(`ns=0;i=2391`) browsen. Das ist der Weg, der auch beim Conveyor funktioniert —
dessen Programme haben numerische Ids (`ns=7;i=6100`) und sind nur so auffindbar.

---

## 5. Ablauf: einen Job fahren

**Die Reihenfolge ist bindend: erst abonnieren, dann Parameter schreiben, dann
`Start()`.** `Start` nimmt keine Argumente — genau wie bei den anderen Modulen.

```
Frontend                                    VisionProgram
   |  1. subscribe_events(VisionProgram, [i=2378])
   |  2. write ParameterSet/RecipeId   = "apriltag"
   |  3. write ParameterSet/Continuous = false
   |------------------- Start() ---------------------->|
   |                                                   |  Guard + Validierung (synchron)
   |<-- ProgramTransitionEvent  Ready -> Running ------|  angenommen
   |      (bei Ablehnung: KEIN Zustandswechsel,
   |       stattdessen Event mit Severity 500)
   |                                                   |
   |<-- (die 40100-Events laufen unverändert weiter)   |
   |<-- ProgramTransitionEvent  Running -> Ready ------|  fertig
   |  4. read ResultSet/JobId, ResultSet/ErrorCode
   |  5. read VisionMachine.LatestResultJson           |  das eigentliche Ergebnis
```

Beispiel (asyncua):

```python
ns = await client.get_namespace_index("http://launch-rm.de/vision")
prog = client.get_node(ua.NodeId("VisionProgram", ns))

sub = await client.create_subscription(100, handler)
await sub.subscribe_events(prog, [client.get_node(ua.NodeId(2378, 0))])

recipe = client.get_node(ua.NodeId("VisionProgram.ParameterSet.RecipeId", ns))
await recipe.write_value("apriltag", ua.VariantType.String)

await prog.call_method(client.get_node(ua.NodeId("VisionProgram.Start", ns)))
```

### Zustände

| Zustand | Nr. | ns=0 NodeId | Bedeutung hier |
| --- | --- | --- | --- |
| `Halted` | 11 | `i=2406` | Vision-System nicht betriebsbereit (z. B. Kamera ging nicht auf) oder per `Halt` angehalten |
| `Ready` | 12 | `i=2400` | aufnahmebereit |
| `Running` | 13 | `i=2402` | ein Job läuft |
| `Suspended` | 14 | `i=2404` | **wird nie erreicht** |

Übergänge sind die Standardknoten `ns=0;i=2408` … `i=2424`.

### Welcher Knopf darf gedrückt werden

Nicht selbst nachbauen: Der Server pflegt das **`Executable`-Attribut** jeder
Methode. Das Frontend liest es und graut Knöpfe danach aus — dieselbe Logik wie
für jedes andere Modul.

| Zustand | ausführbar |
| --- | --- |
| `Halted` | `Reset` |
| `Ready` | `Start`, `Halt` |
| `Running` | `Halt`, `Suspend` |
| `Suspended` | `Halt`, `Resume` |

### `Suspend` / `Resume`

Existieren, weil Teil 10 sie vorschreibt, **lehnen aber immer ab**: Ein
Vision-Job dauert 250 ms bis 20 s und lässt sich nicht sinnvoll pausieren. Der
Aufruf ändert den Zustand nicht und liefert ein Event mit Severity 500 und dem
Text „Ein Vision-Job kann nicht pausiert werden; Halt verwenden." Das Frontend
sollte beide Knöpfe gar nicht erst anbieten.

---

## 6. Events

| | |
| --- | --- |
| Event-Typ | `ProgramTransitionEventType`, `ns=0;i=2378` |
| Emittierender Knoten | **`VisionProgram`** |

**Zwei Fallen, beide verifiziert:**

1. **Die Event-Typ-Liste ist zwingend.** `subscribe_events(prog)` ohne zweites
   Argument liefert zwar Events, aber `FromState`, `ToState` und `Transition`
   sind dann `None` — nur `Message` ist gefüllt. Mit
   `subscribe_events(prog, [client.get_node(ua.NodeId(2378, 0))])` sind alle
   Felder da. Dieselbe Falle wie bei den 40100-Events.
2. **Auf `VisionProgram` abonnieren, nicht auf dem Server-Objekt.** asyncua
   bubbelt Events serverseitig nicht.

Gemessene Felder eines erfolgreichen Jobs:

```
FromState=Ready    ToState=Running  Transition=ReadyToRunning  Message="ReadyToRunning"
FromState=Running  ToState=Ready    Transition=RunningToReady  Message="RunningToReady ; Job job-000001 abgeschlossen"
```

Bei einem **abgelehnten** Start gibt es keinen Zustandswechsel, sondern nur ein
Event mit `Severity=500` und z. B.
`Message="Start abgelehnt: UNKNOWN_RECIPE (4)"`.

---

## 7. Fehler

`Start()` hat — wie bei allen Part-10-Programmen — **keinen Rückgabewert**.
Ob der Job angenommen wurde, erfährt das Frontend auf zwei Wegen:

1. **Sofort:** Kommt ein Zustandswechsel nach `Running`, ist er angenommen.
   Kommt stattdessen ein Event mit `Severity=500`, ist er abgelehnt; der Grund
   steht im Klartext in `Message`.
2. **Maschinenlesbar:** `ResultSet/ErrorCode` trägt den Code.

| Code | Name | Bedeutung |
| --- | --- | --- |
| 0 | `OK` | angenommen bzw. sauber beendet |
| 1 | `INVALID_STATE` | Vision-System nicht betriebsbereit |
| 2 | `INVALID_ARGUMENT` | Parameter unbrauchbar |
| 3 | `BUSY` | es läuft bereits ein Job |
| 4 | `UNKNOWN_RECIPE` | `RecipeId` nicht bekannt |
| 5 | `DETECTION_FAILED` | Erkennung fehlgeschlagen oder Zeitüberschreitung |
| 6 | `INTERNAL` | unerwarteter Serverfehler |
| 7 | `CANCELLED` | durch `Halt` abgebrochen |

**Bewusste Abweichung von Teil 10:** Ein *fehlgeschlagener* Job (5, 6) führt
**nicht** nach `Halted`, sondern zurück nach `Ready`. Sonst müsste das Frontend
nach jeder misslungenen Erkennung erst `Reset` drücken, und die beiden
Automaten liefen auseinander — der 40100-Automat kehrt nach einem Fehler
ebenfalls nach `Ready` zurück. Nach `Halted` kommt man nur durch **`Halt`** oder
wenn das System beim Start gar nicht hochkam.

---

## 8. Das Ergebnis holen

Teil 10 definiert keinen Ergebnistransport. **Hier ändert sich nichts** gegenüber
[`vision-server-interface.md`](vision-server-interface.md) Abschnitt 6:

- Der JSON-String steht in `ns=<vision>;s=VisionMachine.LatestResultJson`
  (unter `ResultSet` zusätzlich verlinkt — **derselbe Knoten**, nicht zwei).
- Schema unverändert `wsc.vision.detections/1`: `detections[]` mit `moduleId`,
  `position` (Meter), `orientation` (Quaternion `xyzw`), `frameId`,
  `frameConvention`, `attributes`.
- Wer bereits die 40100-`ResultReadyEvent`-Auswertung hat, behält sie. Das
  Payload reist dort unverändert mit.
- Zuordnung Ergebnis → Job über `payload["jobId"]`, abgleichbar mit
  `ResultSet/JobId`.
- Livestream weiterhin über `VisionMachine.LatestCameraFrame` (Base64-JPEG).

---

## 9. Verfügbare Rezepte (`ParameterSet/RecipeId`)

Der Server nennt die zugelassenen Werte selbst: in der **Description** des
Knotens `ParameterSet/RecipeId`. Ein generisches Frontend kann sie also
auslesen, statt sie zu kennen.

Aktuell auf diesem Stand:

| `RecipeId` | Job |
| --- | --- |
| `""` oder `"hello-world"` | Platzhalter ohne Bildverarbeitung, kamerafreier Smoke-Test |
| `"calibration"` | Messbereitschaft der Zelle prüfen |
| `"image-recognition"` | QR-Erkennung über die Kamera |

**Angekündigt, noch nicht gemergt:** `"apriltag"` — die AprilTag-Lokalisierung
(echte Modulposen). Sie liegt auf einem eigenen Branch und ist noch ungetestet,
kommt also als **späterer Schritt**. Für Backend und Frontend ändert sich dann
**nichts an dieser Schnittstelle**: derselbe `Start()`, dasselbe `ParameterSet`,
dasselbe Payload-Schema — nur die Werte in `detections[]` werden echt, und
`attributes` trägt zusätzlich `tagId`, `reprojErrorPx`, `ambiguous`,
`sampleCount`. Es lohnt sich deshalb, die Rezeptauswahl jetzt schon
datengetrieben aus der Description zu bauen statt fest zu verdrahten.

---

## 10. Was Backend und Frontend konkret tun müssen

Nichts davon ist Pflicht, um weiterzulaufen — alles ist Gewinn.

**Muss (sonst bricht etwas):**

- [ ] `ns=4` für den Vision-Namespace **überall** durch
      `get_namespace_index("http://launch-rm.de/vision")` ersetzen. Der Index
      ist jetzt 7 (Abschnitt 3).

**Soll (dafür wurde es gebaut):**

- [ ] mDNS-Suche nach `_opcua-tcp._tcp.local.` einbauen; Endpoint-URL aus
      Adresse, Port und TXT-`path` zusammensetzen (Abschnitt 2).
- [ ] Hartcodierte Pi-Adressen entfernen (`pi_relay.py`, `piServers.ts`),
      manuelle Eingabe als Rückfall behalten.
- [ ] Eine **generische Programm-Komponente** bauen: Instanzen von
      `ProgramStateMachineType` finden, `ParameterSet` als Formular rendern,
      `Start`/`Halt`/`Reset` anbieten, Knöpfe nach `Executable` freigeben,
      `CurrentState` anzeigen, `ProgramTransitionEvent` abonnieren. Dieselbe
      Komponente bedient danach auch Conveyor und CardDispenser.
- [ ] Rezeptauswahl aus der Description von `ParameterSet/RecipeId` speisen.

**Kann bleiben, wie es ist:**

- [ ] Die gesamte 40100-Auswertung: `StartSingleJob`, `Stop`, `ResultReadyEvent`,
      `LatestResultJson`, `LatestCameraFrame`, Payload-Parser.

---

## 11. Stolpersteine

| # | Punkt |
| --- | --- |
| 11.1 | **Parameter vor dem Start schreiben.** `Start()` liest `ParameterSet` erst beim Aufruf. Wer die Reihenfolge dreht, startet mit den alten Werten. |
| 11.2 | **`ParameterSet` ist Zustand, kein Aufrufargument.** Zwei Clients, die gleichzeitig verschiedene Rezepte schreiben, überschreiben sich. Bei nur einem Frontend unkritisch, aber bewusst so — es ist die Konvention der Zelle. |
| 11.3 | **Nur ein Job gleichzeitig.** Ein zweiter `Start` wird mit `BUSY` (3) abgelehnt — egal ob über das Programm oder über `StartSingleJob`. Beide Oberflächen teilen sich denselben Job. |
| 11.4 | **Beide Oberflächen zeigen denselben Job.** Wird ein Job über `StartSingleJob` gestartet, geht auch `VisionProgram` nach `Running` und am Ende zurück nach `Ready`. Das Frontend darf also mischen. |
| 11.5 | **`Halt` ohne laufenden Job** ist erlaubt und geht trotzdem nach `Halted`. Zurück nur über `Reset`. |
| 11.6 | **`Start()` hat keinen Rückgabewert** — anders als `StartSingleJob`, das `(JobId, Error)` liefert. Wer die JobId sofort braucht, liest `ResultSet/JobId` nach dem Zustandswechsel. |
| 11.7 | **`caps=DA`.** Die Betreuer-Kurzanleitung nennt `NA`; wir kündigen `DA` an, weil Conveyor und CardDispenser das tun und ein Filter uns sonst übersieht. |

---

## 12. Selbst ausprobieren

```bash
pip install -r requirements.txt          # bringt jetzt auch `zeroconf` mit

# Zellserver starten (Raspi-Interface + VisionMachine + VisionProgram + mDNS)
python3 src/OPCUA/server.py

# Auf dem Pi laeuft er als Service:
sudo systemctl restart opcua-server.service
```

Im Netz suchen:

```bash
avahi-browse -rt _opcua-tcp._tcp
```

Mit UaExpert: `VisionProgram` browsen, `ParameterSet/RecipeId` auf
`hello-world` schreiben, `Start` aufrufen, `CurrentState` beobachten.

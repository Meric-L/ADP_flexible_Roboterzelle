# Part-10-Programm und mDNS — Schnittstelle für Backend und Frontend

Diese Datei beschreibt zwei Fähigkeiten des Vision-Servers und was Backend und
Frontend dafür tun müssen. Sie ist ohne Kenntnis dieses Repos benutzbar.

Geprüft gegen einen laufenden Server (asyncua 2.0.1, Python 3.12).

> **Nichts bricht.** Beides ist **additiv**. Die bestehende OPC-40100-Schnittstelle
> aus [`vision-server-interface.md`](vision-server-interface.md) — `StartSingleJob`,
> `Stop`, die 40100-Events, `LatestResultJson`, das Payload-Schema
> `wsc.vision.detections/1` — bleibt unverändert gültig.

---

## 1. Zweck

Der Vision-Server ist bisher der Sonderfall im Netz: Um ihn zu bedienen, muss ein
Client OPC 40100 (Machine Vision) verstehen — zwei geschachtelte Zustandsautomaten,
`StartSingleJob` mit fünf getypten Argumenten, eine projekteigene Fehlercode-Tabelle.

Die übrigen Module nutzen **OPC-UA Teil 10 „Programs"**:

| Modul | Endpoint | Programme |
| --- | --- | --- |
| Conveyor | `opc.tcp://10.10.38.41:4840/conveyor/` | `RunContinuous`, `MoveDistance`, `MoveUntilSensor` — alle vom Typ `ProgramStateMachineType` |
| CardDispenser | `opc.tcp://10.10.38.40:4840/card-dispenser/` | keine Programme im geprüften Bereich (DI/Machinery) |

Der Vision-Server bekommt deshalb **zusätzlich** ein Part-10-Programm in derselben
Form. Ein Frontend, das die Conveyor-Skills bedient, kann damit ohne neuen Code auch
die Vision starten, anhalten und ihren Zustand anzeigen.

**Generisch:** Starten, Anhalten, Zurücksetzen, Zustandsanzeige, Knopf-Freigabe,
Zustandswechsel-Events.
**Vision-spezifisch:** das Erkennungsergebnis (Posen, Tag-Ids, Bezugsrahmen) und der
Kamera-Livestream. Teil 10 sagt über Ergebnisse nichts; dafür gilt weiterhin
`vision-server-interface.md`.

---

## 2. Verbindung: mDNS statt hartcodierter IP

Der Zellserver kündigt sich im lokalen Netz an. Die hartcodierten Pi-Adressen in
`backend/src/backend/config/pi_relay.py` und
`frontend/src/features/opcua-server/config/piServers.ts` werden damit überflüssig
(Abschnitt 10).

| | Wert |
| --- | --- |
| Dienst-Typ | `_opcua-tcp._tcp.local.` |
| Instanzname | die Vision-Identität des Pi, z. B. `vision-ceiling-01`, `vision-flange-01` |
| Hostname | `<instanzname>.local.` |
| Port | `4840` |
| TXT `path` | `/raspi/server/` |
| TXT `caps` | `DA` |

**Endpoint-URL zusammenbauen:** `opc.tcp://<adresse>:<port><path>`, also z. B.
`opc.tcp://10.10.38.104:4840/raspi/server/`. Den Pfad **aus dem TXT-Eintrag lesen**,
nicht annehmen; die Nachbarn benutzen andere Pfade (`/conveyor/`, `/card-dispenser/`).

Beispiel einer Suche (die Pi-Adressen kommen per DHCP und wechseln, deshalb immer den
Eintrag lesen statt die IP abzuschreiben):

```
vision-ceiling-01._opcua-tcp._tcp.local.   -> opc.tcp://10.10.38.104:4840/raspi/server/   caps=DA
vision-flange-01._opcua-tcp._tcp.local.    -> opc.tcp://10.10.38.109:4840/raspi/server/   caps=DA
Conveyor-Conveyor._opcua-tcp._tcp.local.   -> opc.tcp://10.10.38.41:4840/conveyor/        caps=DA
CardDispenser-Black-CDBlack._opcua-tcp._tcp.local. -> opc.tcp://10.10.38.40:4840/card-dispenser/  caps=DA
```

### Aggregation-Server: Anmeldung per `RegisterServer` nötig

Die Zelle betreibt einen `AggregationServer` unter `opc.tcp://10.10.38.27:48400/`
(`roboteach.plcm.tu-darmstadt.de/agg-server`), der die Module einsammelt. Auf
demselben Rechner läuft ein **open62541 Local Discovery Server (LDS)** unter
`opc.tcp://10.10.38.27:4840/`.

Der Aggregation-Server führt genau die Module, die im LDS registriert sind. In den
LDS kommt man nur durch einen aktiven **`RegisterServer`**-Aufruf (`RegisterServer2`
tut es auch). Eine mDNS-Ankündigung allein reicht **nicht**. Gemessen:

- Beide Pis waren per mDNS sichtbar und direkt erreichbar, der Aggregation-Server
  führte sie trotzdem nicht. Beim Neustart des Aggregation-Servers wurden nur die
  fünf im LDS registrierten Module aufgenommen.
- Gegenprobe: eine einzelne `RegisterServer2`-Anfrage für
  `urn:plcm:camera-server:ceiling-01` führte das Modul samt aller neun Namespaces
  31 s später im Aggregation-Server. Nach dem Abmelden war es wieder weg.

Umgesetzt in `src/vision_server/discovery/lds.py`, aufgerufen aus
`src/vision_server/server.py` neben der mDNS-Ankündigung. Beide Wege bleiben: der LDS
bringt uns in den Aggregation-Server, mDNS bedient Clients im Subnetz direkt.

| | Wert |
| --- | --- |
| Discovery-Server | `opc.tcp://10.10.38.27:4840/` |
| Dienst | `RegisterServer`, **ohne** `MdnsDiscoveryConfiguration` |
| Verbindung | sessionlos, `NoSecurity` |
| Erneuerung | alle 60 s (notwendig, siehe unten) |
| Registrierte DiscoveryUrl | `opc.tcp://<LAN-IPv4>:4840/raspi/server/` |
| Abschalten / umbiegen | `OPCUA_LDS_URL=""` bzw. `OPCUA_LDS_URL=opc.tcp://host:4840/` |

**Einmal anmelden genügt nicht.** Ein Neustart des LDS verwirft alle Anmeldungen;
die Nachbarmodule erneuern deshalb periodisch. `asyncua.Server.register_to_discovery()`
startet diese Erneuerungsschleife selbst (Standardabstand **60 s**, Parameter
`period`) und baut dabei jeden Kanal neu auf.

`Server.stop()` bricht nur die Schleife ab. Ohne `unregister_from_discovery()` bliebe
der Eintrag bis zum Ablauf stehen. `discovery.lds.register()` ruft es im `finally` auf.
Wie lange eine Anmeldung ohne Erneuerung hält, ist **nicht gemessen**; die Angabe
„mindestens alle 10 Minuten" stammt aus dem asyncua-Docstring.

**Falle 1:** `register_to_discovery()` trägt `server.endpoint.geturl()` als
DiscoveryUrl ein. Ein Endpoint auf `0.0.0.0` meldet dem Aggregation-Server also
`0.0.0.0`, und der verbindet ins Leere. Lösung: `Server.socket_address` setzt die
LAN-IPv4 für den Endpoint, gelauscht wird weiter auf `0.0.0.0`. `server.py` setzt
beides; die URL baut `discovery.lds.advertised_endpoint()`. Der Server ist damit über
`127.0.0.1` **und** die LAN-IP erreichbar, was lokale Skripte auf dem Pi benötigen.

**Falle 2 (einmal beobachtet, nicht reproduziert):** Bei `RegisterServer2` mit
`MdnsDiscoveryConfiguration` stand im LDS einmal die DiscoveryUrl
`opc.tcp://10.10.38.104.local:4840/…`, ein an eine IP gehängtes `.local`, das nicht
auflöst. Wir melden uns deshalb mit dem schlichten `RegisterServer` an, also mit
derselben Konfiguration, mit der Conveyor, CardDispenser und die Roboter laufen.

Im Aggregation-Server erscheint ein Modul unter seiner **ApplicationUri**, nicht unter
dem mDNS-Namen:

| Modul | ApplicationUri |
| --- | --- |
| UR5e | `urn:plcm:robot-server:ur5e` |
| CardDispenser | `urn:smart-business-card-factory:card-dispenser-system` |
| Conveyor | `urn:smart-business-card-factory:conveyor-system` |
| **Vision Decke** | **`urn:plcm:camera-server:ceiling-01`** |
| **Vision Roboterhand** | **`urn:plcm:camera-server:roboter-hand-01`** |

Der Aggregation-Server hängt die ApplicationUri an jeden Namespace des Moduls an
(z. B. `http://launch-rm.de/vision/urn:plcm:camera-server:ceiling-01`) und legt das
Modul als Objekt unter `Objects` in seinem ns=1 ab. Wer über den Aggregation-Server
zugreift, löst den Namensraum also über die **zusammengesetzte** URI auf. Direkt am
Pi bleibt es bei `http://launch-rm.de/vision`.

Zu beachten:

- **Keine feste IP nötig.** Die Adresse wird beim Start ermittelt; eine
  DHCP-Reservierung reicht. Wechselt der Lease im Betrieb, zeigt die Ankündigung bis
  zum Dienstneustart ins Leere.
- **mDNS endet an der Subnetzgrenze** (UDP-Multicast `224.0.0.251:5353`, TTL 1). Aus
  einem anderen VLAN oder dem Gast-WLAN findet man nichts, obwohl der Server läuft.
  Das ist der häufigste „geht nicht"-Fall. Die LDS-Registrierung ist davon **nicht**
  betroffen (normale TCP-Verbindung zu `10.10.38.27:4840`).
- **Manuelle Eingabe bleibt.** Die `ConnectOpcUa`-Eingabe ist der Rückfallweg, wenn
  mDNS blockiert ist.
- Beim geordneten Beenden werden Ankündigung **und** Registrierung zurückgezogen. Nach
  einem harten Abbruch bleiben beide bis zum Ablauf der TTL bzw. der LDS-Frist stehen:
  der Aggregation-Server zeigt das Modul, kommt aber nicht mehr dran.
- **Der Aggregation-Server räumt Namespaces nicht auf.** Verlässliches Zeichen für ein
  verschwundenes Modul ist ein fehlendes Objekt, nicht ein fehlender Namespace.

---

## 3. Namespace — niemals hartcodieren

| Namespace | URI |
| --- | --- |
| Vision (dieses Dokument) | `http://launch-rm.de/vision` |
| OPC 40100 | `http://opcfoundation.org/UA/MachineVision` |
| Machinery (trägt `Objects/Machines`) | `http://opcfoundation.org/UA/Machinery/` |

Immer `await client.get_namespace_index("<uri>")` benutzen. Der Index verschiebt sich,
wenn Nodesets dazukommen oder wegfallen. Aktuelle Reihenfolge, gegen einen laufenden
Server ausgelesen:

```
ns=0  http://opcfoundation.org/UA/
ns=1  urn:plcm:camera-server:<einbauort>                  <- ApplicationUri
ns=2  http://opcfoundation.org/UA/MachineVision           <- OPC 40100
ns=3  http://opcfoundation.org/UA/DI/
ns=4  http://opcfoundation.org/UA/Machinery/              <- trägt Objects/Machines
ns=5  http://opcfoundation.org/UA/MachineVision/AMCM/
ns=6  http://launch-rm.de/vision                          <- VisionMachine, VisionProgram
```

Wer einen dieser Indizes fest verdrahtet hat, greift ins Leere. Das ist der einzige
Punkt in diesem Dokument, der bestehenden Code **brechen kann**, und er betrifft den
40100-Pfad genauso.

---

## 4. Adressraum des Programms

Alle NodeIds sind sprechende String-Ids und über Neustarts stabil. Browsen ist möglich,
aber nicht nötig.

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
│       ├── LatestResultJson          Verweis auf denselben Knoten wie unten
│       ├── LatestCameraFrame         Verweis  (Base64-JPEG, nur mit Kamera)
│       ├── CameraStreamMode          Verweis, **beschreibbar**
│       └── CalibrationProgress       Verweis  (JSON, nur mit AprilTag-Profil)
├── StartCalibration                  Verweis auf die 40100-Methode, keine Eingaben
├── CaptureCalibrationSample          Verweis; Error: Int32
├── FinishCalibration                 Verweis; Summary: String, Error: Int32
└── AbortCalibration                  Verweis; Error: Int32

Machines/
└── VisionMachine                     ns=<vision>;s=VisionMachine   (OPC 40100, unverändert)
    └── ... StartSingleJob, Stop, ResultManagement, LatestResultJson, LatestCameraFrame ...
```

**`VisionProgram` genügt sich selbst.** Alles, was ein Client zum Fahren und Auswerten
eines Jobs braucht, hängt darunter, als Referenz und nicht als Kopie. `VisionMachine`
liegt im Machinery-Ordner `Machines`.

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

**Generisch finden:** nach Instanzen von `ProgramStateMachineType` (`ns=0;i=2391`)
browsen. So funktioniert es auch beim Conveyor, dessen Programme numerische Ids haben
(`ns=7;i=6100`) und nur so auffindbar sind.

### Implementierung: `src/ua_program` und `vision_server.vision_program`

- **`src/ua_program/program.py`** (Exporte in `src/ua_program/__init__.py`: `Program`,
  `ProgramException`, `ProgramStateMachine`). `Program` ist eine abstrakte Basisklasse:
  `init()` legt die Instanz im Adressraum an. `start`/`halt`/`reset`/`suspend`/`resume`
  implementiert die Unterklasse als `async … -> str | None`; `None` heißt
  Zustandswechsel erfolgt, `str` heißt Ablehnung mit dieser Meldung.
  `ProgramStateMachine` bindet die festen ns=0-Zustands- und Übergangsknoten
  (`i=2406…2424`) an eine Instanz von `ProgramStateMachineType` (`ua.NodeId(2391,0)`),
  inklusive einer dokumentierten Umgehung eines asyncua-2.0.1-Fehlers bei
  `set_available_transitions()`.
- **`src/vision_server/vision_program.py`**: `class VisionProgram(Program)`. Verdrahtet
  Start/Halt/Reset/Suspend/Resume mit dem `JobRunner` aus `runner/install.py` und legt
  `ParameterSet`/`ResultSet` wie oben an.

Ein weiteres Teil-10-Programm für ein anderes Modul erbt von `ua_program.Program`.

---

## 5. Ablauf: einen Job fahren

**Die Reihenfolge ist bindend: erst abonnieren, dann Parameter schreiben, dann
`Start()`.** `Start` nimmt keine Argumente.

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
   |  5. read ResultSet/LatestResultJson               |  das eigentliche Ergebnis
```

> **Wichtig für einen reinen Part-10-Client.** Die 40100-Events erreichen ein Abo auf
> `VisionProgram` **nicht**. Events gehen nur an Abos auf dem emittierenden Knoten; die
> `HasNotifier`/`HasEventSource`-Hierarchie wird serverseitig nicht ausgewertet. Die
> 40100-Events zusätzlich auf `VisionProgram` zu emittieren würde jedes Ereignis doppelt
> liefern, deshalb unterbleibt es. Der vollständige Part-10-Weg ohne eine einzige
> 40100-NodeId lautet:
>
> 1. `ProgramTransitionEvent` auf `VisionProgram` abonnieren: sagt, **dass** der Job
>    fertig ist.
> 2. Wertänderung von `VisionProgram/ResultSet/LatestResultJson` abonnieren: liefert,
>    **was** erkannt wurde (Schema `wsc.vision.detections/1`).
> 3. Bei Fehler `ResultSet/ErrorCode` lesen.
>
> Wer die 40100-Events will, abonniert weiterhin `VisionMachine`. Beides geht nebeneinander.

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
| `Halted` | 11 | `i=2406` | Vision-System nicht betriebsbereit (z. B. Kamera nicht erreichbar) oder per `Halt` angehalten |
| `Ready` | 12 | `i=2400` | aufnahmebereit |
| `Running` | 13 | `i=2402` | ein Job läuft |
| `Suspended` | 14 | `i=2404` | **wird nie erreicht** |

Übergänge sind die Standardknoten `ns=0;i=2408` … `i=2424`.

### Welcher Knopf darf gedrückt werden

Der Server pflegt das **`Executable`-Attribut** jeder Methode. Das Frontend liest es und
graut Knöpfe danach aus, wie bei jedem anderen Modul.

| Zustand | ausführbar |
| --- | --- |
| `Halted` | `Reset` |
| `Ready` | `Start`, `Halt` |
| `Running` | `Halt`, `Suspend` |
| `Suspended` | `Halt`, `Resume` |

### `Suspend` / `Resume`

Existieren, weil Teil 10 sie vorschreibt, **lehnen aber immer ab**: Ein Vision-Job dauert
250 ms bis 20 s und lässt sich nicht sinnvoll pausieren. Der Aufruf ändert den Zustand
nicht und liefert ein Event mit Severity 500 und dem Text „Ein Vision-Job kann nicht
pausiert werden; Halt verwenden." Das Frontend sollte beide Knöpfe nicht anbieten.

---

## 6. Events

| | |
| --- | --- |
| Event-Typ | `ProgramTransitionEventType`, `ns=0;i=2378` |
| Emittierender Knoten | **`VisionProgram`** |

**Zwei Fallen:**

1. **Die Event-Typ-Liste ist zwingend.** `subscribe_events(prog)` ohne zweites Argument
   liefert Events, aber `FromState`, `ToState` und `Transition` sind dann `None`; nur
   `Message` ist gefüllt. Mit `subscribe_events(prog, [client.get_node(ua.NodeId(2378, 0))])`
   sind alle Felder da. Dieselbe Falle gibt es bei den 40100-Events.
2. **Auf `VisionProgram` abonnieren, nicht auf dem Server-Objekt.** asyncua bubbelt
   Events serverseitig nicht.

Felder eines erfolgreichen Jobs:

```
FromState=Ready    ToState=Running  Transition=ReadyToRunning  Message="ReadyToRunning"
FromState=Running  ToState=Ready    Transition=RunningToReady  Message="RunningToReady ; Job job-000001 abgeschlossen"
```

Bei einem **abgelehnten** Start gibt es keinen Zustandswechsel, sondern nur ein Event mit
`Severity=500`, z. B. `Message="Start abgelehnt: UNKNOWN_RECIPE (4)"`.

---

## 7. Fehler

`Start()` hat, wie alle Part-10-Programme, **keinen Rückgabewert**. Ob der Job angenommen
wurde, erfährt das Frontend auf zwei Wegen:

1. **Sofort:** Zustandswechsel nach `Running` heißt angenommen. Ein Event mit
   `Severity=500` heißt abgelehnt; der Grund steht in `Message`.
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

**Bewusste Abweichung von Teil 10:** Ein fehlgeschlagener Job (5, 6) führt zurück nach
`Ready`, nicht nach `Halted`. So ist nach einer misslungenen Erkennung kein `Reset`
nötig, und der 40100-Automat verhält sich ebenso. Nach `Halted` kommt man nur durch
**`Halt`** oder wenn das System beim Start gar nicht hochkam.

---

## 8. Das Ergebnis holen

Teil 10 definiert keinen Ergebnistransport. Gegenüber
[`vision-server-interface.md`](vision-server-interface.md) Abschnitt 6 ändert sich nichts:

- Der JSON-String steht in `ns=<vision>;s=VisionMachine.LatestResultJson`
  (unter `ResultSet` verlinkt, **derselbe Knoten**, nicht zwei).
- Schema `wsc.vision.detections/1`: `detections[]` mit `moduleId`, `position` (Meter),
  `orientation` (Quaternion `xyzw`), `frameId`, `frameConvention`, `attributes`.
- Zuordnung Ergebnis → Job über `payload["jobId"]`, abgleichbar mit `ResultSet/JobId`.
- Livestream über `VisionMachine.LatestCameraFrame` (Base64-JPEG).

---

## 9. Verfügbare Rezepte (`ParameterSet/RecipeId`)

Die zugelassenen Werte stehen in der **Description** des Knotens
`ParameterSet/RecipeId`. Ein generisches Frontend liest sie dort aus.

| `RecipeId` | Job |
| --- | --- |
| `"calibration"` | Messbereitschaft der Zelle prüfen |
| `"apriltag"` | **Module lokalisieren**, echte Posen aus der Kamera |

Eine nicht bekannte `RecipeId` ergibt `ErrorCode=4` (`UNKNOWN_RECIPE`); so meldet sich
z. B. ein Client, der noch das entfallene `image-recognition` anspricht.

Die Erkennung ändert an der Schnittstelle nichts: derselbe `Start()`, dasselbe
`ParameterSet`, dasselbe Payload-Schema. Nur sind die Werte in `detections[]` echt;
`attributes` trägt zusätzlich `tagId`, `reprojErrorPx`, `ambiguous`, `sampleCount`. Ein
AprilTag-Job dauert deutlich länger (mehrere Aufnahmen je Job), der Zustand bleibt
entsprechend länger auf `Running`.

---

## 10. Was Backend und Frontend konkret tun müssen

Nichts davon ist Pflicht, um weiterzulaufen. Alles ist Gewinn.

**Muss:**

- [ ] Keine festen Namespace-Indizes verwenden. Überall
      `get_namespace_index("http://launch-rm.de/vision")` nutzen (Abschnitt 3).

**Soll:**

- [ ] mDNS-Suche nach `_opcua-tcp._tcp.local.` einbauen; Endpoint-URL aus Adresse,
      Port und TXT-`path` zusammensetzen (Abschnitt 2).
- [ ] Hartcodierte Pi-Adressen entfernen (`pi_relay.py`, `piServers.ts`); manuelle
      Eingabe als Rückfall behalten.
- [ ] Eine **generische Programm-Komponente** bauen: Instanzen von
      `ProgramStateMachineType` finden, `ParameterSet` als Formular rendern,
      `Start`/`Halt`/`Reset` anbieten, Knöpfe nach `Executable` freigeben,
      `CurrentState` anzeigen, `ProgramTransitionEvent` abonnieren. Dieselbe Komponente
      bedient danach auch Conveyor und CardDispenser.
- [ ] Rezeptauswahl aus der Description von `ParameterSet/RecipeId` speisen.

**Kann bleiben, wie es ist:**

- [ ] Die gesamte 40100-Auswertung: `StartSingleJob`, `Stop`, `ResultReadyEvent`,
      `LatestResultJson`, `LatestCameraFrame`, Payload-Parser.

---

## 11. Stolpersteine

| # | Punkt |
| --- | --- |
| 11.1 | **Parameter vor dem Start schreiben.** `Start()` liest `ParameterSet` erst beim Aufruf. Wer die Reihenfolge dreht, startet mit den alten Werten. |
| 11.2 | **`ParameterSet` ist Zustand, kein Aufrufargument.** Zwei Clients, die gleichzeitig verschiedene Rezepte schreiben, überschreiben sich. Bei nur einem Frontend unkritisch; so ist die Konvention der Zelle. |
| 11.3 | **Nur ein Job gleichzeitig.** Ein zweiter `Start` wird mit `BUSY` (3) abgelehnt, egal ob über das Programm oder über `StartSingleJob`. Beide Oberflächen teilen sich denselben Job. |
| 11.4 | **Beide Oberflächen zeigen denselben Job.** Ein über `StartSingleJob` gestarteter Job setzt auch `VisionProgram` auf `Running` und am Ende zurück auf `Ready`. Das Frontend darf beides mischen. |
| 11.5 | **`Halt` ohne laufenden Job** ist erlaubt und geht trotzdem nach `Halted`. Zurück nur über `Reset`. |
| 11.6 | **`Start()` hat keinen Rückgabewert**, anders als `StartSingleJob` mit `(JobId, Error)`. Wer die JobId sofort braucht, liest `ResultSet/JobId` nach dem Zustandswechsel. |
| 11.7 | **`caps=DA`.** Wir kündigen `DA` an, wie Conveyor und CardDispenser, damit ein Filter uns nicht übersieht. |

---

## 12. Selbst ausprobieren

```bash
pip install -r requirements.txt          # enthält zeroconf

# Zellserver starten (VisionMachine + VisionProgram + mDNS + LDS)
python3 -m vision_server.server

# Auf dem Pi läuft er als Service:
sudo systemctl restart opcua-server.service
```

Im Netz suchen:

```bash
avahi-browse -rt _opcua-tcp._tcp
```

Mit UaExpert: `VisionProgram` browsen, `ParameterSet/RecipeId` auf `calibration`
schreiben, `Start` aufrufen, `CurrentState` beobachten.

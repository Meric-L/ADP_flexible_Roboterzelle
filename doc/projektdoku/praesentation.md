# AprilTag-Lokalisierung und OPC 40100 — Präsentationsgrundlage

Eine Folie je `##`-Abschnitt. Die Zeilen unter **Dazu sagen** sind Sprechnotizen,
nicht Folieninhalt. **Alle Zahlen in diesem Dokument sind gemessen**, nicht
geschätzt; wo etwas ungeprüft ist, steht es ausdrücklich dabei.

Quellen je Aussage: [`apriltag-lokalisierung.md`](apriltag-lokalisierung.md),
[`apriltag-referenz.md`](apriltag-referenz.md),
[`apriltag-e2e-test.md`](apriltag-e2e-test.md),
[`vision-server-interface.md`](vision-server-interface.md).

---

## 1 — Ziel

**Module, die mit AprilTags bestückt sind, in der Zelle lokalisieren.**

Zwei Kameras, zwei Raspberry Pis, ein gemeinsames Koordinatensystem.

> **Dazu sagen:** Das ist der praktische Teil der Arbeit. Der theoretische Teil
> vergleicht Lokalisierungsverfahren; hier geht es darum, eines davon so zu
> bauen, dass die Zelle es wirklich benutzen kann.

---

## 2 — Die Leitidee

> **Ein AprilTag trägt eine Nummer, keine Bedeutung.**

Die Kamera liefert immer nur eine Sache: `T_cam_tag` — wo der Tag relativ zur
Kamera steht, in Metern, mit voller Orientierung.

Dass Tag 7 das Modul `MOD-A` ist und dessen Ursprung 40 mm unter der Tag-Mitte
liegt, steht in **einer Datei**, nicht im Code.

> **Dazu sagen:** Das ist die Entscheidung, aus der alles andere folgt. Der Code
> kennt nur Tags, Posen und Verkettung. Ein neues Modul heißt: ein Eintrag in
> der Tag-Map, keine Codeänderung.

---

## 3 — Drei Rollen, ein Codepfad

| Rolle | Hardware | Pose |
|---|---|---|
| **Welt-Tag** | vier Tags im Randbereich der Zelle | **fest**, steht in der Karte |
| **Roboter-Tag** | Tags am Roboter | **beweglich — wird gemessen** |
| **Modul-Tag** | Tag am Modul | **beweglich — wird gemessen** |

Nur der Welt-Tag steht fest. Der Roboter ist ein Modul wie jedes andere — nur
dasjenige, das immer verwendet wird.

Referenz- und Mess-Tags durchlaufen denselben Code. Unterschied: steht die
Weltpose in der Karte oder als `null`?

---

## 4 — Die Kette

```
                  T_world_cam              T_cam_tag        T_tag_module
  Welt-KS  <───────────────────  Kamera-KS ──────────> Tag-KS ──────────> Modul-KS
              aus Referenz-Tags      gemessen           konstant (CAD)
              im selben Bild
```

```
T_world_module = T_world_cam · T_cam_tag · T_tag_module
```

**Genau eine Funktion im Code führt diese Verkettung aus** (`localize.locate_modules`).

> **Dazu sagen:** Einheiten- und Konventionsfehler — mm gegen m, xyzw gegen
> wxyz, `T_base_cam` gegen `T_cam_base` — sind der klassische stille Fehler in
> solchen Systemen. Eine einzige Stelle, die verkettet, macht ihn testbar.

---

## 5 — Layer 1 und Layer 2 sind dieselbe Funktionalität

| | Layer 1 — Decke | Layer 2 — Flansch |
|---|---|---|
| Aufgabe | grobe Modulposition | genaue 6-DOF-Pose |
| Kamera | fest montiert | bewegt sich mit dem Roboter |
| `T_world_cam` | aus Referenz-Tags im Bild | aus der Roboterpose |
| Ergebnis-`frameId` | `world` | `cam_flange` |

**Ein Profil, eine Klasse, zwei Konfigurationen.** Die Unterschiede sind
ausschließlich Konfiguration und die Herkunft **einer** Matrix.

---

## 6 — Architektur: geschnitten nach Abhängigkeiten

```
tagloc/
  identity, modes            nur Stdlib — nicht einmal numpy
  geometry, calibration,
  tagmap, localize           numpy, kein OpenCV
  boards, detector, pose,
  overlay, frames            OpenCV, aber erst in der Funktion importiert
  cli/                       vier Werkzeuge
```

Harte Regel, **im Test nachgemessen** (`tests/test_layering.py`, prüft
`sys.modules` im Unterprozess):

- Der Server bildet die `configurationId`, bevor irgendetwas geladen ist
- Die Rechenschicht ist unabhängig von der installierten OpenCV-Version
- `tagloc` importiert **nirgends** aus `vision_server`

---

## 7 — Tags in ein gemeinsames Koordinatensystem bringen

Zwei Tags in **einem** Bild ergeben eine kamerafreie Relation:

```
T_a_b = T_cam_a⁻¹ · T_cam_b        die Kamerapose kürzt sich heraus
```

Diese Relationen spannen einen Graphen auf; eine Breitensuche vom Anker
verkettet sie zu einer Karte.

**`residuals()` liefert die Schließfehler je Kante — die Qualitätszahl der Karte.**

> **Dazu sagen:** Deshalb muss man nicht jeden Tag einzeln einmessen. Man
> fotografiert die Zelle so, dass sich benachbarte Tags paarweise überlappen.
> Schließt sich der Ring nicht, stimmt die Kalibrierung oder eine Tag-Größe.

---

## 8 — Gemessen: die Kette gegen bekannte Wahrheit

Synthetisch gerenderte Szene, wahre Posen exakt bekannt:

| Größe | gemessen | Schranke |
|---|---|---|
| Position | max **0,29 mm** | 2 mm |
| Winkel | Median **0,031°**, max 0,50° | 1° |
| Reprojektionsfehler | max **0,063 px** | 1 px |
| Schließfehler der Karte | **0,00–0,02 mm / 0,000°** | 1 mm / 0,05° |

> **Dazu sagen:** Der eine Winkel-Ausreißer bei 0,50° ist keine Mehrdeutigkeit —
> die zweite IPPE-Lösung ist dort 82-fach schlechter. Es ist die
> Pixelquantisierung des gerenderten Markers bei 34° Schrägblick. Die Schranke
> steht deshalb begründet auf 1°, nicht auf den 0,5° aus dem Konzept.

---

## 9 — OPC 40100: die beiden Teile

| | **Part 1** (40100-1) | **Part 2** (40100-2) |
|---|---|---|
| Frage | *Wie bediene ich das System?* | *Woraus besteht es, wie geht es ihm?* |
| Inhalt | Zustandsautomaten, Jobs, Rezepte, Ergebnisse | Kamera, Objektiv, Recheneinheit, Zustand |
| Zielgruppe | Zelle, SPS, MES | Service und Instandhaltung |
| Version bei uns | 1.0.0 (2019) | 1.00.0 (17.05.2024) |

> **Häufiges Missverständnis:** Das „NodeSet2" im Dateinamen ist das
> **XML-Dateiformat**, nicht die Teilnummer. Jede Companion Spec liefert
> `*.NodeSet2.xml` aus — auch eine reine Part-1-Datei.

---

## 10 — Part 1: was verlinkt ist

Das Nodeset bringt **alle** Methoden mit der Typdefinition mit. Ob sie etwas
tun, entscheidet `link_method`.

**Verlinkt:** `StartSingleJob`, `StartContinuous`, `Stop`, `Abort`, `Halt`, `Reset`

**Bewusst nicht**, jeweils mit Begründung dokumentiert:

| Methode | Warum nicht |
|---|---|
| `SimulationMode` | das Profil `hello_world` *ist* bereits die simulierte Quelle |
| `SelectModeAutomatic` | es gibt nur eine Betriebsart |
| Configuration- / RecipeManagement | setzt ein Rezept-Datenmodell voraus, das die Zelle nicht hat |

> **Dazu sagen:** Eine verlinkte Methode, die nichts tut, wäre schlimmer als
> eine erkennbar nicht verlinkte — die antwortet ehrlich `BadNothingToDo`.

---

## 11 — Part 2: die Anlagensicht

```
ns=<vision>;s=VisionMachine.VisionAsset
├── Identification        Hersteller, Modell, Seriennummer, Softwarestand
├── ComputingDevices/ComputingDevice
├── ImageSensors/ImageSensor
└── Lenses/Lens
```

Angelegt wird nur, was wirklich verbaut ist. **Ein leeres Modellfeld heißt
„nicht bekannt" und erzeugt keinen Eintrag** — in einer Instandhaltungssicht ist
eine Lücke ehrlicher als ein erfundenes Modell.

---

## 12 — Part 2: erst gemessen, dann gebaut

Part 2 bringt **DI 1.04.0** und **Machinery 1.03.0** mit — vier Nodesets statt
einem.

| | RSS | Startzeit |
|---|---|---|
| nur Part 1 | 108,5 MB | 0,65 s |
| mit Part 2 | 121,3 MB | +1,6 s |
| Anlagensicht instanziiert | +2,9 MB | |

**Rund 16 MB und knapp zwei Sekunden.** Abschaltbar: ohne `assets` wird nichts
davon geladen.

> **Dazu sagen:** Der Pi bedient nebenher die Kamera, deshalb war die erste
> Frage nicht „wie baue ich das", sondern „passt das überhaupt". Das Messwerkzeug
> liegt im Repo und läuft auf dem Pi genauso.

---

## 13 — Was die Messung an Fallen aufgedeckt hat

| Fund | Konsequenz |
|---|---|
| **DI 1.05.0 lässt sich nicht importieren** — fordert UA-Basis 1.05.04, scheitert an asyncua mit `BadParentNodeIdInvalid` | Versionen exakt auf die `RequiredModel`-Angaben gepinnt |
| **asyncua legt Platzhalter als echte Knoten an** (`<VisionItem>`, Modelling Rule `MandatoryPlaceholder`) | nachträgliches Löschen kostete **9 s für 26 Knoten** → jetzt wird nur aufgebaut, was gefüllt wird: ~50 statt ~700 Knoten |
| **Der Namensraumindex verschiebt sich** von 3 auf 6 | Clients müssen ihn zur Laufzeit auflösen |

---

## 14 — Drei Fehler, die wir unterwegs gefunden haben

**1. Numerische NodeIds im Livestream.** Die Stream-Knoten bekamen automatisch
vergebene Ids (`ns=3;i=1`). Die verschieben sich, sobald jemand einen Knoten
davor anlegt — und das Backend abonniert sie fest. Jetzt explizite String-Ids.

**2. NaN hätte den Job getötet.** `nan > schranke` ist in Python `False`: der
Qualitätsfilter ließ entartete Posen durch, und `payload` serialisiert mit
`allow_nan=False`. Der Job wäre nicht als `DETECTION_FAILED` gescheitert,
sondern als `INTERNAL`.

**3. Das Frontend bot einen Job an, den es nicht mehr gibt.** `image-recognition`
stand nach dem QR-Ausbau weiter in der Liste und endete in `Error=4`.

> **Dazu sagen:** Alle drei wären im Betrieb schwer zu finden gewesen — sie
> scheitern still oder mit irreführender Meldung.

---

## 15 — Livestream als Debugwerkzeug

Umschaltbar über einen beschreibbaren OPC-UA-Knoten:

| Modus | Was man sieht |
|---|---|
| `off` | Rohbild |
| `apriltag` | Umriss, **Achsenkreuz im Tag**, ID, Modulname, Distanz, Reprojektionsfehler |
| `calibration` | gefundene Board-Ecken und Bildabdeckung |

Standardmäßig **aus** und hinter den Settings versteckt: er kostet auf dem Pi
Rechenzeit und gehört nicht dauerhaft ins Bild.

> **Dazu sagen:** Das Achsenkreuz ist die eigentliche Information — ein
> verdrehtes Kreuz fällt sofort auf, eine falsche Zahl in einer Tabelle nicht.

---

## 16 — Stand der Prüfung

| | |
|---|---|
| Tests gesamt | **204, grün, kein Skip** |
| davon Mathematik mit Golden Values | Verkettung, Quaternionen, Mittelung |
| End-to-End auf gerendertem Bild | Detektor → Pose → Karte → Modulpose |
| Architekturregeln | im Unterprozess an `sys.modules` gemessen |

**Ungeprüft:** alles, was echte Hardware braucht. Die Zahlen sind
Desktop-Zahlen; auf dem Pi läuft OpenCV 4.x, wofür Laufzeitweichen existieren,
die dort noch niemand ausgeführt hat.

---

## 17 — Offene Punkte

| Punkt | Stand |
|---|---|
| **Hand-Auge-Kalibrierung** für Layer 2 | offen — bis dahin liefert Layer 2 im Kamera-KS, `auto_execute` bleibt `False` |
| Abnahme an der Pi-Kamera | Anleitung steht (`apriltag-e2e-test.md`, vier Stufen mit Abbruchkriterien) |
| Übergabeformat Layer 1 → Layer 2 | Vorschlag: 3D-Pose im Welt-KS, Schema bleibt unverändert |
| Stream über 5 fps | ginge nur mit anderem Transport — OPC UA deckelt bei 10 fps |

> **Dazu sagen:** Die Hand-Auge-Kalibrierung ist die einzige offene Frage, die
> Code nach sich zieht. Alles andere sind Entscheidungen, keine Untersuchungen.

---

## 18 — Wo alles steht

| Dokument | Inhalt |
|---|---|
| `doc/apriltag-lokalisierung.md` | Konzept und Umsetzungsplan |
| `doc/apriltag-referenz.md` | Funktions- und CLI-Referenz, Dateiformate |
| `doc/apriltag-e2e-test.md` | Testanleitung bis zur Pi-Kamera |
| `doc/vision-server-interface.md` | OPC-UA-Schnittstelle, Part 1 und Part 2 |
| `doc/altlasten.md` | was warum noch offen ist |
| `src/vision_server/nodesets/README.md` | warum die Nodeset-Versionen gepinnt sind |

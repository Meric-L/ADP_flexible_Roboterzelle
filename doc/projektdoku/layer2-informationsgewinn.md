# Layer 2: Messversuch zum Informationsgewinn durch mehrere Ansichten

Der Hand-Pi mittelt in `MeasureTags` standardmäßig drei frische Bilder derselben Kamerapose. Die Steuerung mittelt danach Tag-Posen aus verschiedenen Roboterstellungen. Die bisher angezeigte Streuung um den Mittelwert misst Konsistenz, aber keine Genauigkeit: ein gemeinsamer Kalibrierfehler kann in allen Ansichten gleich auftreten.

## Versuchsfrage und Vergleich

Bei **demselben festen Tag** und derselben Kalibrierung werden pro Trial drei Ansichten mit jeweils drei PNG-Rohbildern aufgenommen. Der Offline-Auswerter verwendet für alle Strategien dieselben gespeicherten Bilder:

| Strategie im Bericht | Bilder | Zweck |
| --- | --- | --- |
| `ein_bild` | erstes Bild der ersten Ansicht | Ausgangswert |
| `drei_bilder` | drei Bilder der ersten Ansicht | Gewinn durch zeitliches Mitteln |
| `drei_ansichten_je_ein_bild` | erstes Bild jeder Ansicht | Gewinn durch Blickrichtungswechsel bei **gleichem Budget von drei Bildern** |
| `drei_ansichten` | drei Bilder jeder Ansicht | heutiges Verfahren mit neun Bildern |

Die Tag-Position wird über alle erkannten Bilder gemittelt. Orientierungen werden nur aus nicht mehrdeutigen Lösungen gemittelt; ohne solche Lösung bleibt der Orientierungsfehler leer. Eine Ansicht mit drei Bildern gilt erst ab zwei Tag-Erkennungen als gültig, wie in der Steuerung. Für `drei_ansichten` müssen alle drei Ansichten gültig sein. Fehlende Erkennung senkt die Erfolgsquote und wird nicht durch eine andere Messung ersetzt.

## Aufnahme an der Zelle

1. Einen festen, nicht bewegten Tag wählen. Seine reale 6D-Pose `T_base_tag` **unabhängig** bestimmen, etwa mit kalibriertem Messgerät und bekannten Bezugspunkten. Verfahren, Unsicherheit und Datum im Versuchsprotokoll festhalten. Den Katalogwert oder den Mittelwert der Versuchsbilder nicht als Referenz verwenden. Ohne unabhängige Referenz liefert das Skript ausschließlich Wiederholbarkeit.
2. Die aktuelle `cam_flange.json` und Hand-Auge-Datei in den Session-Ordner kopieren. Tag-Größe mit Messschieber prüfen. Belichtung, Auflösung, Beleuchtung, Kalibrierung, Detektorparameter und Tag über den ganzen Versuch konstant halten. Ein `placeholder`-Kalibrierstand ist ungeeignet.
3. Mindestens zehn unabhängige Trials aufnehmen. Je Trial die drei vorgesehenen Blickrichtungen (z. B. frontal und ±12°) anfahren. Die Reihenfolge der **physischen** Blickrichtungen über Trials wechseln, damit Drift nicht immer der ersten Ansicht zugeschrieben wird. Nach jeder Fahrt vollständigen Stillstand abwarten, Ist-Gelenke lesen und daraus per FK `T_base_flange` berechnen. Die tatsächliche Pose als JSON-Datei sichern; Sollpose ist ungeeignet.
4. Auf dem **Hand-Pi** bei jeder stillstehenden Ansicht `layer2_capture_view.py` aufrufen. Es ruft dreimal die vorhandene OPC-UA-Methode `SaveRawImage` auf, kopiert die PNGs in `images/` und trägt Bildpfade und Ist-Flanschpose in die Session ein. Zwischen Aufrufen liegen standardmäßig 0,5 s. Die Bilder werden später direkt erneut erkannt; sie müssen daher nicht zeitgleich mit einer separaten `MeasureTags`-Anfrage sein.
5. Nach dem Versuch den Session-Ordner auf den Auswertungsrechner kopieren. Der Ordner gehört unter `data/` und ist nicht für einen Git-Commit vorgesehen. Bilddateien und Posen dürfen nach der Aufnahme nicht ausgetauscht werden.

Session-Startdatei `data/layer2-information/versuch-01/session.json`:

```json
{
  "schema": "adp.layer2.information-gain/1",
  "tagId": 7,
  "tagSizeM": 0.05,
  "tagFamily": "tag36h11",
  "detectorBackend": "aruco",
  "subpixelCornerRefinement": false,
  "cornerRefinementMethod": "subpix",
  "apriltagQuadDecimate": 0.0,
  "maxReprojectionErrorPx": 3.0,
  "calibration": "cam_flange.json",
  "handEye": "hand_eye.json",
  "referenceTagInBase": {
    "position": [0.5, 0.0, 0.4],
    "orientation": [0, 0, 0, 1]
  },
  "trials": []
}
```

`referenceTagInBase` weglassen, wenn keine unabhängige Referenz vorliegt. Die Beispielpose ist **kein** realer Messwert. `hand_eye.json` enthält `poseFlangeCam` im vorhandenen Format. Die Pose-Datei je Ansicht enthält nur `{"position":[x,y,z],"orientation":[qx,qy,qz,qw]}`. Alle Längen sind Meter, Quaternionen xyzw. Für den Replay müssen Detektor-Backend, Tag-Familie, Eckenverfeinerung, Reprojektionsgrenze und optionale `detectorParameters` mit dem Serverprofil übereinstimmen. Die Bildgröße muss exakt zur Kamera-Kalibrierung passen.

Beispielaufruf auf dem Hand-Pi, nachdem die erste Ansicht von Trial 1 steht und `pose-ist.json` aus Ist-Gelenken berechnet wurde:

```bash
.venv/bin/python tools/layer2_capture_view.py data/layer2-information/versuch-01/session.json \
  --trial 1 --view 1 --pose-file pose-ist.json \
  --endpoint 'opc.tcp://127.0.0.1:4840/vision/' \
  --machine-node 'ns=2;s=VisionMachine' \
  --method-node 'ns=2;s=VisionMachine.SaveRawImage' \
  --repo-root "$PWD"
```

Endpoint und Namespace-Index aus der laufenden Serverkonfiguration ablesen. Bei jedem weiteren Blick `--view 2` bzw. `3`, dann `--trial 2`. Der Helfer überschreibt vorhandene Bildnamen nicht. Falls ein Aufruf abbricht, die bereits kopierten Bilder prüfen und erst danach dieselbe Ansicht erneut aufnehmen.

## Auswertung

```bash
PYTHONPATH=src .venv/bin/python tools/layer2_information_gain.py \
  data/layer2-information/versuch-01/session.json \
  --out data/layer2-information/versuch-01/bericht.json
```

Das Ergebnis enthält je PNG SHA-256, Erkennung, Mehrdeutigkeit und Reprojektionsfehler; je Trial die vier Posen und gegebenenfalls den Positionsfehler in mm sowie den Orientierungsfehler in Grad. `summary` nennt Erfolgsquoten, Median, Mittel und 90. Perzentil. `pairedGain` ist **Fehler der Ausgangsstrategie minus Fehler der neuen Strategie** auf denselben Trials; positive Werte bedeuten Verbesserung. `mean95Ci` ist ein reproduzierbares Bootstrap-Intervall für die mittlere gepaarte Differenz (5000 Ziehungen über ganze Trials, bei mindestens zwei Trials). Besonders aussagekräftig ist `ansichten_gegen_drei_bilder_gleiches_budget`: drei Blickrichtungen gegen drei Bilder am selben Ort, jeweils mit insgesamt drei Bildern. Liegt dessen Intervall vollständig über null, stützen die erhobenen Trials einen echten Gewinn durch verschiedene Ansichten; bei einem Intervall über null hinaus und darunter bleibt die Aussage offen. Fehlende Ergebnisse werden in der Erfolgsquote sichtbar und in gepaarten Fehlervergleichen nicht als Nullfehler behandelt.

Ohne Referenz enthält `summary` nur die Abweichung der wiederholten Trial-Ergebnisse von ihrem gemeinsamen Mittel. Das ist Wiederholbarkeit und darf nicht als Informationsgewinn oder absolute Genauigkeit bezeichnet werden. Auch mit Referenz gelten Aussagen nur für den geprüften Tag, den Abstand, die Blickwinkel und die Kalibrierung. Für eine belastbare Aussage weitere Tags, Abstände und Beleuchtungen als getrennte Sessions wiederholen.

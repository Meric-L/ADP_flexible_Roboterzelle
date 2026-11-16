# Vision-System — Stand & nächste Schritte (für Agenten)

Nur die offenen Punkte, je mit nächstem Schritt. Ist-Stand: [`vision-system.md`](vision-system.md).
Backend-Schnittstelle: [`vision-server-interface.md`](vision-server-interface.md). Zielbild und
WSC-Anbindung: [`vision-system-integration.md`](vision-system-integration.md).

Erledigt und damit nicht mehr aufgeführt: Adressraum-Import, Zustandsautomaten und `StartSingleJob`,
echte AprilTag-Erkennung (`detection/apriltag.py`, Rezept `apriltag`), Hand-Auge-Verkettung, Welttag-Konzept,
Livestream, interaktive Kalibrierung (`StartCalibration`, `CaptureCalibrationSample`, `FinishCalibration`,
`AbortCalibration`), Part 2 (`asset_model.py`, `VisionAsset`), Part-10-Fassade (`vision_program.py`,
`src/ua_program/program.py`), mDNS/LDS, Entfernung der Altlasten (`2:VisionSystem`, Nodeset-Ablage).

## Offene Punkte

1. **Auf dem Pi durchspielen.** Bisher nur lokal gegen asyncua 2.0.1 geprüft. `requirements.txt` pinnt nur
   `asyncua>=1.1.5`, auf dem Pi kann eine ältere Version liegen.
   *Nächster Schritt:* `git pull`, `systemctl restart opcua-server.service`, dann den Testclient gegen
   `opc.tcp://<pi>:4840/raspi/server/` (Befehl in [`vision-system.md`](vision-system.md), Abschnitt Starten).
   Bei Abweichung die asyncua-Version prüfen (`pip show asyncua`) und ggf. die Mindestversion in
   `requirements.txt` anheben.

2. **Gemeinsamer Test mit dem Backend-Team.** Die Schnittstelle einmal end-to-end durchspielen.
   *Nächster Schritt:* Grundlage ist [`vision-server-interface.md`](vision-server-interface.md). Prüfpunkte:
   Event-Typliste im Abo (§7.1), Abo auf `VisionMachine` statt auf dem Server-Objekt (§7.2), Payload (§6).

3. **Hand-Auge-Datei je Zelle vermessen und verifizieren.** Die Implementierung ist fertig
   (`AprilTagProfileConfig.hand_eye_path`, Arbeitsplan
   [`arbeitsplaene/apriltag-hand-auge-ankern.md`](arbeitsplaene/apriltag-hand-auge-ankern.md)). Ein Flag
   `auto_execute` gibt es im Code nicht; das Backend entscheidet anhand der gelieferten Posen und
   `anchorDriftM`/`anchorDriftDeg` selbst.
   *Nächster Schritt:* Hand-Auge-Kalibrierung je Zelle nach
   [`vision-server-interface.md`](vision-server-interface.md) Abschnitt 15 durchführen, Gütezahlen prüfen, die
   Datei unter `hand_eye_path` ablegen. Bis dahin bleibt das automatische Anfahren im Backend aus.

4. **Koordinatensystem-Konventionen klären.** Größtenteils geklärt durch das Welttag-Konzept
   ([`arbeitsplaene/apriltag-welttag-konzept.md`](arbeitsplaene/apriltag-welttag-konzept.md),
   [`apriltag-lokalisierung.md`](apriltag-lokalisierung.md)). Verbleibend: Kette Welt-Tag → Kamera → Roboter,
   Format der Layer-1→Layer-2-Übergabe, Repositionierungsstrategie.
   *Nächster Schritt:* die Punkte in [`../../concept/offene_punkte.md`](../../concept/offene_punkte.md)
   entscheiden. Sie bestimmen, was `frameId`, `position` und `orientation` im Payload bedeuten; danach
   Abschnitt 6 von [`vision-server-interface.md`](vision-server-interface.md) anpassen.

5. **Deckenkamera: Auflösung 2028×1520.** Die Kalibrierung für 2028×1520 wird bis zur Neukalibrierung
   hochgerechnet (`allow_resolution_mismatch` im Preset `cam_ceiling`).
   *Nächster Schritt:* bei 4056×3040 neu kalibrieren
   ([`arbeitsplaene/deckenkamera-volle-aufloesung.md`](arbeitsplaene/deckenkamera-volle-aufloesung.md)),
   danach den Schalter `allow_resolution_mismatch` entfernen.

6. **Kamera-Controls für die Deckenkamera festlegen.** Das Preset `PI_CAMERA_STREAM_PRESETS["cam_ceiling"]["camera_controls"]`
   ist leer; Setzen über `VISION_CAMERA_CONTROLS`.
   *Nächster Schritt:* nach dem Warmup die Metadaten auslesen
   (`journalctl -u opcua-server.service | grep "Kamera-Metadaten"`), feste Belichtungswerte wählen und ins Preset
   eintragen oder per Drop-In setzen ([`arbeitsplaene/kamera-controls-und-detektor-parameter.md`](arbeitsplaene/kamera-controls-und-detektor-parameter.md)).

7. **Strukturtypisierte Event-Felder befüllen.** Blockiert durch asyncua-Issue #1693.
   *Nächster Schritt:* bei einem Upstream-Fix `ResultId`, `JobId` u. a. als Strukturfelder setzen. Das
   JSON-Payload (`wsc.vision.detections/1`) bleibt auch dann die maßgebliche Quelle.

8. **Zweites Vision-Profil (3D).** Nicht begonnen. Geplant als zweite `VisionSystemType`-Instanz im selben
   Server mit eigenem Instanznamen, eigener `visionSystemId` und eigener String-NodeId. Gleiche Schnittstelle
   wie 2D. Unterschiede: volle 6 DOF statt x/y/yaw, zusätzlich `StartContinuous`/`Stop`/`Abort`, Teilergebnisse
   (`IsPartial`), Zusatzfelder `fitError`, `inlierCount`, `pointCount`. Ein zweiter Serverprozess ist nicht
   vorgesehen. *Nächster Schritt:* Profil und Abgrenzung festlegen, dann die zweite Instanz einbauen.

9. **Dispatch-Modus (WSC-Seite).** Offen ist, wie eine erkannte Pose zur Roboterbewegung wird: `cartesian`
   (über `LinMoveTcp`/`RotMoveTcp`) oder `frontendIk`. Betrifft das Backend, nicht diesen Server.
   *Nächster Schritt:* prüfen, ob der reale Robotics-Server `LinMoveTcp`/`RotMoveTcp` exponiert (Address-Space-Browser
   oder Log-Zeile `discovered robot … skillNames=`), dann entscheiden. Details:
   [`vision-system-integration.md`](vision-system-integration.md).

10. **Reconnect und Watchdog (WSC-Seite).** Asyncua-Subscriptions sterben bei Verbindungsabriss lautlos. Für v1
    nicht enthalten, vor Zellenbetrieb zwingend. *Nächster Schritt:* im Backend Verbindungsabriss erkennen,
    Verbindung und Subscriptions neu aufbauen. Details: [`vision-system-integration.md`](vision-system-integration.md).

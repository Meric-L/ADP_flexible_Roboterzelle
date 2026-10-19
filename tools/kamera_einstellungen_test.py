"""Einmal-Werkzeug: Kamera-Einstellungen der Deckenkamera durchprobieren.

Nimmt je Einstellung ein Vollbild (4056x3040, verlustfrei als PNG) auf und
legt es samt Metadaten unter `data/rohbilder/camera_settings_test/<Zeit>/` ab.
Die Bilder werden danach am PC ausgewertet (Schaerfe, Ausbrennen, erkannte
Tags); die beste Einstellung kommt dann ins Preset `cam_ceiling`
(`PI_CAMERA_STREAM_PRESETS["camera_controls"]` in `vision_server/server.py`).
Nur auf dem Branch `kamera-einstellungen-test`, wird danach nicht mehr
gebraucht.

Auf dem Pi (der Server haelt die Kamera, deshalb vorher stoppen):

    sudo systemctl stop opcua-server.service
    cd ~/ADP_flexible_Roboterzelle   # Pfad des Repos auf dem Pi
    python3 tools/kamera_einstellungen_test.py
    sudo systemctl start opcua-server.service

Optionen: `--liste` zeigt nur die Einstellungen, `--nur 00 03 11` nimmt nur
diese auf, `--ohne-erkennung` spart die Tag-Erkennung je Bild (~2 s auf dem Pi).

Jede Einstellung oeffnet die Kamera neu, genau wie der Server
(`SharedCamera._open_picamera2`: Controls in der Video-Konfiguration). So
bleibt keine Einstellung der vorigen haengen -- libcamera behaelt gesetzte
Controls sonst bis zum Schliessen -- und nebenbei ist der neue Weg fuer
`camera_controls` am echten Pi geprueft.
"""

import argparse
import dataclasses
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from vision_server.camera import (  # noqa: E402
    CAMERA_METADATA_KEYS,
    picamera2_video_configuration,
    resolve_camera_controls,
    validate_camera_controls,
)
from vision_server.server import vision_config  # noqa: E402

AUSGABE = REPO_ROOT / "data" / "rohbilder" / "camera_settings_test"

#: Wartezeit nach dem Start, wie `CameraStreamConfig.warmup_s` im Server.
WARMUP_S = 2.0
#: Belichtungsautomatik gilt als eingeschwungen, wenn Belichtungszeit und
#: Gain sich ueber so viele Frames um weniger als `STABIL_TOLERANZ` aendern.
STABIL_FRAMES = 3
STABIL_TOLERANZ = 0.03
STABIL_MAX_S = 6.0

#: Name -> Controls. Jede Zeile aendert moeglichst nur eine Stellschraube
#: gegenueber der Automatik (00), damit sich die Wirkung zuordnen laesst.
#: "MANUELL" wird nach Bild 00 aus dessen Belichtungswerten berechnet.
EINSTELLUNGEN: list[tuple[str, dict]] = [
    ("00_auto", {}),
    # Belichtung runter: Weiss brannte bei Tag 1 zu 17 % aus.
    ("01_ev_minus_0.5", {"ExposureValue": -0.5}),
    ("02_ev_minus_1.0", {"ExposureValue": -1.0}),
    # ISP-Schaerfe: steilere Kanten, kostet keine CPU.
    ("03_schaerfe_1.5", {"Sharpness": 1.5}),
    ("04_schaerfe_2.5", {"Sharpness": 2.5}),
    ("05_kontrast_1.25", {"Contrast": 1.25}),
    # Rauschunterdrueckung: Video-Standard ist "Fast".
    ("06_rauschen_aus", {"NoiseReductionMode": "Off"}),
    ("07_rauschen_hq", {"NoiseReductionMode": "HighQuality"}),
    # Messfeld: das Fischauge sieht helle Fenster am Rand.
    ("08_messung_matrix", {"AeMeteringMode": "Matrix"}),
    # Feste Belichtung (siehe `manuelle_einstellungen`).
    ("09_fest_wie_auto", "MANUELL"),
    ("10_fest_kurz", "MANUELL"),
    ("11_fest_dunkler", "MANUELL"),
    # Kombination der vermutlich besten Einzelwerte.
    (
        "12_kombi",
        {"ExposureValue": -0.5, "Sharpness": 1.5, "Contrast": 1.15, "NoiseReductionMode": "Fast"},
    ),
    # Wiederholung von 00: hat sich das Licht waehrend des Laufs geaendert?
    ("99_auto_kontrolle", {}),
]


def manuelle_einstellungen(belichtung_us: int, gain: float, max_gain: float) -> dict[str, dict]:
    """Feste Belichtungen aus den Automatik-Werten von Bild 00.

    - fest_wie_auto: dieselbe Helligkeit, aber eingefroren (gleich hell in
      jedem Bild, reproduzierbar).
    - fest_kurz: halbe Zeit, doppelter Gain -- gleiche Helligkeit, weniger
      Bewegungsunschaerfe, mehr Rauschen.
    - fest_dunkler: 60 % der Zeit bei gleichem Gain, etwa -0,7 EV fest.
    """
    return {
        "09_fest_wie_auto": {"AeEnable": False, "ExposureTime": belichtung_us, "AnalogueGain": gain},
        "10_fest_kurz": {
            "AeEnable": False,
            "ExposureTime": max(1, belichtung_us // 2),
            "AnalogueGain": min(gain * 2.0, max_gain),
        },
        "11_fest_dunkler": {
            "AeEnable": False,
            "ExposureTime": max(1, int(belichtung_us * 0.6)),
            "AnalogueGain": gain,
        },
    }


def ae_kompatibel(controls: dict, verfuegbar: dict) -> dict:
    """`AeEnable` fuer neuere libcamera-Versionen uebersetzen.

    Dort gibt es `AeEnable` nicht mehr, sondern `ExposureTimeMode` und
    `AnalogueGainMode` (0 = Automatik, 1 = manuell).
    """
    if "AeEnable" not in controls or "AeEnable" in verfuegbar:
        return controls
    controls = dict(controls)
    modus = 0 if controls.pop("AeEnable") else 1
    for name in ("ExposureTimeMode", "AnalogueGainMode"):
        if name in verfuegbar:
            controls[name] = modus
    return controls


def warte_auf_stabile_belichtung(kamera) -> dict:
    """Metadaten holen, bis Belichtungszeit und Gain sich nicht mehr bewegen."""
    time.sleep(WARMUP_S)
    verlauf = []
    ende = time.monotonic() + STABIL_MAX_S
    while time.monotonic() < ende:
        metadaten = kamera.capture_metadata()
        verlauf.append((metadaten.get("ExposureTime", 0), metadaten.get("AnalogueGain", 0.0)))
        letzte = verlauf[-STABIL_FRAMES:]
        if len(letzte) == STABIL_FRAMES and all(
            abs(e - letzte[-1][0]) <= STABIL_TOLERANZ * max(letzte[-1][0], 1)
            and abs(g - letzte[-1][1]) <= STABIL_TOLERANZ * max(letzte[-1][1], 1e-6)
            for e, g in letzte
        ):
            return metadaten
    print("  Hinweis: Belichtung nicht eingeschwungen, nehme trotzdem auf")
    return metadaten


def aufnehmen(kamera_config, controls: dict):
    """Kamera mit diesen Controls oeffnen, Vollbild + Metadaten holen, schliessen."""
    from libcamera import controls as libcamera_controls
    from picamera2 import Picamera2

    kamera = Picamera2()
    try:
        verfuegbar = kamera.camera_controls
        argumente = picamera2_video_configuration(
            dataclasses.replace(kamera_config, camera_controls={}, preview_resolution=None)
        )
        if controls:
            argumente["controls"] = resolve_camera_controls(
                validate_camera_controls(ae_kompatibel(controls, verfuegbar), verfuegbar),
                libcamera_controls,
            )
        kamera.configure(kamera.create_video_configuration(**argumente))
        kamera.start()
        warte_auf_stabile_belichtung(kamera)
        request = kamera.capture_request()
        try:
            bild = request.make_array("main")  # bereits BGR, siehe PICAMERA2_MAIN_FORMAT
            metadaten = request.get_metadata()
        finally:
            request.release()
        max_gain = verfuegbar.get("AnalogueGain", (1.0, 16.0, 1.0))[1]
        return bild, metadaten, max_gain
    finally:
        kamera.close()


def kennzahlen(bild, erkennen: bool, detector_parameters: dict) -> dict:
    """Schnelle Kennzahlen fuer die Uebersicht; die Auswertung passiert am PC."""
    import cv2

    grau = cv2.cvtColor(bild, cv2.COLOR_BGR2GRAY)
    werte = {
        "mittelwert": round(float(grau.mean()), 1),
        "anteil_ausgebrannt_pct": round(float((grau >= 250).mean() * 100), 2),
        "laplace_varianz": round(float(cv2.Laplacian(grau, cv2.CV_64F).var()), 1),
    }
    if erkennen:
        from tagloc.detector import build_detector

        detektor = build_detector("tag36h11", "aruco", detector_parameters=detector_parameters)
        werte["tag_ids"] = [o.tag_id for o in detektor.detect(grau)]
    return werte


def metadaten_json(metadaten: dict) -> dict:
    """Nur die einfachen Werte, die sich als JSON schreiben lassen."""
    return {
        key: value
        for key, value in metadaten.items()
        if isinstance(value, (int, float, str, bool))
        or (isinstance(value, (list, tuple)) and len(value) <= 16)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--liste", action="store_true", help="nur die Einstellungen zeigen")
    parser.add_argument("--nur", nargs="+", metavar="NR", help="nur diese Nummern (z. B. 00 03)")
    parser.add_argument("--ohne-erkennung", action="store_true", help="keine Tag-Erkennung je Bild")
    args = parser.parse_args()

    if args.liste:
        for name, controls in EINSTELLUNGEN:
            print(f"{name:22} {controls if controls != 'MANUELL' else 'aus Bild 00 berechnet'}")
        return 0

    import cv2

    config = vision_config()
    kamera_config = config.camera_stream
    detector_parameters = dict(config.apriltag.detector_parameters) if config.apriltag else {}
    einstellungen = EINSTELLUNGEN
    if args.nur:
        einstellungen = [e for e in EINSTELLUNGEN if e[0].split("_")[0] in args.nur]
        if any(c == "MANUELL" for _, c in einstellungen) and not any(
            n.startswith("00_") for n, _ in einstellungen
        ):
            # Die festen Belichtungen brauchen die Automatik-Werte von Bild 00.
            einstellungen = [EINSTELLUNGEN[0], *einstellungen]

    ordner = AUSGABE / datetime.now().strftime("%Y%m%d-%H%M%S")
    ordner.mkdir(parents=True, exist_ok=True)
    print(f"Rahmen {config.frame_id}, Aufloesung {tuple(kamera_config.resolution)} -> {ordner}")

    manuell: dict[str, dict] = {}
    uebersicht = []
    for name, controls in einstellungen:
        if controls == "MANUELL":
            if name not in manuell:
                print(f"{name}: uebersprungen, Bild 00 fehlt")
                continue
            controls = manuell[name]
        print(f"{name}: {controls or 'Automatik'}")
        try:
            bild, metadaten, max_gain = aufnehmen(kamera_config, controls)
        except RuntimeError as fehler:
            if "busy" in str(fehler).lower() or "acquire" in str(fehler).lower():
                print(
                    "Kamera belegt -- laeuft der Server noch? "
                    "sudo systemctl stop opcua-server.service"
                )
                return 1
            raise
        except ValueError as fehler:
            # Unbekanntes Control auf dieser Kamera/libcamera-Version: weiter.
            print(f"  uebersprungen: {fehler}")
            uebersicht.append({"name": name, "controls": controls, "fehler": str(fehler)})
            continue
        if name.startswith("00_"):
            manuell = manuelle_einstellungen(
                int(metadaten["ExposureTime"]), float(metadaten["AnalogueGain"]), float(max_gain)
            )
        datei = ordner / f"{name}.png"
        if not cv2.imwrite(str(datei), bild):
            raise OSError(f"Konnte {datei} nicht schreiben")
        eintrag = {
            "name": name,
            "datei": datei.name,
            "controls": controls,
            "metadaten": metadaten_json(metadaten),
            **kennzahlen(bild, not args.ohne_erkennung, detector_parameters),
        }
        (ordner / f"{name}.json").write_text(json.dumps(eintrag, indent=2, ensure_ascii=False))
        uebersicht.append(eintrag)
        kurz = ", ".join(
            f"{k}={metadaten[k]:.4g}" if isinstance(metadaten.get(k), float) else f"{k}={metadaten[k]}"
            for k in CAMERA_METADATA_KEYS
            if k in metadaten
        )
        print(
            f"  {kurz}\n  Mittel {eintrag['mittelwert']}, ausgebrannt "
            f"{eintrag['anteil_ausgebrannt_pct']} %, Laplace {eintrag['laplace_varianz']}"
            + (f", Tags {eintrag['tag_ids']}" if "tag_ids" in eintrag else "")
        )

    (ordner / "uebersicht.json").write_text(json.dumps(uebersicht, indent=2, ensure_ascii=False))
    print(f"\nFertig: {len(uebersicht)} Einstellungen in {ordner}")
    print(
        "Danach: git add data/rohbilder/camera_settings_test && "
        "git commit -m 'Kamera-Einstellungstest' && git push"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

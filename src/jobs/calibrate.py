"""Platzhalter fuer das Kalibrierungsscript.

Wird vom Kalibrierungs-Job als Subprozess ausgefuehrt. Die eigentliche
Kalibrierungslogik (z.B. auf Basis von src/apriltag/calibrate_camera.py)
wird hier spaeter nachgeruestet; bis dahin meldet das Script nur, dass es
gelaufen ist.
"""


def main() -> str:
    return "Calibrieren"


if __name__ == "__main__":
    print(main())

"""Platzhalter fuer das Bilderkennungsscript.

Wird vom Bilderkennungs-Job als Subprozess ausgefuehrt. Die eigentliche
Erkennungslogik (z.B. auf Basis von src/apriltag/detect_apriltags.py) wird
hier spaeter nachgeruestet; bis dahin meldet das Script nur, dass es
gelaufen ist.
"""


def main() -> str:
    return "Taking Image"


if __name__ == "__main__":
    print(main())

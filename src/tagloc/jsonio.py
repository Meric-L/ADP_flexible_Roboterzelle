"""Zahlen fuer JSON: NaN und Unendlich abfangen, Rundung je Aufrufer.

Nur Standardbibliothek -- das Modul laesst sich ohne numpy und ohne OpenCV
importieren, auch von `vision_server.detection.apriltag`.
"""

import math


def finite_or_none(value, digits: int | None = None) -> float | None:
    """NaN und Unendlich auf `None`, sonst die Zahl, auf `digits` Stellen gerundet.

    `json.dumps` schreibt NaN als nacktes `NaN` -- das ist kein JSON --, und
    `vision_server.payload._dumps` serialisiert mit `allow_nan=False` und
    wirft: ein einzelner Ausreisser beendete dann nicht nur das Attribut,
    sondern den ganzen Job als INTERNAL. `None` ist hier die ehrliche
    Antwort, "dieser Wert liegt nicht vor". `None` bleibt `None`.

    Die Rundung beruehrt das Dateiformat und bleibt deshalb Sache des
    Aufrufers: ohne `digits` wird nicht gerundet, die Payload der Erkennung
    rundet auf 4 Stellen, die Hand-Auge-Datei auf 6.
    """
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number if digits is None else round(number, digits)


__all__ = ["finite_or_none"]

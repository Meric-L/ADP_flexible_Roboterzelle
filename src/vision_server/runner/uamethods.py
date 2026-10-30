"""Bausteine fuer die OPC-UA-Methoden des Vision-Systems: Antworten und Anlage.

Alle Methoden antworten mit `Error` (Int32) und, wo es ein Ergebnis gibt,
davor mit einem String (JSON). Asyncua validiert die Rueckgabe nicht; sie muss
ein Tupel aus `ua.Variant` sein -- eine Liste wuerde als ein einziger Variant
verpackt.
"""

from asyncua import ua

from ..errors import VisionErrorCode


def code_reply(code: VisionErrorCode) -> tuple[ua.Variant]:
    """Antwort mit nur `Error` (Int32)."""
    return (ua.Variant(int(code), ua.VariantType.Int32),)

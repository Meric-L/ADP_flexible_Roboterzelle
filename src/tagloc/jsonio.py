"""JSON-Dateien schreiben und mit Schema-Pruefung lesen. Nur Standardbibliothek.

Kalibrierung, Tag-Map, CLI-Ausgaben und die synthetischen Szenen schrieben
und lasen ihre Dateien jeweils selbst -- mit denselben drei Zeilen, aber
nicht atomar: ein Abbruch mitten im Schreiben (Strom weg am Pi, Strg+C)
konnte eine halbe Kalibrierdatei hinterlassen, mit der der Server danach
nicht mehr startet. Hier wird zuerst eine Nachbardatei geschrieben und erst dann per
`os.replace` umbenannt; auf demselben Dateisystem ist das atomar, die alte
Datei bleibt also bis zum letzten Moment vollstaendig.
"""

import json
import os
import uuid
from pathlib import Path
from typing import Any


def write_json(path, payload: Any, *, indent: int = 2) -> None:
    """Schreibt `payload` als JSON mit abschliessendem Zeilenumbruch.

    Fehlende Verzeichnisse werden angelegt. Die temporaere Datei liegt im
    Zielverzeichnis (sonst waere `os.replace` ueber Dateisystemgrenzen nicht
    atomar) und wird mit `open(..., "x")` angelegt, damit sie dieselben
    Rechte bekommt wie eine direkt geschriebene Datei.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=indent) + "\n"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temporary, "x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def read_schema_json(path, schema: str, what: str, *, schema_name: str | None = None) -> dict:
    """Liest eine JSON-Datei und prueft ihr `schema`-Feld.

    `what` benennt die Datei in der Fehlermeldung ("Kalibrierung",
    "Tag-Map"), `schema_name` das Schema (Standard: "<what>-Schema"). Beide
    Meldungen nennen den Pfad -- ohne ihn weiss auf dem Pi niemand, welche
    der Dateien gemeint ist.

    Raises:
        FileNotFoundError: Die Datei existiert nicht.
        ValueError: Kein gueltiges JSON oder ein anderes Schema.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"{what} nicht gefunden: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    found = data.get("schema") if isinstance(data, dict) else None
    if found != schema:
        name = schema_name or f"{what}-Schema"
        raise ValueError(f"Unbekanntes {name} '{found}' in {path} (erwartet {schema})")
    return data


__all__ = ["read_schema_json", "write_json"]

#!/usr/bin/env python3
"""Injiziert die Projektregeln aus CLAUDE.md in jede Claude-Code-Session.

Registriert in .claude/settings.json fuer SessionStart, SubagentStart und den
ersten Schreibzugriff einer Session. Liest nur, blockiert nie: bei jedem Fehler
wird still mit Exit-Code 0 beendet, damit eine Session niemals an diesem Hook
haengen bleibt.

Ausgabe (stdout):
    {"hookSpecificOutput": {"hookEventName": "...", "additionalContext": "..."}}
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REGELN = """PROJEKTREGELN (CLAUDE.md, verbindlich fuer alle Teammitglieder und Agenten):

1. VOR der ersten inhaltlichen Aenderung die relevanten MDs unter
   doc/projektdoku/ lesen.
2. Aufgaben groesser als ein Einzeiler bekommen VOR der Umsetzung einen Plan
   unter doc/projektdoku/arbeitsplaene/<thema>.md (Vorlage:
   doc/projektdoku/arbeitsplaene/_vorlage.md), inklusive Abschnitt
   "Schnittstellen" (OPC-UA-NodeIds/Methoden, Python-Signaturen, Payloads,
   Ports) - damit andere ohne diesen Code dagegen entwickeln koennen.
3. Neuen Plan in die Tabelle in doc/projektdoku/arbeitsplaene/README.md
   eintragen.
4. Steht zum Thema bereits ein Plan "in Arbeit" von jemand anderem: nicht
   parallel implementieren, sondern dort andocken oder nachfragen.
"""

KOPFZEILE = re.compile(r"^\*\*(Status|Verantwortlich):\*\*\s*(.+?)\s*$", re.MULTILINE)


def projekt_wurzel() -> Path:
    """Repo-Wurzel: das Verzeichnis oberhalb von .claude/hooks/."""
    return Path(__file__).resolve().parents[2]


def erste_ueberschrift(pfad: Path) -> str:
    try:
        with pfad.open(encoding="utf-8", errors="replace") as fh:
            for zeile in fh:
                if zeile.startswith("# "):
                    return zeile[2:].strip()
    except OSError:
        pass
    return ""


def fachdoku(wurzel: Path) -> str:
    zeilen = [
        f"- doc/projektdoku/{p.name} — {erste_ueberschrift(p)}".rstrip(" —")
        for p in sorted((wurzel / "doc" / "projektdoku").glob("*.md"))
    ]
    return "\n".join(zeilen) if zeilen else "  (keine)"


def arbeitsplaene(wurzel: Path) -> str:
    zeilen = []
    for p in sorted((wurzel / "doc" / "projektdoku" / "arbeitsplaene").glob("*.md")):
        if p.name in {"README.md", "_vorlage.md"}:
            continue
        try:
            kopf = dict(KOPFZEILE.findall(p.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            kopf = {}
        zeilen.append(
            f"- doc/projektdoku/arbeitsplaene/{p.name} — Status: {kopf.get('Status', '?')}"
            f" | Verantwortlich: {kopf.get('Verantwortlich', '?')}"
        )
    return "\n".join(zeilen) if zeilen else "  (noch keine Arbeitsplaene vorhanden)"


def main() -> int:
    try:
        roh = sys.stdin.read()
    except Exception:
        roh = ""
    try:
        event = json.loads(roh).get("hook_event_name") or "SessionStart"
    except Exception:
        event = "SessionStart"

    wurzel = projekt_wurzel()
    kontext = (
        f"{REGELN}\n"
        f"Vorhandene Fachdokumentation:\n{fachdoku(wurzel)}\n\n"
        f"Arbeitsplaene:\n{arbeitsplaene(wurzel)}\n\n"
        "Volltext der Regeln: CLAUDE.md im Wurzelverzeichnis des Repositories."
    )

    json.dump(
        {"hookSpecificOutput": {"hookEventName": event, "additionalContext": kontext}},
        sys.stdout,
        ensure_ascii=False,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # ein Hook darf die Session nie blockieren
        sys.exit(0)

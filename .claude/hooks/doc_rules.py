#!/usr/bin/env python3
"""Inject the project rules from CLAUDE.md into every Claude Code session.

Registered in .claude/settings.json for SessionStart, SubagentStart and the
first write of a session. Only reads, never blocks: on any error it exits
quietly with code 0, so a session never hangs on this hook.

Output (stdout):
    {"hookSpecificOutput": {"hookEventName": "...", "additionalContext": "..."}}
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REGELN = """PROJECT RULES (CLAUDE.md, binding for all team members and agents):

0. Language: English for all documentation, docstrings, code comments and
   commit messages. Runtime texts (log, error, OPC UA messages, UI texts)
   stay German until migrated deliberately.
1. BEFORE the first substantive change, read the relevant MDs under
   doc/project_docs/.
2. Tasks bigger than a one-liner get a plan BEFORE implementation under
   doc/project_docs/work-plans/<topic>.md (template:
   doc/project_docs/work-plans/_template.md), including the section
   "Interfaces" (OPC UA NodeIds/methods, Python signatures, payloads, ports)
   - so others can develop against it without this code.
3. Add the new plan to the table in doc/project_docs/work-plans/README.md.
4. If a plan on the topic is already "in progress" by someone else: do not
   implement in parallel; join that plan or ask.
"""

#: Plan header fields; "Verantwortlich" is the German name of "Owner" in older plans.
KOPFZEILE = re.compile(r"^\*\*(Status|Owner|Verantwortlich):\*\*\s*(.+?)\s*$", re.MULTILINE)


def projekt_wurzel() -> Path:
    """Repo root: the directory above .claude/hooks/."""
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
        f"- doc/project_docs/{p.relative_to(docs).as_posix()} — {erste_ueberschrift(p)}".rstrip(" —")
        for docs in [wurzel / "doc" / "project_docs"]
        for p in sorted(docs.rglob("*.md"))
        if p.relative_to(docs).parts[0] not in {"work-plans", "analyses"}
    ]
    return "\n".join(zeilen) if zeilen else "  (none)"


def arbeitsplaene(wurzel: Path) -> str:
    zeilen = []
    for p in sorted((wurzel / "doc" / "project_docs" / "work-plans").glob("*.md")):
        if p.name in {"README.md", "_template.md"}:
            continue
        try:
            kopf = dict(KOPFZEILE.findall(p.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            kopf = {}
        zeilen.append(
            f"- doc/project_docs/work-plans/{p.name} — Status: {kopf.get('Status', '?')}"
            f" | Owner: {kopf.get('Owner') or kopf.get('Verantwortlich', '?')}"
        )
    return "\n".join(zeilen) if zeilen else "  (no work plans yet)"


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
        f"Existing topic documentation:\n{fachdoku(wurzel)}\n\n"
        f"Work plans:\n{arbeitsplaene(wurzel)}\n\n"
        "Full text of the rules: CLAUDE.md in the root directory of the repository."
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
    except Exception:  # a hook must never block the session
        sys.exit(0)

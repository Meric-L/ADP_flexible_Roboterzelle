# 0009 — Project documentation: English, current state, plan before code

- **Status:** accepted
- **Date:** 2026-11-17

## Context

Several people and agents work in parallel chats and on separate machines and otherwise find out or build the same thing twice.

## Decision

- All documentation, docstrings, comments and commit messages are English; runtime texts that
  operators read stay German and are quoted verbatim. The thesis keeps its language.
- Documents describe the **current state**, no history; an outdated interface description is worse than none.
- Every task beyond a one-line fix gets a work plan with interfaces in `work-plans/` **before** implementation, registered in `work-plans/README.md`.
- Before starting: read existing docs and check the plan list for duplicate work.

## Consequences

- Plans are corrected to the actual interfaces when done; lasting knowledge moves into topic docs.

## Alternatives considered

- German documentation: dropped when all docs were translated (17.11.2026).

Sources: `CLAUDE.md` (repo root), [`../work-plans/README.md`](../work-plans/README.md),
[`../work-plans/_template.md`](../work-plans/_template.md).

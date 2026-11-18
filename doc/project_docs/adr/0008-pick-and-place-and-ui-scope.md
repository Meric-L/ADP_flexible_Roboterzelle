# 0008 — Pick & Place is the group's use case only; UI polish belongs to the WSC team

- **Status:** accepted
- **Date:** 2026-11-17

## Context

The group builds on the WebSkillComposition (WSC) repository, whose `dev` branch is maintained by the WSC team.

## Decision

- Pick & Place (`usePickPlaceRun.ts`, `pickPlace*.ts`) is only the use case of this group and
  does **not** go into WSC `dev`; it stays unchanged during refactors.
- The WSC team reworks the UI themselves; panels are touched only as far as a change requires
  (form, start/stop, progress).

## Consequences

- Docs that describe these parts label them "use case of the group, not for `dev`" and "UI reworked by the WSC team".

## Alternatives considered

- Upstreaming the use case: not wanted by the group.

Sources: [`../work-plans/pi-workflows-robot-target.md`](../work-plans/pi-workflows-robot-target.md) (out of scope, user decision 17.11.2026).

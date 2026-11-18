---
name: project_docs
description: Read and extend the project documentation of the ADP robot cell. Use when starting a task on OPC UA, the vision system, AprilTag localization, mDNS or the frontend connection, when creating or maintaining a work plan with interfaces, and for questions like "what is already documented about this", "is someone already working on it" or "which interface does X have".
---

# Read and extend the project documentation

Several people work on the same project in separate chats. Without a shared
place, everyone finds out the same thing twice, and two implementations of the
same interface drift apart. This skill keeps `doc/project_docs/` consistent.

Everything in **English** — file contents, comments, commit messages.
Runtime texts (log, error and OPC UA messages, UI texts) stay German until
migrated deliberately; documentation quotes them verbatim.

Work happens on feature branches (currently `feature/vision-server`). Only
finished, working features go to `main`.

## 1. Read — always first

1. `git pull` — the others write to the same files.
2. Read `doc/project_docs/README.md` (map of the folders), then `ls doc/project_docs/*/`
3. Read `doc/project_docs/work-plans/README.md`: **Is someone already working on this?**
   If a plan on the topic is `in progress` and belongs to someone else, do not
   implement in parallel — join it or ask.
4. Read the topic docs that concern the topic. Go to the relevant section,
   not the whole document.

| Topic | Read first |
| --- | --- |
| OPC UA server, OPC 40100, address space, methods, payload | `doc/project_docs/interfaces/vision-server-interface.md`, `doc/project_docs/topics/vision-system.md` |
| Connection to WebSkillComposition, frontend, several vision systems | `doc/project_docs/topics/vision-system-integration.md` |
| Running and configuring the vision server, asyncua pitfalls | `doc/project_docs/topics/vision-system.md` |
| Open points of the vision system | `doc/project_docs/topics/vision-system-integration.md` (Open points) |
| AprilTag: code map, conventions, maintenance | `doc/project_docs/topics/apriltag-localization.md`, `doc/project_docs/topics/apriltag-reference.md` |
| Analyses of past problems and experiments | `doc/project_docs/analyses/` |
| How a component works, where to read more | `doc/project_docs/components/<component>/README.md` |
| Install, quickstart, integrating a device via Part 10 | `doc/project_docs/usage/` |
| Layer 1 / Layer 2 overview | `doc/project_docs/layers.md` |
| Why something is built the way it is | `doc/project_docs/adr/` |
| Unexpected behaviour, historical quirks | `doc/project_docs/work-plans/legacy.md` |
| Presentation / supervision | `doc/project_docs/topics/presentation.md` |

If the answer is in one of these files, it is read rather than asked. If it is
missing there, that is a finding in itself — once clarified, it goes in.

## 2. Create a work plan

For every task that is more than a one-line fix, **before** implementation:

1. Copy `doc/project_docs/work-plans/_template.md` to `doc/project_docs/work-plans/<topic>.md`
   (lower case, with hyphens, e.g. `opcua-part10-wrapper.md`).
2. Fill in the header — exactly this format, the hook reads it:

```markdown
**Status:** planned | in progress | done | dropped — DD.MM.YYYY
**Owner:** <name> or `Agent: <task>`
**Topic:** opcua | vision | apriltag | mdns | frontend | other
**Branch:** <branch-name>
```

3. Fill in the **Interfaces** section. That is the purpose of the whole
   document — concrete enough that someone else can develop against it
   **without this code**:
   - OPC UA: NodeId/BrowsePath with namespace, method signature with
     arguments, data types, return values, error status codes
   - Python: module, function, parameters, return type, raised exceptions
   - Data: JSON/payload format with the meaning of each field
   - Operations: ports, services, mDNS names, systemd units
   - Failure cases: timeout, missing camera, invalid job, abort
4. List the MDs you evaluated under "Already read".
5. Add a row to the table in `doc/project_docs/work-plans/README.md`.

Only then is code written.

## 3. During implementation

- If the implementation deviates from the plan: fill in "Deviations from the
  plan" **and** correct the interfaces part. An outdated interface description
  is worse than none.
- If an already documented interface changes: first adapt the topic doc under
  `doc/project_docs/` so everyone sees it, then the code.
- Do not rewrite existing documents to fit something new in — own section or
  own file.

## 4. When finished

1. Plan status `done`, actual interfaces filled in.
2. Row in the index `doc/project_docs/work-plans/README.md` updated.
3. Lasting domain knowledge moved into the matching topic doc under
   `doc/project_docs/` — the plan is the planning, the topic doc is the current
   state. New topic doc? Then add it to the table in
   `doc/project_docs/work-plans/README.md` too.

## 5. Commit — yes. Push — only when told.

1. Show `git diff`.
2. Commit without asking first. Message in the usual repo format:
   `docs: <short title>` for documentation, `feat(<area>): <short title>` for
   code — e.g. `docs: work plan Part 10 wrapper with interfaces`.
   Only the files of the task (`git add <paths>`, no `git add -A`), always on
   the feature branch, never on `main`.
3. **Do not push.** A push only happens on explicit instruction. This is a
   shared repo — as long as nothing is out, a local commit bothers nobody.
4. At the end of each feature, a short note in the answer, for example:

   > Commit `<sha>` on `<branch>` — **not pushed**.
   > To share: `git push origin <branch>`.

   The note is never missing, not even for small changes. Otherwise someone
   assumes the state is already on the remote.

On a merge conflict in `doc/project_docs/work-plans/README.md`: keep both
rows. A conflict in the index is never an either-or decision.

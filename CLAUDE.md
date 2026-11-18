# ADP flexible robot cell — working instructions

Group project on capturing the position and topology of mechatronic modules in
a reconfigurable lab robot cell (about 5x5 m). The repo holds the
implementation (`src/`, `tests/`, `hardware/`, `data/`), the project
documentation (`doc/project_docs/`) and the LaTeX thesis (`doc/`).

This file is **binding for everyone**: every team member and every agent
(Claude chat, subagent, background job). It lives in the repository — whoever
clones it has it. Claude Code loads it automatically; people read it here.

**Language: English, for everything.** Documentation (all Markdown files,
work plans, topic docs, code documentation under `doc/project_docs/code/`),
docstrings, code comments and commit messages are written in **English**
(Python: PEP 257, Google style with `Args:`, `Returns:`, `Raises:`).
Exceptions:

- Runtime texts that operators and tests read — log, error and OPC UA
  messages, frontend UI texts — stay as they are (German) until they are
  migrated deliberately; do not translate them in passing. Documentation that
  quotes such a text quotes it verbatim.
- The LaTeX thesis (`doc/*.tex`) keeps its own language.
- File and folder names stay as they are (other files link to them).

## Before you start

Before the **first** substantive action — writing code, changing a file,
drafting a concept — without exception:

1. **Look at what already exists.** `ls doc/project_docs/*.md doc/project_docs/work-plans/*.md`.
   Read the MDs relevant to the topic **before** anything is created.
   `doc/project_docs/` is the shared state of knowledge of the project, not
   decoration.
2. **Duplicate-work check.** [`doc/project_docs/work-plans/README.md`](doc/project_docs/work-plans/README.md)
   lists who is working on what. If a plan on the topic is already
   `in progress` and belongs to someone else: do **not** implement in
   parallel; join that plan or talk to the person.
3. **Write a plan, then build** — see the next section.

We work in parallel in separate chats and on separate machines. Without these
two looks, everyone finds out the same thing twice or builds the same thing
twice.

## Work plan with interfaces

Every task that is more than a one-line fix gets its own file
`doc/project_docs/work-plans/<topic>.md` **before** implementation
(template: [`doc/project_docs/work-plans/_template.md`](doc/project_docs/work-plans/_template.md)).

Mandatory header fields: Status (`planned` / `in progress` / `done` /
`dropped`), Owner, Topic, Branch. Then goal, affected files, documents read,
open questions — and above all:

**Interfaces.** The most important section, concrete enough that someone else
can develop against it **without this code**:

- OPC UA: NodeIds and BrowsePaths including namespace, method signatures with
  arguments, data types, return values and error status codes
- Python: module, function, parameters, return type, raised exceptions
- Data: JSON/payload format with the meaning of each field
- Operations: ports, services, mDNS names, systemd units
- Failure cases: timeout, missing camera, invalid job, abort

Then add a row to the table in `doc/project_docs/work-plans/README.md`.
When finished, set the status to `done` and fill in the **actual**
interfaces; if the implementation deviates from the plan, the plan is
corrected. An outdated interface description is worse than none.

Lasting domain knowledge then belongs in the matching topic doc under
`doc/project_docs/` — the work plan is the planning, the topic doc is the
current state.

The skill `project_docs` (`.claude/skills/project_docs/`) covers the whole
procedure. It lives in the repo, so everyone has it after cloning: it finds
the relevant MDs, creates the plan in the fixed format and commits on its
own, without pushing.

## Rules for changes

- `git pull` before reading `doc/project_docs/` — the others write to the same
  files.
- No parallel second implementation of a topic that belongs to someone else
  according to `doc/project_docs/work-plans/README.md`.
- A documented interface is not changed silently: first adapt the work plan
  or the topic doc so everyone sees it, then the code.
- Do not rewrite existing documents in `doc/project_docs/` and `concept/` to
  fit something new in. New material goes into its own section or its own
  file.
- No renaming or deleting of `doc/project_docs/*.md` without agreement — other
  MDs link to them.
- Committing is automatic: show the diff, commit, carry on — no need to ask.
  Only the files of the task, never on `main`.
- Do not push without explicit instruction. At the end of each feature, a
  short note that it was committed but not pushed, with the branch and
  `git push origin <branch>`.
- `main` only gets finished, working features. Work happens on feature
  branches, currently `feature/vision-server`.
- `doc/*.tex` is the written thesis. Only touch it when the task explicitly
  concerns the thesis.

## Finding your way in the repo

| Path | Content |
| --- | --- |
| `doc/project_docs/*.md` | Topic documentation: interfaces, concepts, test instructions |
| `doc/project_docs/work-plans/` | Running and finished work plans (see above) |
| `doc/*.tex` | LaTeX thesis |
| `src/` | Implementation: OPC UA server, vision, sensors |
| `tests/` | Pytest tests |
| `tools/` | Helper scripts |

## Technical enforcement

`.claude/settings.json` calls the script `.claude/hooks/doc_rules.py` at
every session start, for every subagent and before the first write of a
session. It passes on these rules and lists which topic docs and which running
plans exist right now — no agent can claim not to have known the
documentation.

The hook reminds, it does not block. The rules above are binding, and they
also apply to work without Claude Code.

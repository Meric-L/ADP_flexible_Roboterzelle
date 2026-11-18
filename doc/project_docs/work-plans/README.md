# Work plans — who is doing what right now

This folder is the **duplicate-work check before implementation**. Everyone —
human or agent — looks here first before starting a task (see `CLAUDE.md`,
section "Before you start").

The topic docs in `doc/` describe **what is** — the finished state, the documented
interfaces. This folder describes **what someone is planning** and which interfaces
will newly arise in the process. We work in parallel in separate chats and on separate
machines; without this index someone builds for the second time what is already
running, or against an interface that does not exist in that form.

## Index

| Plan | Topic | Status | Owner | Updated |
| --- | --- | --- | --- | --- |
| [`pi-workflows-robot-target.md`](pi-workflows-robot-target.md) | vision | dropped | `Agent: Codex (handover): workflows to Pi, robot driving to backend` | 18.11.2026 |
| [`pi-backend-discovery.md`](pi-backend-discovery.md) | opcua | done | `Agent: backend discovers the Pis (Part 10), orchestration Claude, Pi/backend Codex` | 18.11.2026 |
| [`lds-registration.md`](lds-registration.md) | mdns | done | `Agent: LDS registration for the aggregation server` | 21.09.2026 |
| [`legacy-removal-part10-facade.md`](legacy-removal-part10-facade.md) | opcua | done | `Agent: remove RaspiDevice/VisionSystem, Part-10 facade` | 22.09.2026 |
| [`apriltag-world-tag-concept.md`](apriltag-world-tag-concept.md) | apriltag | done | `Agent: move the coordinate transformation onto the world-tag concept` | 22.09.2026 |
| [`apriltag-hand-eye-anchoring.md`](apriltag-hand-eye-anchoring.md) | apriltag | done | `Agent: hand-eye calibration and anchoring at the world tag` | 22.09.2026 |
| [`apriltag-tagmap-robustness.md`](apriltag-tagmap-robustness.md) | apriltag | done | `Agent: tag-map migration and placeholder modules` | 22.09.2026 |
| [`apriltag-tagmap-over-opcua.md`](apriltag-tagmap-over-opcua.md) | apriltag | done | `Agent: set the tag map via OPC UA method` | 22.09.2026 |
| [`livestream-mjpeg-and-watchdog.md`](livestream-mjpeg-and-watchdog.md) | vision | done | John Glanz, `Agent: takeover of branch John` | 22.09.2026 |
| [`ceiling-camera-full-resolution.md`](ceiling-camera-full-resolution.md) | vision | in progress | Meric | 24.09.2026 |
| [`opcua-camera-condition-monitoring.md`](opcua-camera-condition-monitoring.md) | opcua | done | `Agent: camera state as DeviceHealth (OPC 40100-2)` | 22.09.2026 |
| [`apriltag-pose-estimation-optimization.md`](apriltag-pose-estimation-optimization.md) | apriltag | done | `Agent: AprilTag optimization` | 23.09.2026 |
| [`model-surface-tag-placement.md`](model-surface-tag-placement.md) | frontend | done (provisional) | Meric, `Agent: place tags on model surfaces` | 29.09.2026 |
| [`layer2-run-hand-pi.md`](layer2-run-hand-pi.md) | apriltag | done | Meric, `Agent: layer 2 run on the hand Pi` | 29.09.2026 |
| [`layer2-approach.md`](layer2-approach.md) | apriltag | done | `Agent: layer 2 approach on the hand Pi` | 12.10.2026 |
| [`hand-eye-over-opcua.md`](hand-eye-over-opcua.md) | apriltag | done | `Agent: hand-eye calibration via OPC UA` | 13.10.2026 |
| [`hand-eye-chessboard-automation.md`](hand-eye-chessboard-automation.md) | apriltag | done | John Glanz, `Agent: hand-eye automatic with chessboard` | 13.10.2026 |
| [`layer2-oblique-view.md`](layer2-oblique-view.md) | apriltag | done | John Glanz, `Agent: layer 2 oblique view` | 13.10.2026 |
| [`cell-watch.md`](cell-watch.md) | apriltag | done | `Agent: cell monitoring (layer 1)` | 13.10.2026 |
| [`layer2-measuretags.md`](layer2-measuretags.md) | apriltag | done | `Agent: MeasureTags on the hand Pi` | 15.10.2026 |
| [`hand-eye-board-centring.md`](hand-eye-board-centring.md) | apriltag | done | `Agent: center the hand-eye board on the hand Pi` | 22.10.2026 |
| [`hand-eye-accuracy.md`](hand-eye-accuracy.md) | apriltag | in progress | Meric, `Agent: hand-eye accuracy` | 28.10.2026 |
| [`hand-eye-chessboard-raw-data-offline.md`](hand-eye-chessboard-raw-data-offline.md) | apriltag | done (no hardware run) | Meric, Codex | 09.11.2026 |
| [`hand-eye-charuco-selection.md`](hand-eye-charuco-selection.md) | apriltag | done (no hardware run) | Meric, Codex | 09.11.2026 |
| [`calibration-lens-model-per-camera.md`](calibration-lens-model-per-camera.md) | vision | done | `Agent: lens model per camera (Kannala-Brandt / Brown-Conrady)` | 29.09.2026 |
| [`tag-rotate-90-degrees.md`](tag-rotate-90-degrees.md) | frontend | done | Meric, `Agent: rotate tag by 90°` | 19.10.2026 |
| [`save-raw-image.md`](save-raw-image.md) | vision | done | Meric, `Agent: save raw image` | 19.10.2026 |
| [`stream-overlay-lean.md`](stream-overlay-lean.md) | frontend | done | Meric, `Agent: slim down stream overlay` | 21.10.2026 |
| [`livestream-tags-full-resolution.md`](livestream-tags-full-resolution.md) | vision | done | Meric, `Agent: live-stream tags from full resolution` | 21.10.2026 |
| [`tag-survey-reference-edges.md`](tag-survey-reference-edges.md) | apriltag | done | Meric, `Agent: survey tag position with the camera via reference edges` | 20.10.2026 |
| [`tag-survey-liveview.md`](tag-survey-liveview.md) | apriltag | done | Meric, `Agent: live view for tag survey` | 21.10.2026 |
| [`apriltag-detection-blurred-tags.md`](apriltag-detection-blurred-tags.md) | vision | done | Meric, `Agent: detection of blurred tags` | 19.10.2026 |
| [`camera-controls-and-detector-parameters.md`](camera-controls-and-detector-parameters.md) | vision | done | Meric, `Agent: camera controls` | 19.10.2026 |
| [`cleanup-and-code-handbook.md`](cleanup-and-code-handbook.md) | other | in progress | Meric, `Agent: tidy-up and code handbook` | 30.10.2026 |
| [`use-case-photo.md`](use-case-photo.md) | vision | in progress | Meric, `Agent: use case photo` | 29.10.2026 |
| [`use-case-measurement.md`](use-case-measurement.md) | vision | in progress | Meric, `Agent: use case measurement` | 29.10.2026 |
| [`model-error-check.md`](model-error-check.md) | apriltag | planned | Meric | 10.11.2026 |
| [`ceiling-camera-subpixel-corners.md`](ceiling-camera-subpixel-corners.md) | vision | in progress | NobbisCode, `Agent: subpixel corners on the ceiling Pi` | 10.11.2026 |
| [`calibration-raw-captures.md`](calibration-raw-captures.md) | vision | done | Meric, `Agent: raw calibration images` | 10.11.2026 |
| [`ceiling-camera-calibration-board-choice.md`](ceiling-camera-calibration-board-choice.md) | vision | done (no hardware run) | Meric, `Agent: layer 1 calibration with selectable board` | 17.11.2026 |
| [`ceiling-camera-crop.md`](ceiling-camera-crop.md) | vision | done (no hardware run) | NobbisCode, `Agent: ceiling camera detection region` | 17.11.2026 |

Topics: `opcua` · `vision` · `apriltag` · `mdns` · `frontend` · `other`
Status: `planned` · `in progress` · `done` · `dropped`

## How to create a plan

1. **Read first:** the topic docs under `doc/` that concern the topic — see the table
   below. At least the ones whose interface you are working against.
2. Copy `_template.md` to `<topic>.md` (short, with hyphens, e.g. `opcua-part10-wrapper.md`).
3. Fill in the header (status, owner, topic, branch). Write the **Interfaces** section so
   concretely that another team member can develop against it without this code: NodeIds,
   method signatures, Python signatures, payloads, ports, failure cases.
4. Enter a line in the table above — this is the place where everyone sees what is running.
5. After completion: set the status to `done`, enter the actual interfaces, and carry
   lasting topic knowledge into the matching topic doc under `doc/`.

## Collision

If a plan on the topic already belongs to someone else and is `in progress`: do not
implement in parallel. Instead, add a section to the existing plan or contact the person.

## Topic documentation under `doc/`

Read before creating a plan — here is the documented state:

| File | Content |
| --- | --- |
| [`../vision-server-interface.md`](../interfaces/vision-server-interface.md) | OPC UA vision server: address space, methods, backend connection |
| [`../vision-system.md`](../topics/vision-system.md) | Vision system — current state on the Raspberry Pi |
| [`../vision-system-integration.md`](../topics/vision-system-integration.md) | Integration of the vision systems into WebSkillComposition |
| [`../vision-system.md`](../topics/vision-system.md) | Vision server — operation and maintenance |
| [`../apriltag-localization.md`](../topics/apriltag-localization.md) | AprilTag localisation, maintainer guide |
| [`../apriltag-reference.md`](../topics/apriltag-reference.md) | AprilTag reference |
| [`../README.md`](../code/README.md) | Code documentation: overview; details per area under `../code/` |
| [`../legacy-cell.md`](legacy-cell.md) | Clean-up audit of this repo (`apriltag`): dead code, duplicates, workarounds, guidelines, test gaps |
| [`../legacy-wsc.md`](legacy-wsc.md) | Clean-up audit of webskillcomposition, only our share (`origin/dev..Ungetestet`) |
| [`../legacy.md`](legacy.md) | Known legacy issues and pitfalls (as of 21.09., superseded by the two audits) |
| [`../legacy-dead-code.md`](legacy-dead-code.md) | Dead-code audit, as of 21.10. (superseded by the two audits) |
| [`../presentation.md`](../topics/presentation.md) | Slide basis for the supervisors' presentation |

If a topic doc is added, add a line here. For agents, the script
`.claude/hooks/doc_rules.py` lists the folder contents automatically in any case.

## Technical reminder

`.claude/settings.json` runs `.claude/hooks/doc_rules.py` at every session start,
for every subagent, and before the first write access. The script outputs the rules from
`CLAUDE.md` and lists which topic docs and which running plans currently exist. Everything
lies in the repo — after cloning, it applies automatically for everyone. Without Claude the
folder works just the same; the rule is then followed by hand.

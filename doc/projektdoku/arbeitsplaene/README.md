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
| [`pi-workflows-robotertarget.md`](pi-workflows-robotertarget.md) | vision | planned | `Agent: Codex (handover): workflows to Pi, robot driving to backend` | 17.11.2026 |
| [`pi-backend-discovery.md`](pi-backend-discovery.md) | opcua | in progress | `Agent: backend discovers the Pis (Part 10), orchestration Claude, Pi/backend Codex` | 17.11.2026 |
| [`lds-registrierung.md`](lds-registrierung.md) | mdns | done | `Agent: LDS registration for the aggregation server` | 21.09.2026 |
| [`altlasten-abbau-part10-fassade.md`](altlasten-abbau-part10-fassade.md) | opcua | done | `Agent: remove RaspiDevice/VisionSystem, Part-10 facade` | 22.09.2026 |
| [`apriltag-welttag-konzept.md`](apriltag-welttag-konzept.md) | apriltag | done | `Agent: move the coordinate transformation onto the world-tag concept` | 22.09.2026 |
| [`apriltag-hand-auge-ankern.md`](apriltag-hand-auge-ankern.md) | apriltag | done | `Agent: hand-eye calibration and anchoring at the world tag` | 22.09.2026 |
| [`apriltag-tagmap-robustheit.md`](apriltag-tagmap-robustheit.md) | apriltag | done | `Agent: tag-map migration and placeholder modules` | 22.09.2026 |
| [`apriltag-tagmap-ueber-opcua.md`](apriltag-tagmap-ueber-opcua.md) | apriltag | done | `Agent: set the tag map via OPC UA method` | 22.09.2026 |
| [`livestream-mjpeg-und-watchdog.md`](livestream-mjpeg-und-watchdog.md) | vision | done | John Glanz, `Agent: takeover of branch John` | 22.09.2026 |
| [`deckenkamera-volle-aufloesung.md`](deckenkamera-volle-aufloesung.md) | vision | in progress | Meric | 24.09.2026 |
| [`opcua-kamera-condition-monitoring.md`](opcua-kamera-condition-monitoring.md) | opcua | done | `Agent: camera state as DeviceHealth (OPC 40100-2)` | 22.09.2026 |
| [`apriltag-posenschaetzung-optimierung.md`](apriltag-posenschaetzung-optimierung.md) | apriltag | done | `Agent: AprilTag optimization` | 23.09.2026 |
| [`modell-flaechen-tag-platzierung.md`](modell-flaechen-tag-platzierung.md) | frontend | done (provisional) | Meric, `Agent: place tags on model surfaces` | 29.09.2026 |
| [`layer2-lauf-hand-pi.md`](layer2-lauf-hand-pi.md) | apriltag | done | Meric, `Agent: layer 2 run on the hand Pi` | 29.09.2026 |
| [`layer2-annaeherung.md`](layer2-annaeherung.md) | apriltag | done | `Agent: layer 2 approach on the hand Pi` | 12.10.2026 |
| [`handeye-ueber-opcua.md`](handeye-ueber-opcua.md) | apriltag | done | `Agent: hand-eye calibration via OPC UA` | 13.10.2026 |
| [`handeye-schachbrett-automatik.md`](handeye-schachbrett-automatik.md) | apriltag | done | John Glanz, `Agent: hand-eye automatic with chessboard` | 13.10.2026 |
| [`layer2-schraege-ansicht.md`](layer2-schraege-ansicht.md) | apriltag | done | John Glanz, `Agent: layer 2 oblique view` | 13.10.2026 |
| [`zellen-ueberwachung.md`](zellen-ueberwachung.md) | apriltag | done | `Agent: cell monitoring (layer 1)` | 13.10.2026 |
| [`layer2-measuretags.md`](layer2-measuretags.md) | apriltag | done | `Agent: MeasureTags on the hand Pi` | 15.10.2026 |
| [`hand-auge-brett-zentrieren.md`](hand-auge-brett-zentrieren.md) | apriltag | done | `Agent: center the hand-eye board on the hand Pi` | 22.10.2026 |
| [`handeye-genauigkeit.md`](handeye-genauigkeit.md) | apriltag | in progress | Meric, `Agent: hand-eye accuracy` | 28.10.2026 |
| [`handauge-schachbrett-rohdaten-offline.md`](handauge-schachbrett-rohdaten-offline.md) | apriltag | done (no hardware run) | Meric, Codex | 09.11.2026 |
| [`handauge-charuco-auswahl.md`](handauge-charuco-auswahl.md) | apriltag | done (no hardware run) | Meric, Codex | 09.11.2026 |
| [`kalibrierung-linsenmodell-je-kamera.md`](kalibrierung-linsenmodell-je-kamera.md) | vision | done | `Agent: lens model per camera (Kannala-Brandt / Brown-Conrady)` | 29.09.2026 |
| [`tag-drehen-90-grad.md`](tag-drehen-90-grad.md) | frontend | done | Meric, `Agent: rotate tag by 90°` | 19.10.2026 |
| [`rohbild-speichern.md`](rohbild-speichern.md) | vision | done | Meric, `Agent: save raw image` | 19.10.2026 |
| [`stream-overlay-schlank.md`](stream-overlay-schlank.md) | frontend | done | Meric, `Agent: slim down stream overlay` | 21.10.2026 |
| [`livestream-tags-volle-aufloesung.md`](livestream-tags-volle-aufloesung.md) | vision | done | Meric, `Agent: live-stream tags from full resolution` | 21.10.2026 |
| [`tag-einmessen-bezugskanten.md`](tag-einmessen-bezugskanten.md) | apriltag | done | Meric, `Agent: survey tag position with the camera via reference edges` | 20.10.2026 |
| [`tag-einmessen-liveview.md`](tag-einmessen-liveview.md) | apriltag | done | Meric, `Agent: live view for tag survey` | 21.10.2026 |
| [`apriltag-erkennung-unscharfe-tags.md`](apriltag-erkennung-unscharfe-tags.md) | vision | done | Meric, `Agent: detection of blurred tags` | 19.10.2026 |
| [`kamera-controls-und-detektor-parameter.md`](kamera-controls-und-detektor-parameter.md) | vision | done | Meric, `Agent: camera controls` | 19.10.2026 |
| [`aufraeumen-und-code-handbuch.md`](aufraeumen-und-code-handbuch.md) | other | in progress | Meric, `Agent: tidy-up and code handbook` | 30.10.2026 |
| [`use-case-foto.md`](use-case-foto.md) | vision | in progress | Meric, `Agent: use case photo` | 29.10.2026 |
| [`use-case-messung.md`](use-case-messung.md) | vision | in progress | Meric, `Agent: use case measurement` | 29.10.2026 |
| [`modellfehler-pruefung.md`](modellfehler-pruefung.md) | apriltag | planned | Meric | 10.11.2026 |
| [`deckenkamera-subpixel-ecken.md`](deckenkamera-subpixel-ecken.md) | vision | in progress | NobbisCode, `Agent: subpixel corners on the ceiling Pi` | 10.11.2026 |
| [`kalibrier-aufnahmen-roh.md`](kalibrier-aufnahmen-roh.md) | vision | done | Meric, `Agent: raw calibration images` | 10.11.2026 |
| [`deckenkamera-kalibrierung-board-wahl.md`](deckenkamera-kalibrierung-board-wahl.md) | vision | done (no hardware run) | Meric, `Agent: layer 1 calibration with selectable board` | 17.11.2026 |
| [`deckenkamera-bildausschnitt.md`](deckenkamera-bildausschnitt.md) | vision | done (no hardware run) | NobbisCode, `Agent: ceiling camera detection region` | 17.11.2026 |

Topics: `opcua` · `vision` · `apriltag` · `mdns` · `frontend` · `other`
Status: `planned` · `in progress` · `done` · `dropped`

## How to create a plan

1. **Read first:** the topic docs under `doc/` that concern the topic — see the table
   below. At least the ones whose interface you are working against.
2. Copy `_vorlage.md` to `<topic>.md` (short, with hyphens, e.g. `opcua-part10-wrapper.md`).
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
| [`../vision-server-interface.md`](../vision-server-interface.md) | OPC UA vision server: address space, methods, backend connection |
| [`../vision-system.md`](../vision-system.md) | Vision system — current state on the Raspberry Pi |
| [`../vision-system-integration.md`](../vision-system-integration.md) | Integration of the vision systems into WebSkillComposition |
| [`../vision-system.md`](../vision-system.md) | Vision server — operation and maintenance |
| [`../apriltag-lokalisierung.md`](../apriltag-lokalisierung.md) | AprilTag localisation, maintainer guide |
| [`../apriltag-referenz.md`](../apriltag-referenz.md) | AprilTag reference |
| [`../code-dokumentation.md`](../code-dokumentation.md) | Code documentation: overview; details per area under `../code/` |
| [`../altlasten-zelle.md`](altlasten-zelle.md) | Clean-up audit of this repo (`apriltag`): dead code, duplicates, workarounds, guidelines, test gaps |
| [`../altlasten-wsc.md`](altlasten-wsc.md) | Clean-up audit of webskillcomposition, only our share (`origin/dev..Ungetestet`) |
| [`../altlasten.md`](altlasten.md) | Known legacy issues and pitfalls (as of 21.09., superseded by the two audits) |
| [`../altlasten-toter-code.md`](altlasten-toter-code.md) | Dead-code audit, as of 21.10. (superseded by the two audits) |
| [`../praesentation.md`](../praesentation.md) | Slide basis for the supervisors' presentation |

If a topic doc is added, add a line here. For agents, the script
`.claude/hooks/doku_regeln.py` lists the folder contents automatically in any case.

## Technical reminder

`.claude/settings.json` runs `.claude/hooks/doku_regeln.py` at every session start,
for every subagent, and before the first write access. The script outputs the rules from
`CLAUDE.md` and lists which topic docs and which running plans currently exist. Everything
lies in the repo — after cloning, it applies automatically for everyone. Without Claude the
folder works just the same; the rule is then followed by hand.

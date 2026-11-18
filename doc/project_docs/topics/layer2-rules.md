# Layer 2 and hand camera calibration: rules

Established on 15.11.2026 (branch `ADP_Lokaliserung`).

## Layer 2: one tag per module

If a module has several tags, Layer 2 approaches only **one** of them, not all.

- The tag with the best viewpoint is chosen, in this order:
  1. reachable before not reachable,
  2. real IK solution before an approximated pose,
  3. distance closest to the target (0.35 m),
  4. as head-on as possible (smallest tilt),
  5. in case of a tie, the nearest tag.
- These are the same criteria as for choosing the world tag for anchoring.
- The module's other tags do not appear in the plan.
- The module pose comes from this one photo: position and yaw angle of the tag, converted to the module via the catalog.
- Implementation: `planApproach(..., { onePerModule: true })` in `frontend/src/features/cell-modules/model/approachPlan.ts`, enabled only in `ApproachPlanPanel.tsx` (Layer 2). The tag reference measurement (`TagReferencePanel.tsx`) still plans all tags.

## Layer 2: unreachable tag is skipped

A tag that cannot be approached or measured is **skipped**. Layer 2 continues with the next tag.

A tag is skipped if:

- planning finds no reachable pose (the tag does not even make it into the route),
- the viewpoint or the path to it would touch the cart or the floor,
- the move to the viewpoint fails (`go_to` reports an error). New since 15.11.2026: previously the whole run ended here. Afterwards the run re-reads the joint angles and approaches the next tag as usual from the home position.
- the tag is not found or not measured during centering,
- a world tag is measured that is not in the catalog.

The skipped tag appears in red in the list, with its reason. The final message
names all skipped tags, e.g.
„Fertig: 4 von 5 Sichtpunkten angefahren, zurück in der Ausgangsstellung. Übersprungen: Tag 7 (Förderband).“
(Done: 4 of 5 viewpoints approached, back in the home position. Skipped: tag 7 (conveyor belt).)

The run still ends completely at:

- **Stopped** (button „Anhalten“ (stop)),
- **Home position not reachable:** the move there fails, or the path there would touch the cart or the floor. Then it is unclear where the arm is, and further moves would not be safe.
- Joint angles not readable.

## Hand camera: one calibration for intrinsics and hand-eye

- In the settings menu, the hand camera now has only **one** button „Kalibrierung“ (calibration). It starts the full calibration: the robot moves through the poses itself, and the Pi computes intrinsics and hand-eye from the same raw images (`fullCalibration: true`). The separate button „Kalibrierung Hand-Eye“ (hand-eye calibration) has been removed.
- Reason: intrinsics and hand-eye belong together. A hand-eye solution does not fit a different intrinsics: in the rotation test of 13.11.2026, the old hand-eye solution with the new intrinsics gave about 2° axis error, versus only 0.25° with its own.
- The ceiling camera keeps its pure intrinsics calibration (`CameraCalibrationModal`), because it has no hand-eye.
- It is started only by button, not automatically. On completion, the Pi computes and applies intrinsics and hand-eye live immediately.

## Old calibrations are kept

New calibration data replaces the active files, but the old ones are not discarded.

- Full calibration of the hand camera: both old files (`data/calibration/<frame>.json` and `data/handeye/<frame>.json`) are first copied next to it as `<name>.<UTC>.bak`. If writing fails, the Pi restores both from the backup.
- Pure intrinsics calibration (ceiling camera): since 15.11.2026 likewise. Previously the file was overwritten without a backup. `result.backup` in the status names the backup. Implemented in the Pi repo `ADP_flexible_Roboterzelle`, branch `apriltag-aufraeumen`, `src/vision_server/calibration_session.py`.
- Each backup gets its own name; none is overwritten. The raw images of each calibration remain under `data/rohbilder/calibration/<UTC>_<id>/` on the Pi.

# Layers 1 and 2

The two localization layers in brief: Layer 1 places the modules coarsely from the ceiling, Layer 2 refines them with the camera on the robot.

| | Layer 1 | Layer 2 |
| --- | --- | --- |
| Measures | pose of every tagged module in the world frame | tag poses at close range, module pose and robot base corrected |
| Camera | Raspberry Pi HQ Camera on the ceiling, fisheye, 4056×3040 | Intel RealSense D415 on the robot flange |
| Pi | ceiling Pi `ADP-Roboter-Lokalisierung` (`vision-ceiling-01`) | hand Pi `ADP-HandInEye-Kamera-Pi` (`vision-flange-01`) |
| Robot | none | the cell robot (e.g. FR3), driven from the WSC frontend |
| Started from | WSC frontend, window `AutolocateLayer1Window` | WSC frontend, panel `ApproachPlanPanel` |

## Layer 1: ceiling camera

Flow:

1. The frontend runs the device action `vision_program` with recipe `apriltag` on the ceiling Pi (`job.start("apriltag")`; the window rescans automatically).
2. The Pi captures `samples_per_job` frames (ceiling: 3) and detects all tags in the cell region.
3. It computes the camera pose from the world tags (`localize_camera`, tag map `config/tagmap.json`).
4. It computes one pose per module (`locate_modules`) and merges the frames.
5. The result `wsc.vision.detections/1` arrives as `LatestResultJson`.
6. The frontend places the modules in the 3D view from it (`layer1Import.ts`).

Result: per module `moduleId`, `position` (m) and `orientation` (quaternion xyzw) in the world frame (`frameId` `world` when a world tag is visible, else the camera frame).

Detail: [`code/cell-vision-server.md`](code/cell-vision-server.md), [`code/cell-tagloc.md`](code/cell-tagloc.md), [`topics/apriltag-localization.md`](topics/apriltag-localization.md), [`topics/vision-system.md`](topics/vision-system.md), [`components/vision-server/README.md`](components/vision-server/README.md), [`components/tagloc/README.md`](components/tagloc/README.md).

## Layer 2: hand camera on the robot

Flow, run from the frontend:

1. The frontend plans one viewpoint per module tag from the Layer 1 poses (`planApproach` in `approachPlan.ts`) with the real inverse kinematics; one tag per module (`onePerModule`).
2. Unreachable viewpoints and viewpoints touching cart or floor are skipped with a reason.
3. It drives the robot to each viewpoint through the backend (`go_to`).
4. The hand Pi measures the tag (`MeasureTags`, averaged over several frames); the frontend centres the camera on the tag (`centreOnTag`) and repeats the measurement.
5. The tag pose in the robot base is `FK(joints) * T_flange_cam * T_cam_tag` (hand-eye calibration `T_flange_cam`).
6. The module pose and the robot base are corrected against Layer 1, then the robot returns to the home position.

Result: corrected module poses, plus a list of done and skipped tags with the reason (German texts such as „Fertig: 4 von 5 Sichtpunkten angefahren, zurück in der Ausgangsstellung. Übersprungen: Tag 7 (Förderband).“).

Notes:

- The hand Pi also has its own run (`StartLayer2Run`, `Layer2Target`, `ReportRobotPose`); the frontend does not use it.
- The hand camera needs intrinsics and hand-eye calibration, both from the single button „Kalibrierung“.

Detail: [`topics/layer2-rules.md`](topics/layer2-rules.md), [`code/webskill-cell-modules.md`](code/webskill-cell-modules.md), [`topics/vision-system.md`](topics/vision-system.md), [`components/webskill-frontend/README.md`](components/webskill-frontend/README.md), [`components/vision-server/README.md`](components/vision-server/README.md).

## Where the two meet

| Topic | Document |
| --- | --- |
| Interface of both Pis | [`interfaces/vision-server-interface-short.md`](interfaces/vision-server-interface-short.md) |
| Using the Pis | [`quickstart.md`](usage/quickstart.md), [`integration.md`](usage/integration.md) |
| Decisions | [`adr/README.md`](adr/README.md) |

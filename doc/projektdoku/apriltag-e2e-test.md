# AprilTag localization end-to-end test

This guide runs through the whole chain once — from the empty machine
to the module pose in the frontend. It is divided into stages that build
on each other. **Every stage has an abort criterion**: if it is not met,
there is no point in trying the next one — the error then sits in a layer
that can no longer be isolated later.

| Stage | Where | Hardware | Answers |
|---|---|---|---|
| [0](#0-preparation) | PC | none | Does the software run at all? |
| [1](#1-stage-1--without-a-camera-on-the-pc) | PC | none | Does the chain compute correctly? |
| [2](#2-stage-2--with-a-webcam-on-the-pc) | PC | Webcam | Does image → pose work? |
| [3](#3-stage-3--on-the-raspberry-pi-with-the-pi-camera) | Pi | **Pi camera** | Does it work in the cell? |
| [4](#4-stage-4--in-the-frontend) | Pi + frontend | Pi camera | Can the operator see what is happening? |

Related documents: [`apriltag-lokalisierung.md`](apriltag-lokalisierung.md) (why it is built
this way), [`apriltag-referenz.md`](apriltag-referenz.md) (function and CLI
reference), [`vision-server-interface.md`](vision-server-interface.md) (OPC UA side).

---

## 0. Preparation

### 0.1 Environment on the PC

The environment in this repo is ready to use (OpenCV 5.0.0, numpy 2.5.3,
asyncua 2.0.1, pupil-apriltags) — skip straight to the check below. The old,
broken `.venv` is located next to it as `.venv.kaputt/` and can be deleted.

> **On another machine:** `setup.sh` does **not** create a venv; it only sets
> up the ShareLaTeX remote. So create one yourself — remove any old
> `.venv` directory, then

```bash
cd ADP_flexible_Roboterzelle
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Check — both lines must print something:

```bash
.venv/bin/python3 -c "import cv2, numpy; print('opencv', cv2.__version__, '| numpy', numpy.__version__)"
.venv/bin/python3 -c "import cv2; print('aruco ok:', hasattr(cv2.aruco, 'DICT_APRILTAG_36h11'))"
```

**Abort criterion:** `aruco ok: True`. Without `opencv-contrib-python`,
`cv2.aruco` is missing entirely; the regular `opencv-python` is not enough.

### 0.2 Print the tags

```bash
PYTHONPATH=src .venv/bin/python3 tools/make_tag_sheet.py \
    --ids 0 1 2 3 7 12 --size-mm 100 --out /tmp/tags.svg
```

The output is SVG in real millimetres — open it in the browser and print it,
setting **scale to "100 % / Originalgröße" (100 % / actual size)**, not
"an Seite anpassen" (fit to page). Each sheet has a 100 mm scale bar at the
bottom: **measure it with a ruler after printing.** If it does not match, the
printer has scaled the output and all tags on it are unusable.

Then the most important point of this whole guide:

> **Measure the printed edge with a ruler and enter the measured value in the
> tag map, not the ordered one.**

The edge meant is that of the **black square** (including the black border,
excluding the white margin around it). A printer driver that silently scales
to 96 % produces a 4 % distance error — at 1.5 m that is 6 cm, and everything
else still looks perfectly plausible.

Mount the tags flat. A bulge of 2 mm over a 100 mm edge length tilts the
estimated orientation by several degrees.

### 0.3 Calibration board

A ChArUco board is preferable to a chessboard: it is detected even when it
partly protrudes from the image — and that is exactly what is needed to cover
the image edges.

```bash
PYTHONPATH=src .venv/bin/python3 tools/make_tag_sheet.py \
    --charuco --cols 7 --rows 5 --square-mm 30 --marker-mm 22 --out /tmp/board.svg
```

Glue it onto stiff cardboard. A wavy board is the most common reason for a
calibration with good RMS and poor poses.

---

## 1. Stage 1 — without a camera on the PC

Nothing is photographed here. The images are rendered, the true poses are
known exactly, and every deviation is a computation error.

### 1.1 Automated tests

```bash
PYTHONPATH=src .venv/bin/python3 -m unittest discover -s tests -t .
```

**Abort criterion:** `OK`, and `test_tag_pipeline` must **not** show up as
`skipped`. A skipped pipeline test means cv2 is missing — in that case nobody
is checking the detection chain.

Individually, if something is red:

```bash
PYTHONPATH=src .venv/bin/python3 -m unittest tests.test_tag_geometry -v    # maths
PYTHONPATH=src .venv/bin/python3 -m unittest tests.test_tag_map -v         # placement
PYTHONPATH=src .venv/bin/python3 -m unittest tests.test_tag_pipeline -v    # image -> pose
PYTHONPATH=src .venv/bin/python3 -m unittest tests.test_apriltag_source -v # server source
```

### 1.2 Run through the tools once by hand

```bash
mkdir -p /tmp/e2e
PYTHONPATH=src .venv/bin/python3 tools/make_synthetic_scene.py \
    --mode tags --count 12 --out /tmp/e2e/bilder \
    --calibration /tmp/e2e/calib.json

PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source /tmp/e2e/bilder --calibration /tmp/e2e/calib.json --limit 1

PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.build_tagmap \
    --source /tmp/e2e/bilder --calibration /tmp/e2e/calib.json \
    --anchor 0 --out /tmp/e2e/tagmap.json
```

**Abort criterion:** `build_tagmap` places all tags, the list
"ohne Pfad zum Anker" (without path to the anchor) is empty, and the closure
errors are **below 1 mm and 0.05°**. With rendered images there is no
measurement uncertainty — larger values are a computation or convention error,
not noise. For comparison, on this scene 0.00–0.02 mm and 0.000° were measured.

---

## 2. Stage 2 — with a webcam on the PC

From here on there is optics, noise and motion blur. This stage is optional,
but it saves time: every error you find here would otherwise have been hunted
for on the ladder under the ceiling.

```bash
# calibrate: move the board slowly through the image, including into the corners
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.calibrate \
    --source camera:0 --board charuco --cols 7 --rows 5 \
    --square-size-m 0.030 --marker-size-m 0.022 \
    --frame-id webcam --out data/calibration/webcam.json \
    --capture-to /tmp/e2e/webcam_bilder

# detect, with live overlay
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source camera:0 --calibration data/calibration/webcam.json \
    --tag-size-m 0.100 --overlay
```

Keys during calibration: `SPACE` captures, `C` computes and saves, `Q` aborts.

**Abort criteria:**
- RMS reprojection error **below 0.5 px**
- Coverage in x **and** y above **70 %** (shown at the end of the calibration).
  A calibration made only from the image centre has good RMS and unusable
  distortion at the edges — exactly where the modules are.
- In the overlay (CLI window, `--overlay`) the axis cross sits **inside** the
  tag, not beside it, and does not tilt when the tag is slowly rotated. The
  live stream shows only the box and ID, no axis cross

---

## 3. Stage 3 — on the Raspberry Pi with the Pi camera

The stage that matters. Everything before it was preparation.

### 3.1 Check the camera at all

First the camera, then the software. On Bookworm:

```bash
rpicam-hello --list-cameras     # Bullseye: libcamera-hello --list-cameras
rpicam-still -o /tmp/test.jpg   # Bullseye: libcamera-still
```

**Abort criterion:** The camera is listed and `/tmp/test.jpg` is sharp and
correctly exposed. No algorithm makes a blurred image better — with fixed-focus
modules the working distance decides, with autofocus the focus has to be right.

### 3.2 Environment on the Pi

`picamera2` comes via apt, not pip. The venv must see the system packages:

```bash
sudo apt update && sudo apt install -y python3-picamera2
cd ~/ADP_flexible_Roboterzelle
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python3 -c "from picamera2 import Picamera2; print('picamera2 ok')"
.venv/bin/python3 -c "import cv2; print('aruco ok:', hasattr(cv2.aruco,'DICT_APRILTAG_36h11'))"
```

> Without `--system-site-packages`, `picamera2` is invisible inside the venv,
> `SharedCamera` fails on opening with `ModuleNotFoundError`, and the server
> stays in `Preoperational` — without it being obvious that this has anything
> to do with the camera.

### 3.3 Calibrating with the Pi camera

> **Stop the server first.** `tagloc.cli.calibrate` opens the camera itself and
> exclusively -- just like `SharedCamera` in the running server. Both at the
> same time does not work (RSUSB/libuvc or Picamera2 allow only one open access).
> So while calibrating there is **no** live stream in the frontend; run
> `sudo systemctl stop opcua-server.service` beforehand and `start` afterwards.

The Pi usually has no screen. Two ways, here for the ceiling Pi
(`--source picamera`) — for the hand Pi use `--source realsense --resolution 640 480`
instead of `--source picamera --resolution 2028 1520`, otherwise identical:

**Option A — with a display (X11 forwarding or VNC), interactive:**

```bash
ssh -X pi@pi-decke
cd ~/ADP_flexible_Roboterzelle
sudo systemctl stop opcua-server.service
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.calibrate \
    --source picamera --resolution 2028 1520 \
    --board charuco --cols 7 --rows 5 --square-size-m 0.030 --marker-size-m 0.022 \
    --frame-id cam_ceiling --out data/calibration/cam_ceiling.json \
    --capture-to data/calibration/aufnahmen_decke
sudo systemctl start opcua-server.service
```

**Option B — without a display, two steps.** More robust, and with a side
benefit: the images are kept, and the calibration can be reproduced exactly
on the PC.

```bash
# on the Pi: stop the server, then only capture
sudo systemctl stop opcua-server.service
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.calibrate \
    --source picamera --resolution 2028 1520 --no-gui \
    --board charuco --capture-to data/calibration/aufnahmen_decke \
    --frame-id cam_ceiling --out data/calibration/cam_ceiling.json
sudo systemctl start opcua-server.service

# recompute on the PC (identical computation core)
scp -r pi@pi-decke:~/ADP_flexible_Roboterzelle/data/calibration/aufnahmen_decke /tmp/
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.calibrate \
    --source /tmp/aufnahmen_decke --board charuco \
    --frame-id cam_ceiling --out data/calibration/cam_ceiling.json
scp data/calibration/cam_ceiling.json pi@pi-decke:~/ADP_flexible_Roboterzelle/data/calibration/
```

When capturing: **at least 15, better 20 views**, board tilted each time (±30°)
and once moved through all four image corners. A board that always stands
parallel to the image plane leaves the focal length undetermined — the
calibration still converges and returns wrong values.

**Abort criterion:** RMS below **0.5 px**, coverage in x and y above **70 %**.
The file must be named exactly `data/calibration/<frame_id>.json`, i.e.
`cam_ceiling.json` for the ceiling and `cam_flange.json` for the flange — the
server looks for it under that name.

### 3.4 Check detection live

The server stays stopped (see 3.3) — here too the CLI tool opens the camera
exclusively. For the hand Pi again `--source realsense --resolution 640 480`.

```bash
# with display
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source picamera --resolution 2028 1520 \
    --calibration data/calibration/cam_ceiling.json \
    --tag-size-m 0.100 --overlay

# without display: one frame, result as text and JSON
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source picamera --resolution 2028 1520 \
    --calibration data/calibration/cam_ceiling.json \
    --tag-size-m 0.100 --limit 1 --json /tmp/posen.json
```

### 3.5 The two measurements that really prove something

A pose always looks plausible. These two tests show whether it is correct —
both need only a folding ruler.

**Measurement A — scale.** Reveals a wrong tag size and a wrong calibration.
Glue two tags onto a board at a known distance, measure centre to centre (e.g.
exactly 400 mm), capture both in one image and compute the distance from the
measured poses:

```bash
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source picamera --calibration data/calibration/cam_ceiling.json \
    --tag-size-m 0.100 --limit 1 --json /tmp/zwei.json

PYTHONPATH=src .venv/bin/python3 - <<'PY'
import json, math
poses = json.load(open("/tmp/zwei.json"))["poses"]
a, b = poses[0]["pose"]["position"], poses[1]["pose"]["position"]
print("gemessener Abstand:", round(math.dist(a, b) * 1000, 1), "mm")
PY
```

**Acceptance:** deviation from the nominal value below **1 %**. If it is larger
and systematic, the entered tag size is wrong — it enters the distance linearly.

**Measurement B — repeatability.** Reveals noise, blur and poor lighting. Keep
the tag fixed, measure 50 frames, look at the scatter:

```bash
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source picamera --calibration data/calibration/cam_ceiling.json \
    --tag-size-m 0.100 --limit 50 | tee /tmp/serie.txt
```

**Acceptance Layer 1** (ceiling, approx. 2 m, 100 mm tags): standard deviation
laterally below **2 mm**, in depth below **10 mm**, orientation below **1°**.
**Acceptance Layer 2** (flange, approx. 0.3 m, 50 mm tags): laterally below
**0.5 mm**, depth below **2 mm**, orientation below **0.5°**.

For a single marker, depth is always the worst axis. That is physics, not a
fault: depth follows from the apparent size, and that changes only slowly with
distance. That is exactly why `ambiguous` appears in the result — if you look at
the tag too flatly, the solution flips.

### 3.6 Capture the tag map

Now bring the tags in the room into a common coordinate system. Capture images
such that **every image shows at least two tags** and the pairs form a chain
to the anchor tag:

```bash
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.build_tagmap \
    --source data/aufnahmen_zelle \
    --calibration data/calibration/cam_ceiling.json \
    --tag-map config/tagmap.json --anchor 0 --out config/tagmap.json
```

Then post-edit the file by hand: set roles (`world` for the four world tags,
`robot` for the tags on the robot, otherwise `module`), set `poseInWorld` to
`null` for the movable tags, and enter the module assignment (`moduleId`,
`instanceId`) and the CAD offset `tagToModule`. Template:
[`config/tagmap.example.json`](../../config/tagmap.example.json), field
description in [`apriltag-referenz.md`](apriltag-referenz.md) section 5.

#### Small setup test: one world tag, one module

For the first functional test the four world tags are **not** needed. One world
tag and one module tag are enough, and `build_tagmap` is skipped entirely: if
the world tag lies at the origin with identity pose, it *is* the world
coordinate system (CS), so there is nothing to survey.

Template for this:
[`config/tagmap.test.example.json`](../../config/tagmap.test.example.json) — copy
it to `config/tagmap.json`, adjust the tag IDs and the **measured** edge
lengths, done:

```bash
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source picamera \
    --calibration data/calibration/cam_ceiling.json \
    --tag-map config/tagmap.json --save-overlay overlays/
```

On startup `validate_tag_map` reports two complaints — "Erwartet 4 Welttags
… gefunden 1" (expected 4 world tags … found 1) and "Kein Tag mit der Rolle
'robot'" (no tag with role 'robot'). These are **not an abort**, but exactly the
hint that the final setup is still missing; detection runs in full. The second
message disappears as soon as a robot tag is in the map.

What this setup does **not** provide: `cameraSpreadM` always stays 0 with a
single world tag. The number measures the disagreement *between* world tags, so
there is no cross-check here — the accuracy depends entirely on this one tag and
its entered size. The value only becomes meaningful with the second world tag.

For the hand camera, without a measured hand-eye file, the following still
applies: world tag and module must lie in the **same image**. Anchoring
(section 1.4 of the [concept doc](apriltag-lokalisierung.md)) only takes effect
with `data/handeye/cam_flange.json`.

**Abort criterion:** closure error below **3 mm** and **0.5°**. Larger values
mean: the calibration or an entered tag size is wrong. A map that does not close
is worthless — then go back to step 3.3.

### 3.6a Calibrate and anchor hand-eye (hand camera / Layer 2 only)

Without this step the hand camera returns poses in the camera CS as soon as no
world tag lies in the same image (see above). For `frameId: world` without the
world-tag requirement it needs `data/handeye/cam_flange.json`:

```bash
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.calibrate_handeye \
    --samples fahrt/aufnahmen.json \
    --calibration data/calibration/cam_flange.json \
    --out data/handeye/cam_flange.json
```

Capture beforehand: lay one tag down in a fixed position, let the robot move to
at least a dozen clearly different poses (**rotating about several axes**), take
one image per pose and note `T_base_flansch` — format and procedure in
[`apriltag-referenz.md`](apriltag-referenz.md) section 9.3a.

**Acceptance criteria:**

| Check | Expectation |
|---|---|
| `target_spread` (output of `calibrate_handeye`) | below `SPREAD_WARNING_M` (5 mm); otherwise record again with more varied poses |
| Job without a world tag in the image, with hand-eye file | `frameId = world`, payload carries `anchorWorldTagId` and `attributes.cameraPoseOrigin = "robot_pose"` |
| Bring a world tag back into the image | `cameraPoseOrigin` switches back to `"world_tags"`, `anchorDriftM`/`anchorDriftDeg` show the deviation from the anchor |
| Hand-eye file removed/moved | server still starts, Layer 2 falls back to the camera CS (no crash) |

### 3.7 Start the server and measure via OPC UA

```bash
PYTHONPATH=src .venv/bin/python3 -m vision_server.server
```

The log must show:

```
Vision-Identitaet: vision-ceiling-01 (Rahmen cam_ceiling)
VisionSystem 'VisionMachine' als ns=6;s=VisionMachine unter Machines angelegt
Livestream aus Profil 'apriltag' mit Overlay
Vision-System 'VisionMachine' bereit (Profile apriltag, calibration, hello_world)
```

> If it says **"bleibt in Preoperational"** (stays in Preoperational), a source
> has not opened. Almost always the calibration file is missing, or `picamera2`
> is invisible in the venv (see 3.2). The cause is one line above in the log.

Trigger jobs — first the readiness check, then the measurement:

```bash
PYTHONPATH=src .venv/bin/python3 - <<'PY'
import asyncio
from asyncua import Client, ua

async def main():
    async with Client("opc.tcp://pi-decke:4840/freeopcua/server/") as client:
        vis = await client.get_namespace_index("http://launch-rm.de/vision")
        mv = await client.get_namespace_index("http://opcfoundation.org/UA/MachineVision")
        machine = client.get_node(ua.NodeId("VisionMachine", vis))
        sm = await machine.get_child(
            [f"{mv}:VisionStateMachine", f"{mv}:AutomaticModeStateMachine"]
        )
        start = await sm.get_child(f"{mv}:StartSingleJob")
        for recipe in ("calibration", "apriltag"):
            job_id, error = await sm.call_method(start, "", "", recipe, "", [])
            print(f"{recipe:>12}: JobId={job_id} Error={error}")
            await asyncio.sleep(3.0)
        results = await machine.get_child([f"{mv}:ResultManagement", f"{mv}:Results"])
        for node in await results.get_children():
            print((await node.read_browse_name()).Name)

asyncio.run(main())
PY
```

**Acceptance criteria:**

| Check | Expectation |
|---|---|
| `calibration` job | `attributes.message` names calibration ID, resolution, RMS and tag map |
| `apriltag` job, Error | `0` |
| `resultState` | `0` |
| `detections` | one detection per visible module tag |
| `frameId` | `world` as soon as a reference tag is in the image; otherwise `cam_ceiling` |
| `frameConvention` | `z_forward_x_right_y_down` |
| `configurationId` | contains tag family, calibration **and** tag-map identity |
| `attributes` | at least `tagId`, `reprojErrorPx`, `ambiguous`, `sampleCount`, `recipeId`, `source`, `role`; additionally `referenceTagId`/`referencePosition`/`referenceOrientation`/`referenceDistanceM` as soon as a world tag is known as reference, `worldTagIds`/`cameraSpreadM`/`cameraSpreadDeg`/`cameraPoseOrigin` as soon as the camera is localized, `anchorWorldTagId`/`anchorDriftM`/`anchorDriftDeg` with a hand-eye anchor — full list in [`apriltag-referenz.md`](apriltag-referenz.md) section 10.2 |
| `sampleCount` | equal to the profile's `samples_per_job` (ceiling 3, flange 5) |
| No tag in the image | `resultState = 5` (`DETECTION_FAILED`), **not** an empty success result |
| `Stop` during the job | `resultState = 7` (`CANCELLED`) |

And the cross-check that ties the whole setup together: **the pose from the
payload must match the one measured by `tagloc.cli.detect`** (within 1 mm). If
it deviates, stream and job use different calibrations — then compare
`configurationId`.

---

## 4. Stage 4 — in the frontend

The live stream is the debugging tool for everything above. It shows not only
that an image arrives, but what the detection sees in it.

### 4.1 The nodes

| Node | NodeId | Direction | Content |
|---|---|---|---|
| Live stream | `ns=<vision>;s=VisionMachine.LatestCameraFrame` | read only | Base64 JPEG, approx. 5 fps |
| Mode selection | `ns=<vision>;s=VisionMachine.CameraStreamMode` | **writable** | `off`, `apriltag`, `undistorted`, `tagreference` (`calibration` removed 17.11.2026) |

`<vision>` is the index of `http://launch-rm.de/vision` — on the Pi server `4`,
in the standalone vision server `3`. **Always fetch the index at runtime via
`get_namespace_index`, never hard-code it.**

### 4.2 The modes

> **Removed 17.11.2026:** the mode `calibration` ("Kalibrierboard markieren", mark
> calibration board) no longer exists; an unknown value falls back to `apriltag`.
> `undistorted` and `tagreference` are described in `vision-server-interface.md`
> section 10.1.


| Value | Display label | What is marked |
|---|---|---|
| `off` | "Rohbild" (raw image) | nothing — the unchanged camera image |
| `apriltag` | "AprilTags markieren" (mark AprilTags) | for each detected tag only a **green box** (outline) and the ID `#<id>`, no status bar. Axis cross, module name, distance, reprojection error and orange colouring of ambiguous poses are only available in the detailed CLI overlay (`python -m tagloc.cli.detect --overlay`) |

Switching is a simple write access:

```python
mode_node = client.get_node(ua.NodeId("VisionMachine.CameraStreamMode", vis))
await mode_node.write_value("off")
```

An invalid value does **not** stop the stream — it falls back to `apriltag`.
This is intentional: the image matters more than the marking.

### 4.3 What still remains to be done in the frontend

The server side is done; the frontend (`webskillcomposition`) still needs the
selection. The contract is exactly the one above: a dropdown with the three
values that writes `CameraStreamMode` on change. The live-stream node itself
stays unchanged — the existing `subscribeNode` infrastructure continues to carry
it. The labels are on the server side in `tagloc.modes.OVERLAY_MODE_LABELS`.

### 4.4 Acceptance

- Image arrives, approx. 5 fps, no delay that grows over minutes
- Switching to `apriltag` shows markings within about one second
- the green box sits on the tag and follows it when the tag is moved; `#<id>`
  stands beside it. The axis cross is not checked in the stream but with
  `python -m tagloc.cli.detect --source camera --overlay`
- Switching to `off` immediately shows the raw image again
- a running `apriltag` job does **not** interrupt the stream — both read from the
  same `SharedCamera`
- no message "Event-Loop … ms blockiert" (event loop … ms blocked) appears in the server log

> On the last point: the overlay detects by itself at most every
> `overlay_detect_interval_s` (default 5 s) and in between keeps drawing the last
> result. With a fixed camera you see nothing of this. If loop warnings still
> appear: increase `overlay_detect_interval_s` or lower the stream resolution.
> (`overlay_interval_s` was removed on 17.11.2026 together with the `calibration`
> stream mode.)

---

## 5. Quick reference: troubleshooting

| Symptom | What to check first |
|---|---|
| Distance systematically off by a fixed factor | printed tag size ≠ entered `sizeM` (see 0.2) |
| All poses wrong, but detection stable | calibration belongs to a different resolution — the source reports both sizes |
| Pose flips between two positions | `ambiguous: true`, viewing angle too flat |
| Poses at the image edge much worse | calibration coverage too low — look at the coverage, not the RMS |
| `build_tagmap` finds fewer tags than captured | no path to the anchor; an image with two already-connected tags is missing |
| Server stays in Preoperational | missing calibration file or `picamera2` invisible (3.2); cause is in the log |
| Live stream gone, although configured | no open source holds a camera |
| Stream running, but without markings | source without loaded detector/calibration; log says "ohne Overlay" (without overlay) |
| `DETECTION_FAILED` despite a visible tag | wrong tag family, or all tags discarded above `max_reproj_error_px` — visible with `-v` in the CLI |

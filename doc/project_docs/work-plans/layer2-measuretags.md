# Layer 2: MeasureTags — measure tags in camera coordinates

**Status:** done — 15.10.2026 (not tested on real hardware)
**Owner:** `Agent: MeasureTags on the hand Pi`; frontend: session `opcua-4c` (`webskillcomposition`, branch `Ungetestet`)
**Topic:** apriltag
**Branch:** apriltag

## Goal

Layer 2 is newly split:
- The **controller** (webskillcomposition) plans and moves. From layer 1 it calculates, for each tag, a camera pose about 35 cm in front of the tag and moves there.
- Afterwards it corrects in a stop-and-go manner: move → standstill → measure → `T_base_tag = FK(joints) · T_flansch_cam · T_cam_tag` → new flange target.
- The **hand Pi only measures.** It plans nothing and knows neither the robot nor the kinematics.

`StartLayer2Run` and the anchoring remain in place, but are not used for now.

## Already read

- `layer2-run-hand-pi.md`, `layer2-approach.md`, `layer2-oblique-view.md`
- `hand-eye-over-opcua.md`: `measure_tag` is the same calculation, so `T_flansch_cam` fits it
- `vision-server-interface.md` sections 14, 15

## Affected files

- `src/vision_server/measuretag.py` (new): request, merging, result JSON
- `src/vision_server/detection/apriltag.py`: `measure_tags`, `intrinsics_source`, fallback to the factory intrinsics in `open()`
- `src/vision_server/camera.py`: `SharedCamera.factory_calibration`, `_realsense_calibration`
- `src/vision_server/address_space.py`: node `LastTagMeasurement`
- `src/vision_server/runner.py`: `_install_measure_tags`
- `tests/test_measuretag.py` (new)

## Interfaces

### OPC UA

The elements exist only on the hand Pi, i.e. with `hand_eye_path`. They lie under `VisionMachine` and are linked under `VisionProgram`.

| Element | NodeId | Type |
| --- | --- | --- |
| MeasureTags | `ns=<vision>;s=VisionMachine.MeasureTags` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)` |
| LastTagMeasurement | `ns=<vision>;s=VisionMachine.LastTagMeasurement` | String (JSON), display only |

The method only replies after the measurement. The result comes back **directly**, so there is no race for a result node. `ResultJson` is always valid JSON, including on a rejection (then with `tags: []` and `error`).

| Error | When |
| --- | --- |
| `OK` | measured. Also when no tag was in the image (`tags: []`). |
| `DETECTION_FAILED` | no fresh camera image (`error: "NO_FRAME"`) |
| `INVALID_ARGUMENT` | JSON or values invalid |
| `INVALID_STATE` | vision system not `Operational` |
| `BUSY` | a job or camera calibration is running (an open hand-eye session does not block, since 22.10.2026) |

### Data

**Request**, `wsc.vision.measuretag.request/1`. All fields are optional; `""` or `{}` is valid.

```json
{ "schema": "wsc.vision.measuretag.request/1",
  "tagIds": [7] | null, "tagSizeM": {"7": 0.08}, "frames": 3 }
```

- `tagIds`: `null` means all detected tags.
- `frames`: 1–10, default 3. This many fresh images are averaged.

**Result**, `wsc.vision.measuretag.result/1`:

```json
{ "schema": "wsc.vision.measuretag.result/1",
  "capturedAt": "2026-10-15T12:00:00.123+00:00", "capturedUntil": "…",
  "frameId": "cam_flange", "frameConvention": "z_forward_x_right_y_down",
  "imageSize": [640, 480],
  "intrinsicsSource": "calibration" | "realsense_factory" | "placeholder", "calibrationId": "…",
  "framesRequested": 3, "framesUsed": 3,
  "tags": [ { "tagId": 7,
              "poseInCamera": { "position": [x,y,z], "orientation": [qx,qy,qz,qw] },
              "centerPx": [u,v], "cornersPx": [[u,v],[u,v],[u,v],[u,v]],
              "reprojectionErrorPx": 0.4, "decisionMargin": 55.0,
              "sizeM": 0.08, "sizeSource": "request" | "tagmap" | "default",
              "ambiguous": false, "ambiguityRatio": 0.3, "frameCount": 3,
              "distanceM": 0.351, "viewAngleDeg": 4.2, "nearEdge": [] } ],
  "error": null | "NO_FRAME" }
```

- **`poseInCamera`** is `T_cam_tag`. Camera CS as in OpenCV: +Z out of the lens, +X right, +Y down. Tag CS as everywhere: +Z out of the tag. There is no world transformation and no CAD offset.
- **Only fresh images:** measurement only uses images that arrive **after** the call. An image from the motion is never used.
- **Merging per tag:**
  - The position is the mean over all images containing the tag.
  - The orientation is the mean over the unambiguous images. If there are none, the mean over all images is used and `ambiguous: true` is set. Then the rotation is **not** controlled; the position remains reliable.
- **`frameCount`:** in how many images the tag was seen. The controller does not take a control step below 2.
- **`capturedAt`/`capturedUntil`:** arrival of the first and last image on the Pi, in UTC. Measurement happens on arrival, not at exposure. This accounts for roughly one image period.
- **`cornersPx`/`centerPx`/`decisionMargin`:** from the last image. The pixels are raw (distorted). The order of the corners is that of `tag_object_points`: top left, clockwise.
- **`sizeM`/`sizeSource`:** the size used for the calculation. Priority goes to the request, then the tag map, then the fallback of 8 cm. A wrong size scales the distance linearly.
- **`viewAngleDeg`:** angle between the tag normal and the line of sight, 0 = frontal.
- **`nearEdge`** (added 19.10.2026): the image edges (`left`, `right`, `top`, `bottom`) that a corner of the tag in the last image comes closer to than 5 % of the image size. For the search for initially unreachable tags: the controller approaches as far as possible, measures, and pans in a targeted way in this direction when a tag is near an edge. A cut-off tag is not detected at all, which is why the hint is given beforehand, while the tag is still fully in the image.

### Intrinsics

The calibration file `data/calibration/cam_flange.json` takes priority. If it is missing, on the **RealSense** the factory intrinsics of the running pipeline apply (`realsense_factory`, `calibrationId` = `realsense-werksintrinsik`).

Regarding distortion:
- Brown-Conrady from librealsense corresponds to OpenCV and is adopted.
- Other models are adopted only if all coefficients are 0. Otherwise the Pi calculates without distortion and writes a warning to the log.

Without factory intrinsics the placeholder applies, but only with `allow_placeholder_calibration`. Otherwise the source does not open, as before. With `placeholder` the controller does not regulate.

Note: the factory intrinsics also apply to jobs and the live stream of this Pi, not only to `MeasureTags`.

## Failure cases

| Case | Behaviour |
| --- | --- |
| Tag not in the image | `tags: []`, `OK` |
| No fresh image within `capture_timeout_s` | `error: "NO_FRAME"`, `DETECTION_FAILED`. If some images were already evaluated, those count (`framesUsed` < `framesRequested`). |
| Tag ambiguous | pose comes with `ambiguous: true` |

## Open questions

- Not tested on real hardware. Whether the factory intrinsics of the installed RealSense report Brown-Conrady only shows on the Pi.

## After completion

- [x] Status `done`, interfaces entered.
- [x] Index line in `README.md`.
- [x] `vision-server-interface.md` section 17.
- [x] Tests: `tests/test_measuretag.py` (14).

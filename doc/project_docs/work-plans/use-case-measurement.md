# Use-case measurement: placement of the cube from the hand-camera image (Δx, Δy, Δθ)

**Status:** in progress — 29.10.2026
**Owner:** Meric, `Agent: Use-case measurement`
**Topic:** vision
**Branch:** `usecase-foto`

## Goal

After each placement of the cube in use case 1, the position of the cube is
determined from the photo of the hand camera: deviation in x and y (mm) and
rotation Δθ (°) relative to the centre of a printed target. The measurement runs
live after the photo (in `SaveUseCaseImage`) and offline for whole series
(`tools/use_case_auswertung.py`). From this, the accuracy and repeatability of
the placement are derived according to ISO 9283 (loosely based). Previously the
placement was judged only by eye; there were no numbers.

The target keeps its tolerance circles, so that the placement can also be judged
visually without measurement.

## Already read

- `CLAUDE.md`
- `doc/project_docs/work-plans/README.md`, `_template.md`, `use-case-photo.md`
- `doc/project_docs/interfaces/vision-server-interface.md` (section 18a SaveUseCaseImage)
- Code: `src/use_case_eval/` (`target.py`, `measure.py`, `live.py`, `synthetic.py`),
  `tools/make_use_case_target.py`, `tools/use_case_auswertung.py`,
  `src/vision_server/use_case_image.py`, `_install_save_use_case_image` und
  `_build_use_case_pi_meta` in `src/vision_server/runner.py`

## Current state

- Code and tests are on branch `usecase-foto` (not pushed). The tests
  `tests.test_use_case_eval` and `tests.test_use_case_image` as well as the
  entire suite pass (green).
- Frontend (photo pose, display of the measurement) is implemented:
  webskillcomposition, branch `usecase-foto`, see section "Camera pose" below.
- Nothing has been measured on the real setup yet. The accuracy values below come
  from synthetic images.

## Affected files

- `src/use_case_eval/target.py` (new): geometry of the target, single source
- `src/use_case_eval/measure.py` (new): image measurement, key figures, sticker offset
- `src/use_case_eval/live.py` (new): connection to `SaveUseCaseImage`
- `src/use_case_eval/synthetic.py` (new): synthetic test images
- `src/use_case_eval/__init__.py` (new)
- `tools/make_use_case_target.py` (new): generates the target
- `tools/use_case_auswertung.py` (new): offline evaluation of a series
- `src/vision_server/runner.py`: `_install_save_use_case_image` measures before saving
- `src/vision_server/use_case_image.py`: field `messung`, control image
- `tests/test_use_case_eval.py` (new), `tests/test_use_case_image.py`
- `data/use_case_target/` (generated), `data/use-case/` (trials; `data/` has been
  under version control since 02.11.2026, only commit deliberately)
- `doc/project_docs/interfaces/vision-server-interface.md` (section 18a)

## Target v2

The geometry is in `src/use_case_eval/target.py`. All values in mm, sheet
coordinates: origin at the centre of the target, x points to 0°, y to 90°
(upwards on the printout), z upwards out of the sheet.

| Element | Value |
| --- | --- |
| Scale ring | Radius 80 to 92 mm |
| Tolerance circles | Ø 30.3 (green), 34.3 (yellow), 38.3 (red) mm |
| Cube | 20 mm, dashed square in the centre |
| Sheet markers | ArUco `DICT_4X4_50`, IDs 0 to 3, each 20 mm, centres at (−45, +45), (+45, +45), (+45, −45), (−45, −45) mm, edges parallel to the sheet axes |
| Cube sticker | ArUco `DICT_4X4_50`, ID 10, 18 mm, with 20 mm cutting frame |

- **Why ArUco and not AprilTag 36h11:** The cell pipeline (layers 1/2, hand-eye,
  tag map) detects `tag36h11`. With ArUco it does not see the target, and vice versa.
- Around each sheet marker lies a white quiet zone of one marker cell. Angle
  scale, rays and tick marks are left out there.
- The target has a legend and a 50 mm scale bar.
- **Zones:** The outermost cube corner is checked against the radii 15.15 /
  17.15 / 19.15 mm. If it lies within 15.15 mm, the zone is `gruen` (green);
  within 17.15 mm `gelb` (yellow); within 19.15 mm `rot` (red); otherwise
  `ausserhalb` (outside). This corresponds to what is seen on the sheet.

### Generate

```bash
PYTHONPATH=src python3 tools/make_use_case_target.py --pdf
```

Output to `data/use_case_target/`: `zielscheibe.svg`, `wuerfelmarker.svg` and,
with `--pdf`, the PDFs (needs `inkscape` or `rsvg-convert`). The SVGs carry real
millimetres. Optionally `--stickers N` for the number of stickers on page 2
(default 12), `--out DIR` for another folder.

### Printing

- Print at **100 %**: in the print dialog choose "Tatsächliche Größe" (actual
  size) or scale 100 %, no fitting to the page.
- Measure the 50 mm scale bar on the printout with a ruler. If it deviates, do
  not measure — reprint.

## Setup

- **Sheet:** The sheet centre lies on the nominal placement point of the app. The
  0° axis lies parallel to the table x edge.
- The measurement is relative to the sheet. An offset of the sheet relative to the
  app placement shifts all measured values equally. This enters the **accuracy**
  (AP), not the repeatability (RP). Therefore lay the sheet the same way for every
  series and fix it in place.
- **Cube sticker:** glue centrally on the top face, offset at most about 1 mm. The
  18 mm marker must lie entirely on the 20 mm top face. Otherwise the edge is
  measured truncated.
- **Calibrate the sticker offset:** The sticker is never exactly centred. The
  offset is a constant error and is determined once:
  1. Place the cube on the placement point and measure (trial N1).
  2. Turn the cube by hand through 180°, place it at the same point and measure (trial N2).
  3. Run:
     ```bash
     python3 tools/use_case_auswertung.py data/use-case/reihe_NNN --aufkleber-kalibrieren N1 N2
     ```
     This writes `data/use-case/aufkleber_versatz.json`. The arguments are trial
     numbers or image files of the series.
  For existing series, then re-evaluate with `--neu-messen`.

### Camera pose (frontend, branch `usecase-foto`, webskillcomposition)

- The hand camera stands **20 cm vertically above the nominal centre** (placement
  point). The reference is the table surface or the sheet: optical centre at
  `placeInBase + (0, 0, 0,20 m)`, i.e. 18 cm above the cube top. The optical axis
  points downward, image x is parallel to the table x axis.
- The pose comes from `poseFlangeCam` of the hand-eye calibration. It is fixed and
  is not regulated at runtime (no visual servoing).
- The flange is computed from the camera pose:
  `flange = composePoses(cameraInBase, invertPose(flangeToCamera))`.
- If the pose is not reachable, the sequence tries a rotation of the camera about
  the optical axis by 90°, −90° and 180°. For the measurement the rotation is not
  critical.
- Without a hand-eye calibration the start is blocked (message "Keine
  Hand-Auge-Kalibrierung auf dem Hand-Pi" (no hand-eye calibration on the hand Pi)).
- **Why above the centre:** the foot point of the optical axis then lies on the cube
  top. No side face is visible, so the top is flat and fully in the image. At 20 cm
  height and 640 × 480 px this gives about 0.44 mm per pixel and an image field of
  about 280 × 210 mm.

## Measurement procedure

`measure_trial` in `src/use_case_eval/measure.py`:

1. **ArUco detection:** `tagloc.detector.build_detector("aruco_4x4_50", …)` with
   sub-pixel corners. For duplicate IDs the first find wins.
2. **Edge refinement** (markers of the sheet and cube markers):
   - Cross profiles perpendicular to each edge, sampled bilinearly.
   - The edge position follows from the area under the normalised profile, not from
     a gradient maximum. This is independent of the phase of the edge relative to
     the pixel grid.
   - One line per edge by robust outlier rejection (median distance).
   - Corners as intersections of adjacent lines, undistorted (`tagloc.lens`, pinhole
     and fisheye).
   - Two stages: first around the ArUco corners (max. 4 px deviation), then around
     the refined position (max. 1 px). If a stage fails, the ArUco corners apply.
     `cube_edge_refined` and `sheet_edges_refined` show what was used.
3. **Sheet pose:** `cv2.solvePnP` with `SOLVEPNP_IPPE`, followed by
   `solvePnPRefineLM` over all corners of the detected sheet markers (at least 2).
   The reprojection RMS is in `reprojection_rms_px`.
4. **Cube:** The four undistorted corners of marker 10 are computed as rays from the
   camera centre into the sheet system and intersected with the plane z = 20 mm
   (cube top).
5. **Centre and angle:** The marker centre is the mean of the four intersection
   points. The edge directions are averaged circularly (modulo 90°), from which Δθ,
   wrapped to [−45°, 45°). Positive is counter-clockwise (sheet, y upwards).
6. **Sticker offset:** The cube centre is the marker centre, minus the offset, which
   is rotated into the sheet axes.
7. **Plausibility:** The camera must stand above the plane z = 20 mm, otherwise
   `ok=False` with the reason "Blattpose unplausibel" (sheet pose implausible).

Errors (no cube marker, too few sheet markers, pose not solvable, image size does
not match the calibration, exception) return `ok=False` with a German `grund`
(reason). `measure_trial` never raises.

### Result fields `TrialMeasurement`

| Field | Meaning |
| --- | --- |
| `ok`, `grund` | Measurement valid; otherwise reason as text |
| `dx_mm`, `dy_mm` | Cube centre relative to target centre, sheet x/y, sticker offset subtracted |
| `dtheta_deg` | Yaw angle of the cube, modulo 90°, to [−45, 45) |
| `r_mm` | Distance of the cube centre to the target centre |
| `zone` | `gruen` / `gelb` / `rot` / `ausserhalb`, after the outermost cube corner |
| `sheet_markers` | IDs of the detected sheet markers |
| `reprojection_rms_px` | RMS of the reprojection of the sheet markers (quality of the sheet pose) |
| `camera_height_mm` | Height of the camera centre above the sheet plane |
| `camera_tilt_deg` | Angle between optical axis and vertical (0 = straight down) |
| `nadir_offset_mm` | Distance from the point the optical axis hits on the cube top to the cube centre. Small = camera stands above the cube |
| `cube_corners_mm` | Four cube corners in the sheet system, `[[x, y], ...]`, starting top left |
| `marker_center_mm`, `marker_yaw_deg` | Centre of the marker without sticker offset and its full yaw angle (0..360), for the sticker calibration |
| `cube_edge_refined` | Cube marker edges refined to sub-pixel accuracy (otherwise ArUco corners) |
| `sheet_edges_refined` | Number of sheet markers with refined edges |
| `sticker_offset_mm` | sticker offset used in the marker system |

`to_dict()` returns the JSON-compatible form (without overlay), `from_dict()` the inverse.

## Accuracy (synthetic)

Test images from `synthetic.render_trial`: 640 × 480 px, fx = 466, camera 22 cm
above the sheet, supersampling 4. Result in 12 random cases:

| Method | Position | Angle |
| --- | --- | --- |
| with edge refinement | ≤ 0.01 mm | ≤ 0.02° |
| without edge refinement | 0.05 to 0.13 mm | 0.6 to 0.8° |

This is the state on synthetic images. On the real setup the measurement is still
pending (noise, print, blur, distortion of the real camera). The tests demand less
strictly: recovery to < 0.1 mm and < 0.2°.

## Key figures (ISO 9283, loosely based)

`summarize` in `measure.py` over all successful measurements of a series. Failed
trials are counted but not averaged.

- **Accuracy:**
  - `AP_mm` = distance of the mean (dx, dy) from the nominal (target centre).
  - `AP_theta_deg` = |mean Δθ|. The mean is circular modulo 90°.
- **Repeatability:**
  - `l` = distance of each placement to the centre of the placements.
  - `RP_mm` = l̄ + 3 · S_l.
  - `RP_theta_deg` = 3 · S_θ (scatter of the angle about the mean, modulo 90°).
- In addition `dx_mean_mm`, `dx_std_mm`, `dy_mean_mm`, `dy_std_mm`, `dtheta_mean_deg`,
  `dtheta_std_deg`, `n`, `n_gesamt`, `n_fehlgeschlagen`, `zonen` (count per zone).
- S is the sample standard deviation (ddof 1); with only one value, 0.

## Interfaces

### OPC UA

`SaveUseCaseImage` with signature `(Series: Int32, MetadataJson: String) -> (FileName: String, ResultJson: String, Error: Int32)`.
Fully described in [`vision-server-interface.md`, section 18a](../interfaces/vision-server-interface.md).
`ResultJson` is `TrialMeasurement.to_dict()` as JSON text; on error codes `""`.
A measurement error is not an error code.

### Python

```python
# Module: src/use_case_eval/measure.py
def measure_trial(image, calibration, cube_marker_offset_mm: Sequence[float] = (0.0, 0.0), *,
                  refine_edges: bool = True) -> TrialMeasurement:
    """Measures the cube placement. Never raises; errors are in ok/grund.
    image: BGR or grayscale image, size = calibration.image_size.
    calibration: tagloc.calibration.CameraCalibration of the capture."""

def summarize(measurements: Iterable[TrialMeasurement]) -> dict:
    """Key figures according to the scheme above. Without a successful measurement only n, n_gesamt, n_fehlgeschlagen, zonen."""

def sticker_offset_from_pair(m0: TrialMeasurement, m180: TrialMeasurement) -> tuple[float, float]:
    """Sticker offset (mm, marker system) from two measurements before/after a 180° rotation.
    Raises ValueError: measurement not ok, or rotation deviates more than 30° from 180°."""

def annotate(image, measurement: TrialMeasurement):
    """Control image: copy with sheet and cube markers, tolerance circles, cube outline and measured values."""

@dataclass
class TrialMeasurement:  # fields see table above
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "TrialMeasurement": ...  # unknown keys ignored

# Module: src/use_case_eval/target.py (numpy only); constants, among others:
#   CUBE_TOP_Z_MM = 20.0, CUBE_MARKER_ID = 10, CUBE_SIZE_MM = 20.0,
#   ZONE_RADII_MM = (("gruen", 15.15), ("gelb", 17.15), ("rot", 19.15)), SHEET_MARKERS (IDs 0-3)
def sheet_marker_corners(marker_id: int) -> np.ndarray: ...   # (4, 3), sheet coordinates
def cube_corners_xy(center_xy, yaw_deg: float) -> np.ndarray: ...  # (4, 2)
def zone_for_radius(radius_mm: float) -> str: ...

# Module: src/use_case_eval/live.py (connection to SaveUseCaseImage, never raises)
def load_sticker_offset(base_dir: Path) -> tuple[float, float]:
    """Reads <base_dir>/aufkleber_versatz.json; if missing or defective: (0.0, 0.0)."""
def write_sticker_offset(base_dir: Path, offset_mm) -> Path:
    """Writes {"x_mm", "y_mm"} to <base_dir>/aufkleber_versatz.json."""
def intrinsics_meta(calibration, source: str) -> dict:
    """pi.intrinsics; source: calibration | realsense_factory | placeholder."""
def calibration_from_meta(meta: dict) -> CameraCalibration:
    """Inverse of intrinsics_meta (offline evaluation)."""
def measure_for_save(image, calibration, sticker_offset_mm=(0.0, 0.0)) -> tuple[dict, Any]:
    """(messung_dict, kontrollbild or None). Without calibration: {"ok": False, "grund": "Keine Kamerakalibrierung verfuegbar"}."""

# Module: src/use_case_eval/synthetic.py (tests and development only)
def make_calibration(fx: float = 466.0, size=(640, 480), distortion=None) -> CameraCalibration: ...
def look_down_pose(x_mm=0.0, y_mm=0.0, height_mm=200.0, yaw_deg=0.0, tilt_deg=0.0) -> np.ndarray:
    """4x4 T_sheet_cam, looking straight down."""
def render_trial(calibration, camera_pose_in_sheet, cube_dx_mm, cube_dy_mm, cube_dtheta_deg,
                 sticker_offset_mm=(0.0, 0.0), with_cube=True, with_sheet_markers=None,
                 supersample=1) -> np.ndarray:
    """Grayscale image (uint8) of sheet and cube. Ground truth = the values passed in."""
```

### Data: placement and JSON

- `data/use-case/reihe_NNN/versuch_NNN.png` and `.json` (schema `adp.usecase.trial/1`).
  The measurement is in the field `messung` (see `TrialMeasurement`).
- `data/use-case/reihe_NNN/versuch_NNN_messung.png`: control image.
- `data/use-case/aufkleber_versatz.json`: `{"x_mm": ..., "y_mm": ...}`, applies to
  all series in the folder `data/use-case`.
- `pi.intrinsics` in the trial JSON: camera matrix and distortion incl. source. The
  offline evaluation uses these intrinsics, otherwise `--intrinsik <Kalibrierdatei>`
  (calibration file).

### Offline evaluation

```bash
python3 tools/use_case_auswertung.py data/use-case/reihe_NNN [--neu-messen] [--bilder] \
    [--aufkleber-kalibrieren VOR NACH] [--intrinsik KALIBRIERDATEI]
```

- Without options: takes the stored measurement from the JSON. If it is missing
  there (older trials), it is measured and the JSON is supplemented with the field `messung`.
- `--neu-messen`: re-measure all images with the current sticker offset, update JSON.
- `--bilder`: write control images `versuch_NNN_messung.png` (re-measures).
- `--aufkleber-kalibrieren VOR NACH`: determine the offset from two trials (image
  file or trial number) and write it to `data/use-case/aufkleber_versatz.json`.
- `--intrinsik`: calibration file, if the JSON contains no intrinsics.

**Output in the series folder:**

- `auswertung.csv`, columns: `versuch`, `ok`, `grund`, `dx_mm`, `dy_mm`,
  `dtheta_deg`, `r_mm`, `zone`, `reprojection_rms_px`, `blattmarker`
  (IDs separated by spaces), `camera_height_mm`, `camera_tilt_deg`, `nadir_offset_mm`.
- `auswertung.json`:
  ```json
  {
    "reihe": "reihe_003",
    "aufkleber_versatz_mm": [0.0, 0.0],
    "kennzahlen": { "n": 10, "n_gesamt": 10, "AP_mm": 0.0, "RP_mm": 0.0, "...": "..." },
    "versuche": [ { "versuch": 1, "ok": true, "dx_mm": 0.0, "...": "alle Felder von TrialMeasurement" } ]
  }
  ```
  `kennzahlen` contains the fields from `summarize`. In `versuche` only `blattmarker` is missing.

### Network / operation

No new ports or services. The measurement runs in the server
(`opcua-server.service`) on the hand Pi in a thread (`asyncio.to_thread`).
OpenCV is only imported at call time.

### Failure cases

| Case | Behaviour |
| --- | --- |
| No cube marker (ID 10) | `ok=false`, "Kein Wuerfelmarker (ID 10) erkannt" (no cube marker (ID 10) detected), photo is saved |
| Fewer than 2 sheet markers | `ok=false`, count given |
| Camera not above the sheet (pose implausible) | `ok=false` |
| Image size does not match calibration | `ok=false` |
| No camera calibration | `ok=false`, "Keine Kamerakalibrierung verfuegbar" (no camera calibration available) |
| Edge refinement fails | ArUco corners are used, no error message (`cube_edge_refined=false`) |
| Sticker calibration: no 180° rotation | `ValueError`, command aborts with a message |
| Sticker file missing or defective | offset (0, 0) |
| Control image fails | measurement is still saved, only `_messung.png` is missing |

## Procedure

1. Generate the target (`make_use_case_target.py --pdf`), check the PDF visibly with
   the Read tool (circles, markers, 50 mm scale bar). Then the synthetic measurement
   on the rendered sheet.
2. Frontend: photo pose per the "Camera pose" section above, call of
   `SaveUseCaseImage` with `ResultJson` (webskillcomposition).
3. Tests: `tests.test_use_case_eval`, `tests.test_use_case_image` green, then the
   entire suite.
4. Commissioning and trial run at the cell (see below).
5. After the trial: enter results in section "Accuracy", set the status to `done`.

## Commissioning and trial procedure

The steps are taken from the verification:

1. Print the sheet at 100 %, re-measure the 50 mm scale bar.
2. Fix the sheet at the nominal placement point (centre on the point, 0° axis
   parallel to the table x edge).
3. Glue the sticker onto the cube, offset at most about 1 mm.
4. Calibrate the sticker offset once: two placements with 180° rotation, then
   `tools/use_case_auswertung.py data/use-case/reihe_001 --aufkleber-kalibrieren N1 N2`.
5. Run series 1 with about 10 placements.
6. Check:
   - The app shows Δx, Δy, Δθ and the zone live.
   - `tools/use_case_auswertung.py data/use-case/reihe_001` delivers `auswertung.csv`
     and `auswertung.json`.
7. Plausibility: place the cube by hand offset by about 2 mm. The measurement should
   show about 2 mm.

Restart the server after the update (`sudo systemctl restart opcua-server.service`),
then check the method on the vision system (see `use-case-photo.md`, commissioning).

## Open questions

- **Distortion of the hand camera:** the synthetic values apply to a pinhole camera
  without distortion. The real calibration (pinhole or fisheye) will be checked in
  the trial.
- **Print quality:** the threshold for the edge refinement (contrast 60 grey levels)
  is set on synthetic images and must be checked on the real paper.

## Deviations from the plan

To be filled in during implementation.

## After completion

- Status set to `done`, actual interfaces entered.
- Row in the index `doc/project_docs/work-plans/README.md` updated.
- Lasting domain knowledge transferred into the matching topic doc (section 18a in
  `vision-server-interface.md` is already adjusted).

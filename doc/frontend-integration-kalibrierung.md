# Frontend integration: calibration + live stream

Quick-start briefing for an agent/developer who is implementing the
frontend side of "Calibrate in the Settings menu" (move the board, see the
live image, trigger captures, have them evaluated, get the result).
It does not replace any of the existing docs; it distills exactly what is
needed for **this** task and links to details.

**Canonical reference for everything this document does not cover:**
[`vision-server-interface.md`](vision-server-interface.md) — connection data
(section 2), what a backend must generally be able to do (section 3),
error codes (section 5), live stream (section 10), the complete calibration
interface in detail (section 12). This document is an excerpt plus a
procedure roadmap, not a replacement.

## 1. Architecture in one sentence

The vision server (one process per Pi/camera) speaks **OPC UA**, not
HTTP/WebSocket directly to the browser. If your frontend cannot do OPC UA
itself: the existing backend already subscribes to nodes generically
("What the backend must be able to do", section 3 in
`vision-server-interface.md`) — the node/method names below are what you
address through this backend abstraction, not new protocol logic.

## 2. Connection

```
Namespace URI: http://launch-rm.de/vision
```
**Never hard-code the namespace index** — always resolve it at runtime via
`get_namespace_index("http://launch-rm.de/vision")` (it shifts as soon as
something is added on the server — currently ns=7, it was ns=3/4 before).
All nodes below live under this index, with the stable string prefix
`VisionMachine.` — e.g. `ns=<vision>;s=VisionMachine.StartCalibration`.

## 3. Calibration workflow — exactly what the button in the Settings menu needs

**Since 2026-09-28, capture and evaluation are decoupled** (found live on the
ceiling camera: the corner detection on the full 12 MP frame could take so
long on the Pi hardware — up to a real hang — that it no longer fit into the
OPC UA response path of a single capture). `CaptureCalibrationSample` now
only stores the image, without checking whether the board is visible. The
actual evaluation (corner detection per image + camera calibration) runs only
after `FinishCalibration`, **in the background** — `FinishCalibration` is
therefore no longer an optional step but **always required** to get a result
at all, and its `Summary`/`Error` return value is **not** the result, only
the confirmation "evaluation started". The result comes exclusively via
`CalibrationProgress`.

```
Frontend                                          Vision server
   |  1. StartCalibration() -----------------------> Error: Int32
   |     (BUSY=3 if a job is running OR a session/  (0 = OK, session running)
   |      evaluation is already open)
   |
   |  2. Show LatestCameraFrame (e.g. CameraStreamMode="off";
   |     the mode "calibration" with board corners was removed
   |     on 17.11.2026 -- the stream does not say which
   |     captures were actually accepted)
   |
   |  3. CaptureCalibrationSample() ---------------> Error: Int32
   |     on click of "Aufnahme" or similar           (0 = accepted,
   |     (no auto-capture -- the user decides        1 = no session/
   |      when to trigger; NO check whether the             no camera image)
   |      board is visible -- that only turns out
   |      in step 5)
   |     ... repeat, moving/tilting the board ...
   |
   |  4. CalibrationProgress.samples shows the count live
   |
   |  5. FinishCalibration() ----------------------> Summary: String(JSON)
   |     Ends the capture phase, ONLY triggers the   Error: Int32
   |     evaluation (returns immediately --
   |     Summary is NOT the result yet!).
   |     (AbortCalibration() instead, if nothing
   |      should be evaluated/saved at all.)
   |
   |  6. Keep observing CalibrationProgress (subscribe or poll)
   |     -> "processing": true while the evaluation runs in the background
   |        (the live stream pauses briefly on the server side)
   |     -> as soon as "result" appears: DONE (success or failure;
   |        result.error tells them apart)
```

### The nodes/methods in detail

| Name | NodeId | Type | Meaning |
| --- | --- | --- | --- |
| `StartCalibration` | `ns=<vision>;s=VisionMachine.StartCalibration` | Method, input `OptionsJson: String` (since 17.11.2026), `Error: Int32` | Resets captures, starts a session with the board from `OptionsJson` (empty = board of the configuration) |
| `CaptureCalibrationSample` | `ns=<vision>;s=VisionMachine.CaptureCalibrationSample` | Method, no input, `Error: Int32` | Stores the current image (no detection) |
| `FinishCalibration` | `ns=<vision>;s=VisionMachine.FinishCalibration` | Method, no input, `Summary: String, Error: Int32` | **Mandatory step**: ends the capture, starts the evaluation in the background — `Summary` is not the result, see above |
| `AbortCalibration` | `ns=<vision>;s=VisionMachine.AbortCalibration` | Method, no input, `Error: Int32` | Discards without evaluating/saving |
| `CalibrationProgress` | `ns=<vision>;s=VisionMachine.CalibrationProgress` | String (JSON), **read only** | Live progress of *one session*, the only source for the result, see below |
| `ActiveCalibrationInfo` | `ns=<vision>;s=VisionMachine.ActiveCalibrationInfo` | String (JSON), **read only** | Metadata of the **currently active** calibration — stays put, independent of a running session, see below |
| `LatestCameraFrame` | `ns=<vision>;s=VisionMachine.LatestCameraFrame` | String (Base64 JPEG), read only | Image for the `<img>`/canvas — during the evaluation it only returns the last image from before the pause |
| `CameraStreamMode` | `ns=<vision>;s=VisionMachine.CameraStreamMode` | String, **writable** | `"off"` \| `"apriltag"` \| `"undistorted"` \| `"tagreference"`. Since 17.11.2026 there is no `"calibration"` mode any more (an unknown value falls back to `"apriltag"`); nothing has to be set for a calibration |

**Board parameters have been passed with `StartCalibration` since 17.11.2026**
(input `OptionsJson`, schema `wsc.vision.calibration.start/1`, field `board`
as chessboard or ChArUco, rules as for the hand-eye calibration; details in
[`vision-server-interface.md`](vision-server-interface.md) section 12.1).
Empty or `{}` means: board of the Pi configuration. A rejected board comes
back with `Error=2` (`INVALID_ARGUMENT`) and the reason in
`CalibrationProgress.startError`. The board of the session is shown in
`CalibrationProgress.board`. Update the Pi and the frontend only together
(the argument count is strictly checked). Otherwise the frontend starts/
triggers/reads it as before.

### `CalibrationProgress` — the JSON you poll/subscribe to

```jsonc
// while the capture is running:
{"running": true, "processing": false, "samples": 12, "minSamples": 15}

// FinishCalibration was called, evaluation runs in the background:
{"running": false, "processing": true, "samples": 18, "minSamples": 15}

// done, success:
{
  "running": false, "processing": false, "samples": 18, "minSamples": 15,
  "result": {
    "error": 0, "rms": 0.31, "samples": 16,
    "coverageX": 0.84, "coverageY": 0.9,
    "path": "data/calibration/cam_flange.json"
    // "warning": "..." -- see below, only if RMS is too high
  }
}

// done, failure (e.g. too few of the captures showed the board):
{
  "running": false, "processing": false, "samples": 18, "minSamples": 15,
  "result": {"error": 5, "message": "Zu wenige verwertbare Aufnahmen: 2"}
}
```

Two numbers can diverge: `samples` in the root object is the number of
**captured** images, `result.samples` the number of images on which the board
was actually **found** during the evaluation (can be smaller — blurry
captures, board not in the frame).

**UI logic:** `processing` shows "wird ausgewertet, bitte warten" (evaluation
in progress, please wait; German UI text) (can take a few seconds up to over a minute,
depending on the number of captures and the Pi hardware). When `result`
appears → show the result view: check `result.error` (0 = success, otherwise
`result.message`); on success show RMS/samples and, if applicable, the
warning.

### `ActiveCalibrationInfo` — for a persistent "Zuletzt kalibriert am ..." (last calibrated on ...) display

`CalibrationProgress` belongs to *one* session and is empty again with every
new `StartCalibration`. For a fixed display in the Settings menu (also when
nobody is calibrating right now) read this node — it is updated at server
start and after every successful calibration, and otherwise stays put:

```jsonc
{
  "placeholder": false,
  "frameId": "cam_flange",
  "calibrationId": "cam_flange@2026-09-22T10:00:00+00:00",
  "createdAt": "2026-09-22T10:00:00+00:00",
  "rms": 0.2945,
  "samples": 21,
  "board": {"type": "chessboard", "cols": 7, "rows": 9, "squareSizeM": 0.022},
  "path": "data/calibration/cam_flange.json"
}
```

`placeholder: true` means: never really calibrated yet, poses are not
dimensionally accurate — `rms`/`createdAt` are then `null`. A successful
calibration takes effect **immediately** here and in the real poses, without
any server restart.

## 4. Important pitfalls

1. **The `error` field in `result` is a numeric code, not a bool.** `0` =
   success. Error codes: section 5 in `vision-server-interface.md` — the
   relevant ones here are `1` (`INVALID_STATE`), `3` (`BUSY`), `5`
   (`DETECTION_FAILED`). The `Error` return value of `FinishCalibration`
   itself is a different, narrower case: it only says whether the evaluation
   was started (see pitfall 4) — the actual result, including its own `error`,
   is in `CalibrationProgress["result"]`.

2. **`result.warning` does not mean "error".** The file is still written.
   Coverage alone says nothing about the actual accuracy — a board that was
   never tilted can produce an RMS of >2 px despite 90 % coverage (experienced
   first-hand). The warning appears when RMS > 0.5 px and should be visible in
   the UI (not just console.log), ideally with a hint "tilt the board more and
   try again" (German UI text: "Board stärker kippen und neu versuchen"). **Unlike before, there
   is no longer a live display for this during the capture** — the coverage is
   only known after `FinishCalibration`, so the frontend can no longer warn the
   operator in advance, only afterwards.

3. **`BUSY` (Error=3) on `StartCalibration` does not necessarily mean "a job
   is running".** It can also be a **stale session** of a client that, e.g.,
   closed the tab without calling `FinishCalibration`/`AbortCalibration` — the
   session lives in the server process, not in the client. But it can also
   mean that an **evaluation from a previous `FinishCalibration` is still
   running** (`CalibrationProgress.processing === true`) — `AbortCalibration`
   does not help against that (it then rejects itself with `INVALID_STATE`);
   you just have to wait until `processing` becomes `false` again. Sensible
   frontend behavior: first check `CalibrationProgress.processing`; if it is
   `false`, on `BUSY` call `AbortCalibration` once and retry
   `StartCalibration` (see `src/vision_server/tools/calibration_client.py`,
   function `_start_session`, for the reference implementation of this
   pattern).

4. **`FinishCalibration`'s own `Error` return value is NOT the calibration
   result.** `0` only means "evaluation was started", `5` means "fewer than 3
   captures made, nothing started at all". A frontend that already shows
   "success" here shows the operator something wrong — the actual result
   (including a possible failure *during* the evaluation, see pitfall 5)
   only comes via `CalibrationProgress["result"]`.

5. **`CalibrationProgress["result"]` with `error=5` can occur despite enough
   captures — in two different ways.** Either too few of the captured images
   showed a board at all (`result.samples` < 3 — compare with the `samples`
   field in the root object, which shows the number of *captured*, not
   *detected* images), or the computation itself (`cv2.calibrateCamera`) fails
   numerically, if the board is spread over the whole image but was never
   tilted/varied in distance — the pure count says nothing about this.
   `result.message` gives the reason; simply call `StartCalibration` again and
   this time tilt the board clearly (±30°) and vary the distance.

6. **Layer 1 (ceiling camera) has taken the board via `OptionsJson` since
   17.11.2026 (frontend setting as for Layer 2), but (as of now) is still not
   finally surveyed/permanently mounted** (see section 12.8 in
   `vision-server-interface.md`). For a first integration test, Layer 2 (hand
   Pi, `cam_flange`) is the more reliable candidate.

7. **Mutual exclusion:** While a calibration session *or* a background
   evaluation is running, the normal detection job
   (`StartSingleJob`/`StartContinuous`) rejects with `BUSY`, and vice versa
   (`StartCalibration` rejects as long as a job is running). Both share the
   same camera. If your frontend also shows the normal job button: disable it
   while `CalibrationProgress.running === true` **or**
   `CalibrationProgress.processing === true` (or simply display the error).

## 5. Reference implementations (read, do not translate — the protocol matters, not the language)

All under `src/vision_server/tools/`, they run directly against a real
server, Python + `asyncua`, but the **call sequence** is 1:1 what the
frontend has to reproduce:

- `calibration_client.py` — start, poll progress, call `FinishCalibration`
  and then keep waiting for `progress["result"]` (evaluation runs in the
  background), retry on `BUSY`.
- `stream_viewer.py` — show the image, trigger `CaptureCalibrationSample`.
- `diagnose_board.py` — not part of the normal flow, only for troubleshooting.

## 6. State / what does not exist yet

- No dedicated state-machine state for "calibration running" — the automaton
  stays in `Ready`, the lock works purely via `BUSY` (section 12.7).
- No event for "calibration finished" — only the progress node. If the
  frontend wants to work event-driven instead of polling: subscribe to
  `CalibrationProgress` as usual via the existing `subscribeNode`
  infrastructure (like `LatestCameraFrame`), no separate event system is
  needed for this.

## 7. Lens model (since 29.09.2026)

The ceiling camera (Layer 1) is now calibrated with the fisheye model, the
hand camera (Layer 2) still with the standard model. The frontend sends
**nothing** for this — the model is fixed server-side per Pi. New are only
additional, purely informational fields, each `"fisheye"` or `"pinhole"`:

- `CalibrationProgress.model`: model of the running session — already known
  during the capture, i.e. before the result
- `CalibrationProgress.result.model`: in every result, also on failure
- `ActiveCalibrationInfo.model`: model of the currently active calibration —
  the source for the idle view, because `CalibrationProgress` without a
  session carries no `model`

Display suggestion: "Linsenmodell: Fisheye (Kannala-Brandt)" resp.
"Standard (Brown-Conrady)" (German UI texts: "lens model: fisheye" and
"standard") — in the capture view, next to the RMS in the result and in the
idle view. On failure e.g. "Fisheye-Kalibrierung fehlgeschlagen: <message>"
(German UI text: "fisheye calibration failed: <message>"). The RMS value stays in pixels and is comparable
between both models.
Details: `vision-server-interface.md` section 12.10.

# MJPEG live stream, camera watchdog and world-tag map on feature/vision-server

**Status:** done — 22.09.2026
**Owner:** John Glanz, `Agent: Takeover of branch John`
**Topic:** vision
**Branch:** feature/vision-server (taken over from `John`)

## Goal

The Pis run on feature/vision-server, but three things existed only on the
branch `John`: a smooth live stream directly from the Pi into the browser
(instead of Base64 over OPC UA), a watchdog against the hanging camera on Pi 1,
and the tag map with the world tag. Afterwards, a git pull on
feature/vision-server is enough on both Pis.

## Already read

- `CLAUDE.md`, `doc/arbeitsplaene/README.md` (no running plan on this topic)
- `doc/vision-server-interface.md` (sections 1, 10, 12)
- `doc/vision-system.md`
- `doc/altlasten.md`

## Affected files

- `src/vision_server/camera.py`, `camera_stream.py`, `mjpeg_server.py` (new),
  `profiles.py`, `address_space.py`, `runner.py`
- `tests/test_shared_camera.py` (new), `tests/test_mjpeg_server.py` (new),
  `tests/test_camera_stream.py`
- `config/tagmap.json` (new)
- `doc/vision-server-interface.md` (address space, sections 10, 10.2), `doc/vision-system.md`

## Interfaces

### OPC UA

| Element | NodeId / BrowsePath | Type | Direction | Meaning |
| --- | --- | --- | --- | --- |
| Port of the MJPEG stream | `ns=<vision>;s=VisionMachine.CameraStreamHttpPort` | Int32 | Read/subscribe | Port on the Pi; `0` as long as no HTTP server is listening (off, port in use, server still starting). If the node is missing, the Pi is older than this change |

Unchanged: `LatestCameraFrame` remains the fallback path (at most
`stream_fps`); `CameraStreamMode` still selects the overlay — the same for both
paths.

### Network / operations

| | Value |
| --- | --- |
| Stream | `GET http://<pi>:<port>/stream.mjpg` — `multipart/x-mixed-replace; boundary=frame`, plays in any `<img>` |
| Single image | `GET http://<pi>:<port>/snapshot.jpg` — newest JPEG, `503` as long as none is available |
| Port | `CameraStreamConfig.http_port`, default `8080`; `0` disables it |
| Rate | `http_fps` 15 with at least one viewer, otherwise `stream_fps` 5; capture `capture_fps` 15 |
| Dependencies | none new (only `asyncio.start_server`) |

The frontend builds the URL from the address under which it reaches the OPC UA
server, and the port from the node. Port 8080 must be reachable from the
browser machine.

### Configuration (`CameraStreamConfig`, watchdog)

| Field | Default | Meaning |
| --- | --- | --- |
| `frame_timeout_s` | 3.0 | a single capture may take this long; after that the camera counts as hanging |
| `max_capture_failures` | 3 | consecutive failures until reopening; a hang counts as a full failure immediately |
| `max_reopen_attempts` | 2 | reopenings without an image in between; after that the process exits hard and systemd restarts it |
| `stale_frame_s` | 2.0 | a frame older than this is no longer a live image: the node is cleared once, MJPEG viewers get nothing stale |
| `overlay_timeout_s` | 2.0 | if the overlay hangs longer, the raw image is sent out |

Applies equally to all camera backends (Picamera2, RealSense, OpenCV), because
the watchdog sits in the shared capture loop.

### Data

`config/tagmap.json`: only tag 0 as world tag (`role: world`, `sizeM` 0.1,
origin of the world coordinate system). Module tags are **deliberately not**
included — the Pi reports them as `TAG-<id>` with a world pose; the mapping of
tag → module including offset is done by the frontend (WSC
`features/cell-modules/config/moduleCatalog.ts`).

### Failure cases

- Port in use: server starts without the HTTP stream, node stays `0`, frontend
  falls back to `LatestCameraFrame`.
- Camera hangs: reopen, if necessary a process restart by systemd; during the
  hang no stale image on either path.
- Too many viewers: further connections get `503` (limit in `mjpeg_server.py`);
  unknown path `404`.

## Deviations from the plan

- The remote calibration of `John` (`VisionMachine.Calibration.*`) was **not**
  taken over — on this branch the own calibration applies (`StartCalibration` /
  `CaptureCalibrationSample` / `FinishCalibration` / `AbortCalibration`,
  `CalibrationProgress`, section 12 of the interface docs). Open: the calibration
  dialog in the WSC frontend (`untested`) still addresses the
  `John` interface and must be switched to this one.
- `SharedCamera.is_open` came along because the watchdog tests need it.
- `max_stream_width` (this branch) and the MJPEG encoding (`John`) were merged:
  scaling down is done once while encoding, and both paths get the same image.
  The calibration progress is written at the node cycle.

## After completion

- [x] Status `done`, interfaces as implemented
- [x] Index line
- [x] Topic knowledge in `doc/vision-server-interface.md` (section 10.2) and
  `doc/vision-system.md`

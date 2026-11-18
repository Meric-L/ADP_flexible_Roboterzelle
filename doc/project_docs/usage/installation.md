# Installation

Exact install and update commands for the vision Pis (server and Part 10 program) and for the webskillcomposition (WSC) backend and frontend.

## Overview

| Part | Runs on | Section |
| --- | --- | --- |
| Vision server (incl. Part 10 program `VisionProgram`) | both Pis (ceiling, hand) | [Vision Pi](#vision-pi) |
| Part 10 program layer | ships with the vision server | [Part 10 program](#part-10-program) |
| WSC backend and frontend | any PC in the cell LAN | [WSC](#webskillcomposition-backend-and-frontend) |

## Vision Pi

### Hardware and OS

| | Ceiling Pi | Hand Pi |
| --- | --- | --- |
| Hostname | `ADP-Roboter-Lokalisierung` | `ADP-HandInEye-Kamera-Pi` |
| Camera | Raspberry Pi HQ Camera (IMX477), Picamera2, 4056×3040 | Intel RealSense D415 |
| `visionSystemId` | `vision-ceiling-01` | `vision-flange-01` |
| Raspberry Pi model | Pi 4 (per [`calibration-lens-model-per-camera.md`](../work-plans/calibration-lens-model-per-camera.md)) | Pi 5 (same source) |

- The Pi model is taken from the work plans, not read from the device (`/proc/device-tree/model`).
- Per-Pi presets are selected by hostname in `src/vision_server/server.py`.
- Source: [`deploy/README.md`](../../../deploy/README.md), [`interfaces/vision-server-interface-short.md`](../interfaces/vision-server-interface-short.md).

### System packages

| Package | Why | Source |
| --- | --- | --- |
| `python3-picamera2` (`sudo apt install -y python3-picamera2`) | ceiling camera; not reliably installable with pip | comment in `requirements.txt` |
| `python3-opencv` | present on the Pis; overridden in the venv by `opencv-contrib-python` | `deploy/README.md` |
| `librealsense` / `pyrealsense2` | hand Pi; no aarch64 wheel on PyPI, installed by hand | comment in `requirements.txt` |

### Python environment

Both Pis: venv `<Repo>/.venv`, Python 3.13, `include-system-site-packages = true` (so the apt packages are visible), `opencv-contrib-python` 5.0 in the venv.

```bash
cd <Repo>
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

`requirements.txt` pins: `opencv-contrib-python>=4.8.0,<5`, `scipy>=1.11`, `numpy`, `pupil-apriltags` (optional detector), `asyncua>=1.1.5`, `zeroconf>=0.131` (mDNS; the server runs without it but does not announce itself), `tzdata`.

- Repo paths on the Pis: [`deploy/README.md`](../../../deploy/README.md), table "Values per Pi".
- Note: `deploy/README.md` documents `opencv-contrib-python` 5.0 in the Pi venvs, while `requirements.txt` says `<5`. Check the Pi's installed version before reinstalling.

### Configuration files

| File | Content |
| --- | --- |
| `config/tagmap.json` | tag map of the cell (world tags, module tags); versioned, example in `config/tagmap.example.json` |
| `data/calibration/<frameId>.json` | camera intrinsics (`cam_ceiling`, `cam_flange`) |
| `data/handeye/<frameId>.json` | hand-eye calibration (hand Pi) |
| `opcua-server.service.d/override.conf` | per-Pi `VISION_*` / `OPCUA_*` environment variables |

All environment variables: [`topics/vision-system.md`](../topics/vision-system.md#configuration).

### systemd unit `opcua-server.service`

The template is [`deploy/opcua-server.service`](../../../deploy/opcua-server.service) with placeholders `@USER@` and `@REPO@`. It runs `<Repo>/.venv/bin/python -m vision_server.server` with `PYTHONPATH=<Repo>/src`, `Restart=always`, `RestartSec=5`.

| Placeholder | Ceiling Pi | Hand Pi |
| --- | --- | --- |
| `@USER@` | `adp-roboter` | `adp-kamera-in-hand` |
| `@REPO@` | `/home/adp-roboter/Desktop/ADP_Lokalisierung/ADP_flexible_Roboterzelle` | `/home/adp-kamera-in-hand/Desktop/opcua-server` |

Install (ceiling Pi example; compare with `systemctl cat opcua-server.service` first):

```bash
cd <Repo>/deploy
sed -e 's|@USER@|adp-roboter|g' \
    -e 's|@REPO@|/home/adp-roboter/Desktop/ADP_Lokalisierung/ADP_flexible_Roboterzelle|g' \
    opcua-server.service > /tmp/opcua-server.service
sudo cp /tmp/opcua-server.service /etc/systemd/system/opcua-server.service
sudo systemctl daemon-reload
sudo systemctl enable --now opcua-server.service
```

- `deploy/README.md` uses `restart` instead of `enable --now` because the unit already exists on both Pis; `WantedBy=multi-user.target` makes `enable` start it at boot.
- Backup and rollback commands: [`deploy/README.md`](../../../deploy/README.md#rollback).

### mDNS announcement

Automatic at server start (needs `zeroconf` and a LAN IPv4), no extra service on the Pi.

| | Value |
| --- | --- |
| Service type | `_opcua-tcp._tcp.local.` |
| Instance name | `visionSystemId` |
| TXT | `path=/raspi/server/`, `caps=DA` |

`OPCUA_MDNS=0` switches it off. The server also registers at the LDS `opc.tcp://10.10.38.27:4840/` every 60 s (`OPCUA_LDS_URL`). Details: [`topics/vision-system.md`](../topics/vision-system.md#starting).

### Update

```bash
cd <Repo>
git pull
sudo systemctl restart opcua-server.service
```

New dependencies: run `.venv/bin/pip install -r requirements.txt` before the restart. Do not start the server in the foreground on the Pi (port 4840 collides with the service).

### Check that it runs

```bash
systemctl is-active opcua-server.service      # active
ss -ltn | grep -E ':(4840|8080)\b'            # OPC UA and MJPEG stream listening
journalctl -u opcua-server.service -n 50 --no-pager
```

- Endpoint: `opc.tcp://<pi>:4840/raspi/server/`.
- Optional job handshake: `PYTHONPATH=src python3 src/vision_server/tools/handshake_client.py --url opc.tcp://127.0.0.1:4840/raspi/server/ --recipe-id calibration`.

## Part 10 program

The Part 10 layer (`Objects/VisionProgram`) is **not installed separately**: `install_vision_machine` in `src/vision_server/runner/install.py` creates it together with the 40100 `VisionMachine`, in the same server process and session.

| Piece | Code | Extra install |
| --- | --- | --- |
| Generic program framework | `src/ua_program/` | none (part of the repo, `PYTHONPATH=src`) |
| `VisionProgram` | `src/vision_server/vision_program.py`, `runner/part10.py` | none |

Standalone (development and isolated tests, not the production path): the vision package alone on its own port.

```bash
PYTHONPATH=src python3 -m vision_server --port 4841
```

Endpoint then `opc.tcp://0.0.0.0:4841/vision/machine/`. It still opens the detection sources, so it needs the same camera or a working source configuration. Details: [`components/part10/README.md`](../components/part10/README.md), [`interfaces/part10-program-interface.md`](../interfaces/part10-program-interface.md).

## webskillcomposition backend and frontend

Repository: `webskillcomposition`, branch `ADP_Lokalisierung`.

### Prerequisites

| Tool | Version | Source |
| --- | --- | --- |
| Git, Git LFS | any | `docs/development.md` |
| uv | tested with 0.12 | `docs/development.md` |
| Python | `>=3.13,<3.14` | `backend/pyproject.toml`, `Dockerfile` (`uv python install 3.13`) |
| Node.js and npm | tested with Node 20.20 / npm 10.8; the Dockerfile builds with `node:25` | `docs/development.md`, `Dockerfile` |


### Clone and submodule

```bash
git clone git@git.rwth-aachen.de:ai-in-production/project-repositories/webskillcomposition.git --recurse-submodules
cd webskillcomposition
git lfs install && git lfs pull
# already cloned without submodules:
git submodule update --init
```

The submodule `frontend/public/urdf` holds the robot URDFs.

### Backend

```bash
cd backend
uv pip install -e .
uv run main.py          # http://127.0.0.1:8000, WebSocket ws://127.0.0.1:8000/ws
```

| Variable | Effect |
| --- | --- |
| `WSC_MDNS=0` | no mDNS browsing for OPC UA servers (default on) |
| `WSC_DATA_DIR=<dir>` | module catalog in `<dir>/module_catalog.json` instead of `backend/catalog/` |
| `HOST` (set) | serve the built frontend from `./www` (Docker) |

Tests: `cd backend && uv run pytest`.

### Frontend

```bash
cd frontend
npm install
npm run dev             # Vite dev server on http://localhost:1234
```

Other scripts (`frontend/package.json`): `npm run build`, `npm test`, `npm run typecheck`.

### Docker (backend and frontend in one image)

```bash
docker compose up --build    # http://localhost:8000
```

Source: [`components/webskill-backend/README.md`](../components/webskill-backend/README.md), [`components/webskill-frontend/README.md`](../components/webskill-frontend/README.md).

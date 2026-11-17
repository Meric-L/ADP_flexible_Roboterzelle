# deploy/ — systemd unit of the Pis

`opcua-server.service` starts the vision server (`python -m vision_server.server`,
OPC UA on port 4840, MJPEG stream on port 8080) at boot and restarts it after a
crash or the camera watchdog. The file here is a **template** with the
placeholders `@USER@` (user and group) and `@REPO@` (repo path on the Pi). It
replaces the earlier starter `src/OPCUA/server.py` (legacy item B13).

The entries `Description`, `After`, `Wants` and `WantedBy` were not taken from
the Pis but set according to common practice. The remaining entries correspond
to the state as of 02.11.2026.

## Values per Pi (as of 02.11.2026)

| | Ceiling Pi `ADP-Roboter-Lokalisierung` | Hand Pi `ADP-HandInEye-Kamera-Pi` |
| --- | --- | --- |
| `@USER@` (User/Group) | `adp-roboter` | `adp-kamera-in-hand` |
| `@REPO@` | `/home/adp-roboter/Desktop/ADP_Lokalisierung/ADP_flexible_Roboterzelle` | `/home/adp-kamera-in-hand/Desktop/opcua-server` |
| Drop-in `opcua-server.service.d/override.conf` | yes (see below) | none |

Ceiling Pi, drop-in `override.conf`:

```ini
[Service]
Environment=VISION_DISABLE_STREAM=0
Environment=VISION_APRILTAG_QUAD_DECIMATE=1.5
Environment=VISION_SUBPIXEL_CORNER_REFINEMENT=0
```

The hand Pi has no `VISION_*` variables. Both Pis: venv `<Repo>/.venv` with
Python 3.13, `include-system-site-packages = true`, `opencv-contrib-python` 5.0 in
the venv (the apt package version `python3-opencv` is overridden there).

## Installation

Before that, compare the running unit with the template:

```bash
systemctl cat opcua-server.service
```

Then on the Pi (example: ceiling Pi; insert the values from the table):

```bash
cd <Repo>/deploy
sed -e 's|@USER@|adp-roboter|g' \
    -e 's|@REPO@|/home/adp-roboter/Desktop/ADP_Lokalisierung/ADP_flexible_Roboterzelle|g' \
    opcua-server.service > /tmp/opcua-server.service

sudo cp /etc/systemd/system/opcua-server.service \
        /etc/systemd/system/opcua-server.service.bak-$(date +%Y%m%d)
sudo cp /tmp/opcua-server.service /etc/systemd/system/opcua-server.service
sudo systemctl daemon-reload
sudo systemctl restart opcua-server.service
```

The drop-in `opcua-server.service.d/override.conf` remains untouched.

Check:

```bash
systemctl is-active opcua-server.service      # active
ss -ltn | grep -E ':(4840|8080)\b'            # both ports listening
journalctl -u opcua-server.service -n 50 --no-pager
```

## Rollback

Copy back the backup `opcua-server.service.bak-<Datum>` and reload:

```bash
sudo cp /etc/systemd/system/opcua-server.service.bak-<Datum> \
        /etc/systemd/system/opcua-server.service
sudo systemctl daemon-reload
sudo systemctl restart opcua-server.service
```

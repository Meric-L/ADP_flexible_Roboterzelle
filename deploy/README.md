# deploy/ — systemd-Unit der Pis

`opcua-server.service` startet den Vision-Server (`python -m vision_server.server`,
OPC UA auf Port 4840, MJPEG-Stream auf Port 8080) beim Hochfahren und startet ihn
nach einem Absturz oder dem Kamera-Watchdog neu. Die Datei hier ist eine **Vorlage**
mit den Platzhaltern `@USER@` (Benutzer und Gruppe) und `@REPO@` (Repo-Pfad auf dem
Pi). Sie ersetzt den früheren Starter `src/OPCUA/server.py` (Altlast B13).

Die Einträge `Description`, `After`, `Wants` und `WantedBy` wurden auf den Pis nicht
erhoben, sondern gängig angesetzt. Die übrigen Einträge entsprechen dem Stand vom
02.11.2026.

## Werte je Pi (Stand 02.11.2026)

| | Decken-Pi `ADP-Roboter-Lokalisierung` | Hand-Pi `ADP-HandInEye-Kamera-Pi` |
| --- | --- | --- |
| `@USER@` (User/Group) | `adp-roboter` | `adp-kamera-in-hand` |
| `@REPO@` | `/home/adp-roboter/Desktop/ADP_Lokalisierung/ADP_flexible_Roboterzelle` | `/home/adp-kamera-in-hand/Desktop/opcua-server` |
| Drop-in `opcua-server.service.d/override.conf` | ja (siehe unten) | keines |

Decken-Pi, Drop-in `override.conf`:

```ini
[Service]
Environment=VISION_DISABLE_STREAM=0
Environment=VISION_APRILTAG_QUAD_DECIMATE=1.5
Environment=VISION_SUBPIXEL_CORNER_REFINEMENT=0
```

Der Hand-Pi hat keine `VISION_*`-Variablen. Beide Pis: venv `<Repo>/.venv` mit
Python 3.13, `include-system-site-packages = true`, `opencv-contrib-python` 5.0 im
venv (die apt-Paket-Version `python3-opencv` wird dort überdeckt).

## Einspielen

Vorher die laufende Unit mit der Vorlage vergleichen:

```bash
systemctl cat opcua-server.service
```

Dann auf dem Pi (Beispiel Decken-Pi; Werte der Tabelle einsetzen):

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

Das Drop-in `opcua-server.service.d/override.conf` bleibt unberührt.

Prüfen:

```bash
systemctl is-active opcua-server.service      # active
ss -ltn | grep -E ':(4840|8080)\b'            # beide Ports lauschen
journalctl -u opcua-server.service -n 50 --no-pager
```

## Rückweg

Die Sicherung `opcua-server.service.bak-<Datum>` zurückkopieren und neu laden:

```bash
sudo cp /etc/systemd/system/opcua-server.service.bak-<Datum> \
        /etc/systemd/system/opcua-server.service
sudo systemctl daemon-reload
sudo systemctl restart opcua-server.service
```

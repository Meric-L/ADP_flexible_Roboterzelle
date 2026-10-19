# Rohbild speichern: OPC-UA-Methode SaveRawImage

**Status:** fertig — 19.10.2026
**Verantwortlich:** Meric, `Agent: Rohbild speichern`
**Thema:** vision-server / frontend
**Branch:** `apriltag` (Frontend: `Ungetestet` im Repo webskillcomposition)

## Ziel

Ein Knopf im Frontend speichert das aktuelle Kamera-Vollbild verlustfrei als PNG
unter `data/rohbilder/` auf dem Pi. Das Bild dient als Rohmaterial, z. B. zum
Nachrechnen der Erkennung oder für die Dokumentation. Vorher gab es nur den
verlustbehafteten Livestream.

## Bereits gelesen

- `CLAUDE.md`
- `doc/projektdoku/arbeitsplaene/README.md`, `_vorlage.md`
- `doc/projektdoku/vision-server-interface.md`

## Betroffene Dateien

- `src/vision_server/raw_image.py` (neu)
- `src/vision_server/profiles.py` bzw. `config.py` (`raw_image_dir`)
- `src/vision_server/runner.py` (`_install_save_raw_image`)
- `tests/test_raw_image.py` (neu)
- `.gitignore`, `data/rohbilder/.gitkeep`
- `doc/projektdoku/vision-server-interface.md` (neuer Abschnitt)
- Frontend (Repo webskillcomposition): Button „📷 Rohbild speichern“ in
  `frontend/src/features/opcua-server/components/SaveRawImageButton.tsx`, in
  der Leiste neben „Ansicht“ (`CameraView.tsx`), Hook
  `model/useSaveRawImage.ts`, Binding `rawImage.saveNodeId` in `config/piServers.ts`.

## Schnittstellen

### OPC UA

| Element | NodeId / BrowsePath | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| `SaveRawImage` | `ns=<own_idx>;s=<vision_system_name>.SaveRawImage` | Methode | Aufruf | speichert das aktuelle Kamera-Vollbild als PNG |

Zusätzlich unter `VisionProgram` gespiegelt (wie die Kalibriermethoden).

Signatur: `SaveRawImage() -> (FileName: String, Error: Int32)`

- `FileName`: Pfad relativ zur Repo-Wurzel mit `/`, z. B.
  `data/rohbilder/cam_ceiling_20261019-143012.png`. Liegt das Zielverzeichnis
  nicht unter der Repo-Wurzel, der absolute Pfad. Bei Fehler `""`.
- `Error` (`VisionErrorCode`): `0` OK gespeichert, `1` INVALID_STATE (noch kein
  Kamera-Frame), `3` BUSY (ein Job läuft), `6` INTERNAL (Schreiben fehlgeschlagen).

### Python

```python
# Modul: src/vision_server/raw_image.py
def save_raw_image(image, directory: Path, frame_id: str,
                   now: datetime | None = None) -> Path:
    """Legt directory an, schreibt <frame_id>_<YYYYmmdd-HHMMSS>.png (bei
    Kollision _2, _3, ...), nie überschreibend. Wirft OSError, wenn
    cv2.imwrite fehlschlägt."""
```

Konfiguration: `VisionServerConfig.raw_image_dir: Path | None`, Default
`<Repo>/data/rohbilder`. `None` = Methode wird nicht angelegt.

### Netz / Betrieb

Keine neuen Ports oder Services. Die Methode gibt es nur, wenn eine geöffnete
Erkennungsquelle eine Kamera hat. `frame_id` = `config.apriltag.frame_id`, ohne
AprilTag-Konfiguration `config.vision_system_name`.

### Fehlerfälle

- Kein Frame: `INVALID_STATE`, nichts geschrieben.
- Job läuft: `BUSY`. Das Encoding eines 12-MP-PNG soll den Job-Timeout nicht gefährden.
- Platte voll, kein Schreibrecht: `INTERNAL`, Ausnahme im Log.

## Vorgehen

1. Plan und Doku.
2. `raw_image.py`, Config, Handler in `runner.py`.
3. `.gitignore` anpassen (`data/*`, `!data/rohbilder/`).
4. Tests, volle Suite.

## Offene Fragen

- Aufräumen alter Rohbilder (Speicherplatz auf der SD-Karte) ist nicht Teil
  dieser Aufgabe.

## Abweichungen vom Plan

Keine Abweichung von der Schnittstelle. Tatsächlicher Stand: Das Feld
`raw_image_dir` liegt in `VisionServerConfig` (`config.py`), der Default nutzt
`_REPO_ROOT` aus `profiles.py`. Die Methode entsteht in `install_vision_machine`
vor dem Aufbau des VisionProgram, wenn `_camera_owner(...)` eine Quelle liefert
und `raw_image_dir` nicht `None` ist.

Nachtrag 19.10.2026: Der Button saß zuerst als 📷 oben im Livebild
(`CameraPreview.tsx`). Auf Wunsch sitzt er jetzt gut sichtbar unten in der
Leiste neben „Ansicht“. Ruft das Frontend `SaveRawImage` an einem Pi mit älterer
Server-Version auf, meldet das Relay (`backend/opcua/method_calls.py`,
`call_raw_method`) jetzt „method not found on the server“ statt eines
`NoneType`-Fehlers, und das Frontend zeigt dazu den Hinweis, die Pi-Software zu
aktualisieren.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index aktualisiert.
- Abschnitt in `vision-server-interface.md` ergänzt.

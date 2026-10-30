"""Betriebsmittel der AprilTag-Quelle: Kalibrierung, Tag-Map, Hand-Auge, Detektor, Kamera.

`ResourcesMixin` laedt beim Start (`open`) alles, was die Quelle zum Messen
braucht, und tauscht es im Betrieb aus (`apply_*`). Er erwartet am Objekt
`_config`, `_camera_config`, `camera`, `_detector`, `_calibration`, `_tag_map`,
`_hand_eye`, `_anchor`, `_drift`, `_localization` und `configuration_id`.
"""

import functools
import json
import logging
from typing import Any

_log = logging.getLogger(__package__)


class ResourcesMixin:
    """Laden, Ersetzen und Freigeben der Betriebsmittel."""

    def _build_configuration_id(self) -> str:
        """Fuegt Tag-Familie, Kalibrierung und Karte zu einer Zeichenkette zusammen.

        Spaeter laesst sich damit klaeren, welche Kalibrierung und welche Karte
        hinter einer schlechten Pose aktiv waren (doc/projektdoku/altlasten.md C4).
        Braucht bewusst kein OpenCV und liest die Dateien nicht.
        """
        from tagloc.identity import calibration_identity, tag_map_identity

        return (
            f"{self._config.tag_family}@{calibration_identity(self._config.calibration_path)}"
            f"+{tag_map_identity(self._config.tag_map_path)}"
        )

    async def open(self) -> None:
        """Laedt Kalibrierung und Karte, baut den Detektor und oeffnet die Kamera.

        Fehlt die Kalibrierung, oeffnet die Quelle nicht, und der Server bleibt
        in Preoperational -- Absicht, denn ein Vision-Server ohne Kalibrierung
        wuerde sinnlose Zahlen melden. Zwei Ausnahmen, in dieser Reihenfolge:
        eine Kamera, die ihre eigene Werks-Intrinsik kennt (RealSense am
        Hand-Pi), nimmt diese -- gemessen, nur nicht von uns --, und
        `allow_placeholder_calibration`, eine bewusste, vorlaeufige Umgehung, um
        die Erkennung vor dem echten Kalibrierlauf zu betreiben.
        """
        from tagloc.calibration import default_calibration

        calibration_missing = await self._load_calibration()
        await self._load_tag_map()
        await self._load_hand_eye()
        await self._build_detector()
        self.configuration_id = self._build_configuration_id()
        await self.camera.open()
        if calibration_missing is not None:
            await self._fallback_calibration(calibration_missing, default_calibration)

    async def _load_calibration(self) -> FileNotFoundError | None:
        """Kalibrierdatei laden, falls noch keine gesetzt ist.

        Fehlt die Datei, gibt es drei Wege: die RealSense entscheidet erst mit
        offener Kamera (dann kommt der Fehler zurueck und `open` ruft
        `_fallback_calibration`); sonst Platzhalter, wenn erlaubt; sonst bricht
        die Ausnahme ab, ohne dass die Kamera geoeffnet wurde.
        """
        from tagloc.calibration import default_calibration, load_calibration

        if self._calibration is not None:
            return None
        try:
            self._calibration = await self.run_blocking(
                load_calibration, self._config.calibration_path
            )
        except FileNotFoundError as error:
            if getattr(self._camera_config, "backend", "") == "realsense":
                # Entschieden wird erst mit offener Kamera: die RealSense
                # kennt ihre Werks-Intrinsik, aber nur mit laufender Pipeline.
                return error
            if not self._config.allow_placeholder_calibration:
                # Ohne Werks-Intrinsik: wie bisher abbrechen, ohne die
                # Kamera ueberhaupt zu oeffnen.
                raise
            _log.warning(
                "Keine Kalibrierung unter %s -- verwende Platzhalter-Intrinsik "
                "(allow_placeholder_calibration=True). Posen sind NICHT masshaltig.",
                self._config.calibration_path,
            )
            self._calibration = default_calibration(
                self._config.resolution, frame_id=self._config.frame_id
            )
        return None

    async def _load_tag_map(self) -> None:
        """Tag-Map laden, falls noch keine gesetzt ist; ohne brauchbare Datei eine leere."""
        from tagloc.tagmap import empty_tag_map, load_tag_map, validate_tag_map

        if self._tag_map is not None:
            return
        path = self._config.tag_map_path
        if path is not None and path.is_file():
            try:
                self._tag_map = await self.run_blocking(load_tag_map, path)
            except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
                # Eine unbrauchbare Karte darf die Erkennung nicht
                # stilllegen. Ohne Karte laeuft alles weiter -- die Tags
                # werden als TAG-<id> im Kamera-KS gemeldet, und das
                # Frontend bekommt seine Platzhalter. Umgekehrt waere der
                # ganze Vision-Server wegen einer Textdatei tot.
                _log.error(
                    "Tag-Map %s ist unbrauchbar (%s) -- weiter OHNE Karte. "
                    "Tags werden als 'TAG-<id>' im Kamera-KS gemeldet, ohne "
                    "Modulnamen und ohne CAD-Versatz. Grund: %s",
                    path,
                    type(error).__name__,
                    error,
                )
                self._tag_map = empty_tag_map()
            else:
                # Gemeldet, nicht erzwungen: eine Zelle im Aufbau muss messen
                # duerfen. Schweigen hiesse, dass ein fehlender Welttag nur als
                # stillschweigend schlechtere Genauigkeit auffaellt.
                for problem in validate_tag_map(self._tag_map):
                    _log.warning("Tag-Map %s: %s", path, problem)
        else:
            _log.warning(
                "Keine Tag-Map unter %s -- Posen bleiben im Kamera-KS und "
                "Tags werden ohne Modulzuordnung gemeldet",
                path,
            )
            self._tag_map = empty_tag_map()

    async def _load_hand_eye(self) -> None:
        """Hand-Auge-Datei laden, falls konfiguriert und noch nicht gesetzt."""
        if self._hand_eye is not None or self._config.hand_eye_path is None:
            return
        from tagloc.handeye import load_hand_eye

        try:
            self._hand_eye = await self.run_blocking(load_hand_eye, self._config.hand_eye_path)
            _log.info(
                "Hand-Auge geladen: %s (Streuung %.2f mm / %.3f deg ueber %d Posen)",
                self._config.hand_eye_path,
                (self._hand_eye.rms_position_m or 0.0) * 1000.0,
                self._hand_eye.rms_rotation_deg or 0.0,
                self._hand_eye.sample_count,
            )
        except FileNotFoundError:
            # Kein Fehler: die Deckenkamera hat keine, und der Hand-Pi darf
            # auch ohne messen -- dann eben nur mit Welttag im Bild.
            _log.warning(
                "Keine Hand-Auge-Kalibrierung unter %s -- ohne Welttag im Bild "
                "bleiben die Posen im Kamera-KS",
                self._config.hand_eye_path,
            )

    async def _build_detector(self) -> None:
        """Detektor bauen, falls noch keiner gesetzt ist, und seine Parameter loggen."""
        from tagloc.detector import build_detector

        if self._detector is not None:
            return
        # `run_blocking` reicht nur Positionalargumente durch --
        # `subpixel_corner_refinement` ist bei `build_detector` bewusst
        # keyword-only, deshalb hier per `partial` gebunden.
        build = functools.partial(
            build_detector,
            self._config.tag_family,
            self._config.detector_backend,
            subpixel_corner_refinement=self._config.subpixel_corner_refinement,
            corner_refinement_method=self._config.corner_refinement_method,
            apriltag_quad_decimate=self._config.apriltag_quad_decimate,
            detector_parameters=self._config.detector_parameters,
        )
        self._detector = await self.run_blocking(build)
        changed = getattr(self._detector, "changed_parameters", None)
        if changed:
            _log.info(
                "Detektor-Parameter abweichend vom OpenCV-Standard: %s",
                ", ".join(f"{name}={old} -> {new}" for name, (old, new) in changed.items()),
            )
        else:
            _log.info("Detektor-Parameter: OpenCV-Standard")

    async def _fallback_calibration(self, missing: FileNotFoundError, default_calibration) -> None:
        """Ohne Kalibrierdatei: Werks-Intrinsik, sonst Platzhalter, sonst Abbruch."""
        factory = getattr(self.camera, "factory_calibration", None)
        calibration = await factory(self._config.frame_id) if factory is not None else None
        if calibration is not None:
            _log.warning(
                "Keine Kalibrierung unter %s -- verwende die Werks-Intrinsik der "
                "Kamera (%dx%d). Gemessen, aber nicht von uns: eine eigene "
                "Kalibrierung ist genauer.",
                self._config.calibration_path,
                *calibration.image_size,
            )
            self._calibration = calibration
            return
        if not self._config.allow_placeholder_calibration:
            await self.camera.close()
            raise missing
        _log.warning(
            "Keine Kalibrierung unter %s -- verwende Platzhalter-Intrinsik "
            "(allow_placeholder_calibration=True). Posen sind NICHT masshaltig.",
            self._config.calibration_path,
        )
        self._calibration = default_calibration(
            self._config.resolution, frame_id=self._config.frame_id
        )

    @property
    def calibration(self) -> Any:
        """Die gerade benutzte Kalibrierung (`CameraCalibration`) oder `None`."""
        return self._calibration

    @property
    def intrinsics_source(self) -> str:
        """Woher die Intrinsik stammt: `calibration`, `realsense_factory`, `placeholder`."""
        from tagloc.calibration import PLACEHOLDER_CALIBRATION_ID

        from ...camera import REALSENSE_FACTORY_CALIBRATION_ID

        calibration_id = getattr(self._calibration, "calibration_id", "")
        if calibration_id == PLACEHOLDER_CALIBRATION_ID:
            return "placeholder"
        if calibration_id == REALSENSE_FACTORY_CALIBRATION_ID:
            return "realsense_factory"
        return "calibration"

    def apply_tag_map(self, tag_map: Any) -> list[str]:
        """Uebernimmt eine neue Tag-Map in die laufende Quelle.

        Gibt die Beanstandungen von `validate_tag_map` zurueck -- sie sind
        Bericht, kein Fehler: eine Zelle im Aufbau darf messen.

        Der Anker wird dabei **verworfen**. Er wurde gegen die Weltposen der
        alten Karte gerechnet; gegenueber einer neuen waere er stumm falsch,
        und ein stiller Versatz in jeder Modulpose ist genau die Sorte Fehler,
        die erst beim Danebengreifen auffaellt.
        """
        from tagloc.tagmap import validate_tag_map

        self._tag_map = tag_map
        self._anchor = None
        self._drift = None
        self._localization = None
        # Die Karte gehoert zur Identitaet der Konfiguration -- sonst sieht ein
        # spaeteres Ergebnis aus wie eines von vorher.
        self.configuration_id = self._build_configuration_id()
        return validate_tag_map(tag_map)

    def apply_calibration(self, calibration: Any) -> None:
        """Ersetzt die aktive Kalibrierung ohne Neustart.

        Aufgerufen aus `runner.py`, direkt nachdem eine interaktive
        `CalibrationSession` (`StartCalibration`/`CaptureCalibrationSample`/
        `FinishCalibration`) eine neue Kalibrierung berechnet und gespeichert
        hat. `_locate()` liest `self._calibration` bei jedem Job frisch --
        der Tausch hier wirkt also ab dem naechsten Job, ohne dass jemand den
        Server neu starten muss.
        """
        self._calibration = calibration
        self.configuration_id = self._build_configuration_id()

    def apply_hand_eye(self, hand_eye: Any) -> None:
        """Uebernimmt eine neue Hand-Auge-Kalibrierung ohne Neustart.

        Der Anker wird verworfen: er wurde mit dem alten `T_flansch_cam`
        gerechnet, und jede daraus fortgeschriebene Kamerapose waere um genau
        den Unterschied versetzt.
        """
        self._hand_eye = hand_eye
        self._anchor = None
        self._drift = None

    async def close(self) -> None:
        await self.camera.close()
        await self.shutdown_executor()

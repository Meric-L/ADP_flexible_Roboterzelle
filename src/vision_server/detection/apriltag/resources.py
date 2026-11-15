"""Resources of the AprilTag source: calibration, tag map, hand-eye, detector, camera.

`ResourcesMixin` loads everything the source needs for measuring at start-up
(`open`), and swaps parts of it while running (`apply_*`). It expects these
attributes on the object: `_config`, `_camera_config`, `camera`, `_detector`,
`_calibration`, `_tag_map`, `_hand_eye`, `_anchor`, `_drift`, `_localization`
and `configuration_id`.
"""

import functools
import json
import logging
from typing import Any

_log = logging.getLogger(__package__)


class ResourcesMixin:
    """Load, replace and release the resources."""

    def _build_configuration_id(self) -> str:
        """Join the tag family, calibration and tag map into one string.

        Later this shows which calibration and which tag map were active behind a
        bad pose (doc/projektdoku/altlasten.md C4). It deliberately uses no OpenCV
        and does not read the files.
        """
        from tagloc.identity import calibration_identity, tag_map_identity

        return (
            f"{self._config.tag_family}@{calibration_identity(self._config.calibration_path)}"
            f"+{tag_map_identity(self._config.tag_map_path)}"
        )

    async def open(self) -> None:
        """Load calibration and tag map, build the detector and open the camera.

        If the calibration is missing, the source does not open and the server
        stays in Preoperational. This is intended, because a vision server without
        calibration would report meaningless numbers. There are two exceptions, in
        this order. A camera that knows its own factory intrinsics (RealSense on the
        hand Pi) uses them. They are measured, just not by us. The second is
        `allow_placeholder_calibration`, a deliberate temporary bypass to run
        detection before the real calibration run.
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
        """Load the calibration file if none is set yet.

        If the file is missing there are three paths. The RealSense decides only
        once the camera is open. Then the error is returned, and `open` calls
        `_fallback_calibration`. Otherwise a placeholder is used, if allowed.
        Otherwise the exception is raised before the camera is opened.
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
                # Decided only with the camera open: the RealSense knows its
                # factory intrinsics, but only while its pipeline is running.
                return error
            if not self._config.allow_placeholder_calibration:
                # No factory intrinsics: abort as before, without opening
                # the camera at all.
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
        """Load the tag map if none is set yet. Without a usable file, use an empty map."""
        from tagloc.tagmap import empty_tag_map, load_tag_map, validate_tag_map

        if self._tag_map is not None:
            return
        path = self._config.tag_map_path
        if path is not None and path.is_file():
            try:
                self._tag_map = await self.run_blocking(load_tag_map, path)
            except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
                # An unusable map must not stop detection. Without a map
                # everything keeps running: the tags are reported as TAG-<id>
                # in the camera frame, and the frontend gets its placeholders.
                # Otherwise the whole vision server would be dead because of
                # one text file.
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
                # Reported, not enforced: a cell still being built must be
                # allowed to measure. Silence would mean that a missing world
                # tag only shows up as quietly worse accuracy.
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
        """Load the hand-eye file if it is configured and not set yet."""
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
            # Not an error: the ceiling camera has none, and the hand Pi may
            # also measure without one. Then it works only with a world tag in view.
            _log.warning(
                "Keine Hand-Auge-Kalibrierung unter %s -- ohne Welttag im Bild "
                "bleiben die Posen im Kamera-KS",
                self._config.hand_eye_path,
            )

    async def _build_detector(self) -> None:
        """Build the detector if none is set yet, and log its parameters."""
        from tagloc.detector import build_detector

        if self._detector is not None:
            return
        # `run_blocking` only passes positional arguments through.
        # `subpixel_corner_refinement` is keyword-only in `build_detector` on
        # purpose, so it is bound here with `partial`.
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
        """Without a calibration file: use factory intrinsics, else a placeholder, else abort."""
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
        """The calibration in use (`CameraCalibration`), or ``None``."""
        return self._calibration

    @property
    def intrinsics_source(self) -> str:
        """Where the intrinsics come from: `calibration`, `realsense_factory` or `placeholder`."""
        from tagloc.calibration import PLACEHOLDER_CALIBRATION_ID

        from ...camera import REALSENSE_FACTORY_CALIBRATION_ID

        calibration_id = getattr(self._calibration, "calibration_id", "")
        if calibration_id == PLACEHOLDER_CALIBRATION_ID:
            return "placeholder"
        if calibration_id == REALSENSE_FACTORY_CALIBRATION_ID:
            return "realsense_factory"
        return "calibration"

    def apply_tag_map(self, tag_map: Any) -> list[str]:
        """Apply a new tag map to the running source.

        Returns the complaints from `validate_tag_map`. They are a report, not an
        error: a cell still being built may measure.

        The anchor is **discarded**. It was computed against the world poses of the
        old map. Against a new map it would be silently wrong. A silent offset in
        every module pose is the kind of error that only shows up when a gripper
        misses.
        """
        from tagloc.tagmap import validate_tag_map

        self._tag_map = tag_map
        self._anchor = None
        self._drift = None
        self._localization = None
        # The map is part of the configuration identity. Otherwise a later
        # result would look like one from before.
        self.configuration_id = self._build_configuration_id()
        return validate_tag_map(tag_map)

    def apply_calibration(self, calibration: Any) -> None:
        """Replace the active calibration without a restart.

        Called from `runner/calibration.py`, right after an interactive
        `CalibrationSession` (`StartCalibration`/`CaptureCalibrationSample`/
        `FinishCalibration`) has computed and saved a new calibration. `_locate()`
        reads `self._calibration` fresh for every job. So the swap takes effect
        from the next job, without restarting the server.
        """
        self._calibration = calibration
        self.configuration_id = self._build_configuration_id()

    def apply_hand_eye(self, hand_eye: Any) -> None:
        """Apply a new hand-eye calibration without a restart.

        The anchor is discarded. It was computed with the old `T_flansch_cam`, and
        every camera pose propagated from it would be off by exactly that difference.
        """
        self._hand_eye = hand_eye
        self._anchor = None
        self._drift = None

    async def close(self) -> None:
        """Close the camera and shut down the worker thread."""
        await self.camera.close()
        await self.shutdown_executor()

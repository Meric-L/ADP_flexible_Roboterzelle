"""Ein Bild fuer die Zellen-Ueberwachung auswerten: `observe_cell`.

`CellObserveMixin` ist die seiteneffektfreie Auswertung eines Bilds mit einem
eigenen Detektor der Ueberwachung (`cellwatch.py`): Module je Tag im Welt-KS,
ohne Anker, ohne Drift, ohne `_localization`. Er erwartet am Objekt
`_config`, `_tag_map` sowie `_calibration_for` und `_remember_tags` aus
`CaptureMixin`.
"""

from dataclasses import replace
from typing import Any


def _world_tag_errors(tag_poses, tag_map, pose_world_cam, calibration) -> dict:
    """Je sichtbarem Welttag: RMS (px) seiner Ecken unter `pose_world_cam`.

    Die Weltpose kommt aus der Tag-Map, projiziert wird mit dem Linsenmodell
    und verglichen mit den **rohen** (verzeichneten) Ecken des Detektors.
    """
    import numpy as np

    from tagloc import lens
    from tagloc.geometry import compose, invert, rvec_from_rotation
    from tagloc.pose import tag_object_points

    references = tag_map.reference_poses()
    errors = {}
    for tag_pose in tag_poses:
        world_pose = references.get(tag_pose.tag_id)
        if world_pose is None or tag_pose.observation is None:
            continue
        pose_cam_tag = compose(invert(pose_world_cam), world_pose)
        projected = lens.project_points(
            tag_object_points(tag_map.size_for(tag_pose.tag_id, 0.0)),
            rvec_from_rotation(pose_cam_tag[:3, :3]),
            np.ascontiguousarray(pose_cam_tag[:3, 3]),
            calibration,
        )
        seen = np.asarray(tag_pose.observation.corners, dtype=np.float64).reshape(4, 2)
        errors[int(tag_pose.tag_id)] = float(
            np.sqrt(np.mean(np.sum((projected - seen) ** 2, axis=1)))
        )
    return errors


class CellObserveMixin:
    """Ein Bild fuer die Zellen-Ueberwachung auswerten."""

    def observe_cell(self, image, detector: Any, stored_world_cam=None):
        """Ein Bild fuer die Zellen-Ueberwachung:
        `(Modul-Orte je Tag, Lokalisierung, alle erkannten Tag-IDs)`.

        Mit `stored_world_cam` (die frueher aus mindestens zwei Welttags
        geschaetzte Pose der fest montierten Kamera) rechnet das Bild auch dann
        im Welt-KS, wenn nur ein Welttag zu sehen ist: Die Pose aus einem
        einzelnen Tag ist aus 2,5 m um Zentimeter ungenau (gekippte Loesungen),
        die gespeicherte nicht. Die Lokalisierung hat dann `solver="stored"`,
        und `tag_errors_px` sagt je sichtbarem Welttag, wie weit seine Ecken
        unter dieser Pose von den gesehenen liegen -- die Konsistenzpruefung.

        Dieselbe Rechnung wie ein Job -- Tag-Posen, gemeinsames PnP ueber die
        Welttags, `locate_modules` --, aber **ohne Seiteneffekte**: kein Anker,
        keine Drift, kein `_localization`, kein `world_tags`. Und nicht
        zusammengefasst, damit jeder Tag fuer sich verglichen werden kann.

        `detector` ist eine eigene Instanz der Ueberwachung: sie laeuft in
        einem eigenen Thread, und ein Detektor ist nicht fuer zwei Threads
        zugleich gebaut. Laeuft blockierend -- im Worker-Thread aufrufen.

        Die erkannten IDs kommen auch dann zurueck, wenn das Bild keine
        Aussage erlaubt -- gerade dann will der Bediener sehen, welcher
        Welttag fehlt.
        """
        from tagloc import frames as frame_tools
        from tagloc.localize import locate_modules, localize_camera
        from tagloc.pose import estimate_tag_poses

        calibration = self._calibration_for(image)
        observations = detector.detect(frame_tools.to_gray(image))
        visible = sorted({int(observation.tag_id) for observation in observations})
        tag_poses = estimate_tag_poses(
            observations,
            calibration,
            tag_map=self._tag_map,
            default_size_m=self._config.tag_size_m,
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )
        self._remember_tags(tag_poses, image, "cellwatch")
        localization = localize_camera(
            tag_poses,
            self._tag_map,
            max_reprojection_error_px=self._config.max_reproj_error_px,
            calibration=calibration,
        )
        if localization is None:
            return [], None, visible
        localization = self._with_stored_pose(
            localization, tag_poses, stored_world_cam, calibration
        )
        return (
            locate_modules(
                tag_poses,
                self._tag_map,
                localization=localization,
                frame_id=self._tag_map.frame_id,
                max_reprojection_error_px=self._config.max_reproj_error_px,
            ),
            localization,
            visible,
        )

    def _with_stored_pose(self, localization, tag_poses, stored_world_cam, calibration):
        """Ersetzt eine Einzel-Tag-Pose durch die gespeicherte Kamerapose, samt Konsistenzfehlern.

        Nur wenn eine gespeicherte Pose da ist und das Bild keine gemeinsame
        Pose aus mehreren Welttags hergibt; sonst bleibt `localization`.
        """
        from tagloc.localize import SOLVER_JOINT, SOLVER_STORED

        if stored_world_cam is None or localization.solver == SOLVER_JOINT:
            return localization
        errors = _world_tag_errors(tag_poses, self._tag_map, stored_world_cam, calibration)
        return replace(
            localization,
            pose_world_cam=stored_world_cam,
            solver=SOLVER_STORED,
            reprojection_error_px=(
                sum(errors.values()) / len(errors) if errors else float("nan")
            ),
            tag_errors_px=errors,
        )

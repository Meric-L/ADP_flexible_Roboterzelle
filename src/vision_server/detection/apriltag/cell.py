"""Evaluate one image for the cell watch: `observe_cell`.

`CellObserveMixin` is the side-effect-free evaluation of an image with a
dedicated detector of the watch (`cellwatch.py`): modules per tag in the world
CS, without anchor, without drift, without `_localization`. It expects
`_config` and `_tag_map` on the object, plus `_calibration_for` and
`_remember_tags` from `CaptureMixin`.
"""

from dataclasses import replace
from typing import Any


def _world_tag_errors(tag_poses, tag_map, pose_world_cam, calibration) -> dict:
    """For each visible world tag: RMS (px) of its corners under `pose_world_cam`.

    The world pose comes from the tag map. It is projected with the lens model
    and compared with the **raw** (distorted) corners from the detector.
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
    """Evaluate one image for the cell watch."""

    def observe_cell(self, image, detector: Any, stored_world_cam=None):
        """Evaluate one image for the cell watch.

        Returns ``(module positions per tag, localization, all detected tag IDs)``.

        With `stored_world_cam` (the pose of the fixed camera, estimated earlier
        from at least two world tags), the image is also evaluated in the world
        CS when only one world tag is visible. A pose from a single tag is off by
        centimetres at 2.5 m (flipped solutions), while the stored pose is not.
        The localization then has `solver="stored"`. `tag_errors_px` gives, for
        each visible world tag, how far its corners under this pose are from the
        observed ones -- the consistency check.

        The same calculation as a job -- tag poses, joint PnP over the world
        tags, `locate_modules` -- but **without side effects**: no anchor, no
        drift, no `_localization`, no `world_tags`. Results are not aggregated,
        so each tag can be compared on its own.

        `detector` is a separate instance for the watch. It runs in its own
        thread, and a detector is not built for two threads at once. This call
        blocks, so run it in the worker thread.

        The detected IDs are returned even when the image allows no statement.
        That is exactly when the operator needs to see which world tag is missing.
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
        """Replace a single-tag pose with the stored camera pose, including the consistency errors.

        Only when a stored pose is available and the image yields no joint pose
        from several world tags. Otherwise `localization` stays as it is.
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

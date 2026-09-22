"""Tag-based localisation: calibration, AprilTag detection, coordinate frames.

Layout follows dependencies, not topics (see doc/projektdoku/apriltag-lokalisierung.md):

    geometry, calibration, tagmap, localize   -- numpy, **no cv2**
    boards, detector, pose, overlay, frames   -- cv2
    cli/*                                     -- thin adapters

The cv2-free half stays importable and testable regardless of the installed
OpenCV version (local 5.x, Pi 4.x, differing aruco API) -- hence
`geometry.from_rvec_tvec` does Rodrigues in numpy instead of `cv2.Rodrigues`,
and `calibration` reads JSON instead of OpenCV FileStorage YAML.

This package never imports `vision_server` at module level: the dependency
runs one way only, `vision_server.detection.apriltag` imports `tagloc`.
"""

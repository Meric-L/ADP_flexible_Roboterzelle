"""Tag-based localisation: calibration, AprilTag detection, coordinate frames.

The cv2-free modules (geometry, calibration, tagmap, localize) stay importable
under any OpenCV version, which is why geometry does Rodrigues in numpy. The
cv2-based modules are boards, detector, pose, overlay and frames; cli/ holds
thin adapters. This package never imports `vision_server` at module level.
See doc/projektdoku/apriltag-lokalisierung.md for the layout rationale.
"""

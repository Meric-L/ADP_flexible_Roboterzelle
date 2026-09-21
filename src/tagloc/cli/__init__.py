"""Command-line tools. Thin adapters over the library, no logic of their own.

    python -m tagloc.cli.calibrate      camera calibration
    python -m tagloc.cli.detect         detect AprilTags
    python -m tagloc.cli.transform      coordinate transform
    python -m tagloc.cli.build_tagmap   place tags into a shared frame

All accept `--source` as either a camera or an image folder.
"""

"""`TagSnapshot`: the tags of one image, used for the live-stream overlay."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TagSnapshot:
    """The tags of one image at full camera resolution, for the live-stream overlay.

    The live stream shows a downscaled image, but the overlay should show what
    detection finds on the full frame. Running detection at stream rate on the
    Pi would occupy a whole core (py-spy, 24.09.2026). So this source keeps the
    latest result that a job, the cell watch or a measurement already computed,
    and the overlay only draws it (`stream_overlay.py`). The corners are in
    pixels of `image_size`; the drawer scales them to the stream size.
    """

    tag_poses: tuple
    image_size: tuple[int, int]
    #: "job" | "cellwatch" | "measure" | "overlay"
    origin: str
    #: `time.monotonic()` taken after detection.
    taken_at: float

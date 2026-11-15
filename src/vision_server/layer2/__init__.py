"""Layer 2 run on the hand Pi: anchor at a world tag, then measure each module precisely.

``plan.py`` computes the approach targets (numpy, ``tagloc``), and ``run.py`` executes the
sequence (asyncio, without asyncua). Both are wired together in ``runner/layer2.py``. See
``doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md``.

Both modules are imported only when needed, so the ``vision_server`` package stays
importable without numpy, as with ``detection/apriltag.py``.
"""

"""Layer-2-Lauf des Hand-Pi: am Welttag ankern, dann jedes Modul fein messen.

`plan.py` rechnet die Fahrziele (numpy, `tagloc`), `run.py` fuehrt den Ablauf
(asyncio, ohne asyncua). Verdrahtet wird beides in `runner.py`. Siehe
`doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md`.

Beide Module werden erst bei Bedarf importiert -- das Paket `vision_server`
bleibt damit ohne numpy importierbar, wie bei `detection/apriltag.py`.
"""

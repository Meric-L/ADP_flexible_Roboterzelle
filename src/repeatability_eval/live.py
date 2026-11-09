"""Live-Messung beim Speichern: kalibrierfrei, wirft nie."""

import json
from typing import Any


def measure_for_save(image: Any, client_meta: Any) -> dict:
    """Homographie-Kennwerte des Bildes; `{"ok": False, "grund": ...}` bei jedem Problem.

    `client_meta`: JSON-Text oder dict; das Board steht unter `board`.
    """
    try:
        meta = client_meta
        if isinstance(meta, (str, bytes)):
            try:
                meta = json.loads(meta) if meta else None
            except ValueError:
                return {"ok": False, "grund": "Client-Metadaten sind kein gueltiges JSON"}
        board = meta.get("board") if isinstance(meta, dict) else None
        if not board:
            return {"ok": False, "grund": "kein Board in den Metadaten"}
        from .charuco import _detect, spec_from_dict
        from .metrics import homography_metrics

        spec = spec_from_dict(board)
        ids, corners, n_markers = _detect(image, spec)
        height, width = image.shape[:2]
        result = homography_metrics(ids, corners, spec, (width, height))
        result["nMarkers"] = n_markers
        return {"ok": True, **result}
    except Exception as error:
        return {"ok": False, "grund": f"{type(error).__name__}: {error}"}

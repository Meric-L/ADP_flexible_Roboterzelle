"""Druckbogen fuer AprilTags und ChArUco-Boards, masshaltig in Millimetern.

Output is **SVG**, not PNG: an SVG carries real millimeter measurements,
prints from any browser at "scale 100%", and stays dimensionally accurate.
A PNG instead depends on the printer driver's DPI assumption -- exactly the
error that comes back later as a constant distance offset.

Every sheet carries a 100 mm scale bar, so a ruler after printing confirms
the printer really didn't rescale anything.

    python tools/make_tag_sheet.py --ids 0 1 2 3 7 12 --size-mm 100 --out tags.svg
    python tools/make_tag_sheet.py --charuco --cols 7 --rows 5 \
        --square-mm 30 --marker-mm 22 --out board.svg
"""

import argparse
import base64
import sys
from pathlib import Path

A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0
MARGIN_MM = 12.0
LABEL_MM = 6.0
#: Resolution of the embedded bitmap. Markers are rectangles; more pixels
#: only add file size, too few create rounding artifacts.
PIXELS_PER_MM = 8


def _png_data_uri(image) -> str:
    import cv2

    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("PNG-Encode fehlgeschlagen")
    return "data:image/png;base64," + base64.b64encode(buffer).decode("ascii")


def _dictionary(name: str):
    import cv2

    aruco = cv2.aruco
    if not hasattr(aruco, name):
        raise SystemExit(f"Diese OpenCV-Version kennt '{name}' nicht")
    identifier = getattr(aruco, name)
    if hasattr(aruco, "getPredefinedDictionary"):
        return aruco.getPredefinedDictionary(identifier)
    return aruco.Dictionary_get(identifier)


def render_marker(dictionary, tag_id: int, size_mm: float):
    """Render one marker as a bitmap at print resolution."""
    import cv2

    pixels = max(64, int(size_mm * PIXELS_PER_MM))
    if hasattr(cv2.aruco, "generateImageMarker"):
        return cv2.aruco.generateImageMarker(dictionary, tag_id, pixels)
    return cv2.aruco.drawMarker(dictionary, tag_id, pixels)


def render_charuco(spec_cols: int, spec_rows: int, square_mm: float, marker_mm: float, name: str):
    """Render a ChArUco board as a bitmap, plus its size in millimeters."""
    import cv2

    dictionary = _dictionary(name)
    width_mm = spec_cols * square_mm
    height_mm = spec_rows * square_mm
    size_px = (int(width_mm * PIXELS_PER_MM), int(height_mm * PIXELS_PER_MM))
    aruco = cv2.aruco
    if hasattr(aruco, "CharucoBoard"):
        try:
            board = aruco.CharucoBoard(
                (spec_cols, spec_rows), square_mm / 1000.0, marker_mm / 1000.0, dictionary
            )
        except TypeError:
            board = aruco.CharucoBoard_create(
                spec_cols, spec_rows, square_mm / 1000.0, marker_mm / 1000.0, dictionary
            )
    else:
        board = aruco.CharucoBoard_create(
            spec_cols, spec_rows, square_mm / 1000.0, marker_mm / 1000.0, dictionary
        )
    image = board.generateImage(size_px) if hasattr(board, "generateImage") else board.draw(size_px)
    return image, width_mm, height_mm


def _scale_bar(x_mm: float, y_mm: float, length_mm: float = 100.0) -> list[str]:
    """Draw a scale bar for measuring after printing."""
    parts = [
        f'<line x1="{x_mm}mm" y1="{y_mm}mm" x2="{x_mm + length_mm}mm" y2="{y_mm}mm" '
        'stroke="black" stroke-width="0.4mm"/>'
    ]
    for step in range(0, int(length_mm) + 1, 10):
        tick = 3.0 if step % 50 == 0 else 1.8
        parts.append(
            f'<line x1="{x_mm + step}mm" y1="{y_mm - tick}mm" '
            f'x2="{x_mm + step}mm" y2="{y_mm}mm" stroke="black" stroke-width="0.3mm"/>'
        )
    parts.append(
        f'<text x="{x_mm}mm" y="{y_mm + 5}mm" font-family="sans-serif" font-size="3.5mm">'
        f"Massstabskontrolle: diese Linie muss exakt {length_mm:.0f} mm lang sein "
        "(sonst hat der Drucker skaliert)</text>"
    )
    return parts


def build_svg(items, title: str) -> str:
    """Assemble the sheet. `items` are `(data_uri, x, y, w, h, label)` in mm.

    Pure string work, deliberately without OpenCV, so layout is testable
    even without OpenCV installed.
    """
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{A4_WIDTH_MM}mm" height="{A4_HEIGHT_MM}mm" '
        f'viewBox="0 0 {A4_WIDTH_MM} {A4_HEIGHT_MM}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{MARGIN_MM}mm" y="{MARGIN_MM}mm" font-family="sans-serif" '
        f'font-size="4mm">{title}</text>',
    ]
    for data_uri, x_mm, y_mm, width_mm, height_mm, label in items:
        parts.append(
            f'<image x="{x_mm}mm" y="{y_mm}mm" width="{width_mm}mm" height="{height_mm}mm" '
            f'image-rendering="pixelated" xlink:href="{data_uri}"/>'
        )
        if label:
            parts.append(
                f'<text x="{x_mm}mm" y="{y_mm + height_mm + 4.5}mm" font-family="sans-serif" '
                f'font-size="3.5mm">{label}</text>'
            )
    parts.extend(_scale_bar(MARGIN_MM, A4_HEIGHT_MM - MARGIN_MM))
    parts.append("</svg>")
    return "\n".join(parts)


def layout_tags(dictionary, ids, size_mm: float):
    """Arrange markers in columns and rows on A4."""
    cell = size_mm + LABEL_MM + 6.0
    columns = max(1, int((A4_WIDTH_MM - 2 * MARGIN_MM) / cell))
    items = []
    for index, tag_id in enumerate(ids):
        column, row = index % columns, index // columns
        x_mm = MARGIN_MM + column * cell
        y_mm = MARGIN_MM + 8.0 + row * cell
        if y_mm + size_mm > A4_HEIGHT_MM - 2 * MARGIN_MM - 8.0:
            print(
                f"Bogen voll, {len(ids) - index} Tags passen nicht mehr darauf",
                file=sys.stderr,
            )
            break
        items.append(
            (
                _png_data_uri(render_marker(dictionary, int(tag_id), size_mm)),
                x_mm,
                y_mm,
                size_mm,
                size_mm,
                f"ID {tag_id}   Sollkante {size_mm:.0f} mm",
            )
        )
    return items


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="Zieldatei (.svg)")
    parser.add_argument("--ids", type=int, nargs="+", default=[0, 1, 2, 3], help="Tag-IDs")
    parser.add_argument("--size-mm", type=float, default=100.0, help="Kantenlaenge des Markers")
    parser.add_argument("--family", default="DICT_APRILTAG_36h11", help="cv2.aruco-Dictionary")
    parser.add_argument("--charuco", action="store_true", help="Kalibrierboard statt Tags")
    parser.add_argument("--cols", type=int, default=7)
    parser.add_argument("--rows", type=int, default=5)
    parser.add_argument("--square-mm", type=float, default=30.0)
    parser.add_argument("--marker-mm", type=float, default=22.0)
    parser.add_argument("--board-dictionary", default="DICT_4X4_50")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.out.suffix.lower() != ".svg":
        print(f"Warnung: {args.out.name} ist kein .svg -- geschrieben wird SVG", file=sys.stderr)

    if args.charuco:
        image, width_mm, height_mm = render_charuco(
            args.cols, args.rows, args.square_mm, args.marker_mm, args.board_dictionary
        )
        items = [
            (
                _png_data_uri(image),
                MARGIN_MM,
                MARGIN_MM + 8.0,
                width_mm,
                height_mm,
                f"ChArUco {args.cols}x{args.rows}, Feld {args.square_mm:.0f} mm, "
                f"Marker {args.marker_mm:.0f} mm, {args.board_dictionary}",
            )
        ]
        title = f"Kalibrierboard ChArUco {args.cols}x{args.rows}"
    else:
        items = layout_tags(_dictionary(args.family), args.ids, args.size_mm)
        title = f"AprilTags {args.family}, Kantenlaenge {args.size_mm:.0f} mm"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_svg(items, title), encoding="utf-8")
    print(f"Geschrieben: {args.out}")
    print("Drucken mit Massstab 100 % / Originalgroesse, danach den Massstab nachmessen.")
    print("Gemessene Kante in die Tag-Map eintragen, nicht die bestellte.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

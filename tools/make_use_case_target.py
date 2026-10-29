"""Zielscheibe v2 und Wuerfelaufkleber fuer die Use-Case-Bildmessung (A4, 1:1, SVG/PDF).

Seite 1 (`zielscheibe.svg`): die bisherige Zielscheibe (Winkelskala, Fadenkreuz,
15-Grad-Strahlen, Toleranzkreise, 20-mm-Quadrat, Legende, 50-mm-Massstab) plus
vier ArUco-Marker (DICT_4X4_50, IDs 0-3, 20 mm) bei (+-45, +-45) mm. Um jeden
Marker liegt eine weisse Ruhezone von einer Markerzelle; Strahlen, Teilstriche und
Skalenring sind dort ausgespart.
Seite 2 (`wuerfelmarker.svg`): Aufkleber ID 10 (18 mm) mit 20-mm-Schnittrahmen.

Alle Masse stammen aus `use_case_eval.target`. SVG traegt echte Millimeter und
druckt aus jedem Browser mit "Massstab 100 %". PDF entsteht nur, wenn `inkscape`
oder `rsvg-convert` installiert ist (keine Python-Abhaengigkeit).

    python tools/make_use_case_target.py
    python tools/make_use_case_target.py --out data/use_case_target --pdf --stickers 12
"""

import argparse
import math
import shutil
import subprocess
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from use_case_eval import target  # noqa: E402

A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0
#: Scheibenmitte auf der Seite (von links unten 105 / 158,5 mm).
CENTER_X_MM = 105.0
CENTER_Y_FROM_TOP_MM = 138.5
PT = 25.4 / 72.0  # 1 pt in mm
FONT = "Helvetica, Arial, sans-serif"
CIRCLE_COLORS = {"gruen": "#1aa633", "gelb": "#f2bf00", "rot": "#d91a1a"}
CIRCLE_NAMES = {"gruen": "Green", "gelb": "Yellow", "rot": "Red"}
#: Zusatz zur Diagonalen 28,3 mm je Zone (mm je Seite).
CIRCLE_MARGINS_MM = {"gruen": 1, "gelb": 3, "rot": 5}


def _f(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _pos(x_mm: float, y_mm: float) -> tuple[float, float]:
    """Blattkoordinaten (y oben) -> SVG-Koordinaten der Seite (y unten)."""
    return CENTER_X_MM + x_mm, CENTER_Y_FROM_TOP_MM - y_mm


def _line(x1, y1, x2, y2, width_mm, color="black", dash=None) -> str:
    ax, ay = _pos(x1, y1)
    bx, by = _pos(x2, y2)
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<line x1="{_f(ax)}" y1="{_f(ay)}" x2="{_f(bx)}" y2="{_f(by)}" '
        f'stroke="{color}" stroke-width="{_f(width_mm)}"{dash_attr}/>'
    )


def marker_path(dictionary, marker_id: int, size_mm: float, x_mm: float, y_mm: float) -> str:
    """Ein Marker als einzelner SVG-Pfad (schwarze Zellen); (x, y) = Ecke oben-links in SVG-mm."""
    import cv2

    grid = cv2.aruco.generateImageMarker(dictionary, marker_id, 6)  # 4x4 Bits + 1 Zelle Rand
    cell = size_mm / grid.shape[0]
    commands = []
    for row in range(grid.shape[0]):
        for column in range(grid.shape[1]):
            if grid[row, column] < 128:
                commands.append(
                    f"M{_f(x_mm + column * cell)} {_f(y_mm + row * cell)}"
                    f"h{_f(cell)}v{_f(cell)}h{_f(-cell)}z"
                )
    return f'<path d="{"".join(commands)}" fill="black" shape-rendering="crispEdges"/>'


def _svg_open() -> list[str]:
    return [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_f(A4_WIDTH_MM)}mm" '
        f'height="{_f(A4_HEIGHT_MM)}mm" viewBox="0 0 {_f(A4_WIDTH_MM)} {_f(A4_HEIGHT_MM)}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]


def _scale_bar(x_mm: float, y_mm: float, length_mm: float = 50.0) -> list[str]:
    """50-mm-Massstab mit Strichen alle 10 mm (SVG-Koordinaten, y = Balkenlinie)."""
    parts = [
        f'<line x1="{_f(x_mm)}" y1="{_f(y_mm)}" x2="{_f(x_mm + length_mm)}" y2="{_f(y_mm)}" '
        f'stroke="black" stroke-width="{_f(0.5 * PT)}"/>'
    ]
    for step in range(0, int(length_mm) + 1, 10):
        parts.append(
            f'<line x1="{_f(x_mm + step)}" y1="{_f(y_mm - 2.4)}" x2="{_f(x_mm + step)}" '
            f'y2="{_f(y_mm)}" stroke="black" stroke-width="{_f(0.5 * PT)}"/>'
        )
    parts.append(
        f'<text x="{_f(x_mm)}" y="{_f(y_mm + 3.9)}" font-family="{FONT}" font-size="2.5">'
        f"{length_mm:.0f} mm scale bar - print at 100% (no fit-to-page)</text>"
    )
    return parts


def build_target_svg() -> str:
    """Seite 1: Zielscheibe v2 mit Blattmarkern."""
    import cv2

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    r_in, r_out = target.RING_INNER_RADIUS_MM, target.RING_OUTER_RADIUS_MM
    parts = _svg_open()

    # Skalenring.
    cx, cy = _pos(0, 0)
    for radius in (r_in, r_out):
        parts.append(
            f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(radius)}" fill="none" '
            f'stroke="black" stroke-width="{_f(0.5 * PT)}"/>'
        )
    # Teilstriche je 1 Grad (alle 5 Grad laenger, alle 10 Grad am laengsten) und Zahlen.
    for degree in range(360):
        inner = r_in - (4.0 if degree % 10 == 0 else 2.5 if degree % 5 == 0 else 1.5)
        a = math.radians(degree)
        parts.append(
            _line(
                inner * math.cos(a), inner * math.sin(a),
                r_in * math.cos(a), r_in * math.sin(a), 0.3 * PT,
            )
        )
        if degree % 10 == 0:
            lx, ly = _pos(86.0 * math.cos(a), 86.0 * math.sin(a))
            parts.append(
                f'<text x="{_f(lx)}" y="{_f(ly)}" font-family="{FONT}" font-size="3" '
                f'text-anchor="middle" dominant-baseline="central" '
                f'transform="rotate({90 - degree} {_f(lx)} {_f(ly)})">{degree}</text>'
            )
    # Fadenkreuz, 15-Grad-Strahlen (grau, gestrichelt) ueber den vollen Durchmesser.
    for degree in range(15, 180, 15):
        if degree == 90:
            continue
        a = math.radians(degree)
        parts.append(
            _line(
                -r_in * math.cos(a), -r_in * math.sin(a),
                r_in * math.cos(a), r_in * math.sin(a),
                0.3 * PT, color="#8c8c8c", dash=f"{_f(2 * PT)} {_f(2 * PT)}",
            )
        )
    parts.append(_line(-r_in, 0, r_in, 0, 0.5 * PT))
    parts.append(_line(0, -r_in, 0, r_in, 0.5 * PT))
    # Toleranzkreise, von gross nach klein; halbtransparente Fuellung, voller Rand.
    for zone, radius in reversed(target.ZONE_RADII_MM):
        parts.append(
            f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(radius)}" fill="{CIRCLE_COLORS[zone]}" '
            f'fill-opacity="0.25" stroke="{CIRCLE_COLORS[zone]}" stroke-width="{_f(PT)}"/>'
        )
    # 20-mm-Quadrat (gestrichelt) und kleines Fadenkreuz.
    half = target.CUBE_SIZE_MM / 2.0
    qx, qy = _pos(-half, half)
    parts.append(
        f'<rect x="{_f(qx)}" y="{_f(qy)}" width="{_f(target.CUBE_SIZE_MM)}" '
        f'height="{_f(target.CUBE_SIZE_MM)}" fill="none" stroke="black" '
        f'stroke-width="{_f(0.5 * PT)}" stroke-dasharray="{_f(1.5 * PT)} {_f(1.5 * PT)}"/>'
    )
    parts.append(_line(-3, 0, 3, 0, 0.5 * PT))
    parts.append(_line(0, -3, 0, 3, 0.5 * PT))

    # Blattmarker: erst weisse Ruhezone (spart alles darunter aus), dann der Marker.
    quiet = target.SHEET_MARKER_QUIET_MM
    outer = target.SHEET_MARKER_SIZE_MM / 2.0 + quiet
    for marker_id, (mx, my) in target.SHEET_MARKERS.items():
        zx, zy = _pos(mx - outer, my + outer)
        parts.append(
            f'<rect x="{_f(zx)}" y="{_f(zy)}" width="{_f(2 * outer)}" height="{_f(2 * outer)}" '
            'fill="white"/>'
        )
        ox, oy = _pos(mx - target.SHEET_MARKER_SIZE_MM / 2, my + target.SHEET_MARKER_SIZE_MM / 2)
        parts.append(marker_path(dictionary, marker_id, target.SHEET_MARKER_SIZE_MM, ox, oy))

    # Legende links unten, Massstab rechts unten.
    parts.append(
        f'<text x="25" y="241.5" font-family="{FONT}" font-size="3" font-weight="bold">'
        f"Cube calibration target, circles ({target.CUBE_SIZE_MM:.0f} mm cube, "
        "rotation-independent)</text>"
    )
    for row, (zone, diameter) in enumerate(target.CIRCLES):
        y = 246.2 + row * 5.0
        parts.append(
            f'<rect x="25" y="{_f(y - 2.5)}" width="4.2" height="3.4" fill="{CIRCLE_COLORS[zone]}"/>'
        )
        parts.append(
            f'<text x="31" y="{_f(y)}" font-family="{FONT}" font-size="2.6">'
            f"{CIRCLE_NAMES[zone]}: {diameter:.1f} mm dia. (cube diagonal 28.3 + 2 x "
            f"{CIRCLE_MARGINS_MM[zone]} mm)</text>"
        )
    parts.append(
        f'<text x="25" y="{_f(246.2 + 3 * 5.0)}" font-family="{FONT}" font-size="2.6">'
        f"Markers: ArUco 4x4_50, IDs 0-3, {target.SHEET_MARKER_SIZE_MM:.0f} mm, "
        "centres at +-45 mm</text>"
    )
    parts.extend(_scale_bar(135.5, 244.0))
    parts.append("</svg>")
    return "\n".join(parts)


def build_cube_svg(stickers: int = 12) -> str:
    """Seite 2: Wuerfelaufkleber (ID 10, 18 mm) mit 20-mm-Schnittrahmen."""
    import cv2

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    parts = _svg_open()
    parts.append(
        f'<text x="15" y="18" font-family="{FONT}" font-size="4" font-weight="bold">'
        f"Cube markers: ArUco 4x4_50, ID {target.CUBE_MARKER_ID}, "
        f"{target.CUBE_MARKER_SIZE_MM:.0f} mm</text>"
    )
    parts.append(
        f'<text x="15" y="24" font-family="{FONT}" font-size="2.8">'
        f"Cut along the thin {target.CUBE_SIZE_MM:.0f} mm frame, stick centred on the cube top. "
        "Print at 100% (no fit-to-page).</text>"
    )
    pitch = 32.0
    columns = max(1, int((A4_WIDTH_MM - 2 * 15.0) // pitch))
    for index in range(stickers):
        column, row = index % columns, index // columns
        left = 15.0 + column * pitch + (pitch - target.CUBE_SIZE_MM) / 2
        top = 35.0 + row * pitch
        if top + target.CUBE_SIZE_MM > A4_HEIGHT_MM - 30.0:
            print(f"Seite voll, {stickers - index} Aufkleber passen nicht mehr", file=sys.stderr)
            break
        parts.append(
            f'<rect x="{_f(left)}" y="{_f(top)}" width="{_f(target.CUBE_SIZE_MM)}" '
            f'height="{_f(target.CUBE_SIZE_MM)}" fill="none" stroke="black" '
            f'stroke-width="{_f(0.15)}"/>'
        )
        margin = (target.CUBE_SIZE_MM - target.CUBE_MARKER_SIZE_MM) / 2.0
        parts.append(
            marker_path(
                dictionary, target.CUBE_MARKER_ID, target.CUBE_MARKER_SIZE_MM,
                left + margin, top + margin,
            )
        )
    parts.extend(_scale_bar(15.0, A4_HEIGHT_MM - 20.0))
    parts.append("</svg>")
    return "\n".join(parts)


def svg_to_pdf(svg: Path, pdf: Path) -> bool:
    """SVG nach PDF per `inkscape` oder `rsvg-convert`; `False`, wenn keines vorhanden ist."""
    if shutil.which("inkscape"):
        command = ["inkscape", str(svg), "--export-type=pdf", f"--export-filename={pdf}"]
    elif shutil.which("rsvg-convert"):
        command = ["rsvg-convert", "-f", "pdf", "-o", str(pdf), str(svg)]
    else:
        return False
    subprocess.run(command, check=True, capture_output=True)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "use_case_target",
        help="Zielordner (Standard: data/use_case_target)",
    )
    parser.add_argument("--stickers", type=int, default=12, help="Anzahl Wuerfelaufkleber auf Seite 2")
    parser.add_argument("--pdf", action="store_true", help="zusaetzlich PDF (braucht inkscape/rsvg-convert)")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, svg in (
        ("zielscheibe", build_target_svg()),
        ("wuerfelmarker", build_cube_svg(args.stickers)),
    ):
        path = args.out / f"{name}.svg"
        path.write_text(svg, encoding="utf-8")
        written.append(path)
        if args.pdf:
            if svg_to_pdf(path, path.with_suffix(".pdf")):
                written.append(path.with_suffix(".pdf"))
            else:
                print("Kein PDF: weder inkscape noch rsvg-convert gefunden (SVG genuegt zum Drucken)",
                      file=sys.stderr)
    for path in written:
        print(f"Geschrieben: {path}")
    print("Drucken mit Massstab 100 % / Originalgroesse, danach den 50-mm-Massstab nachmessen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

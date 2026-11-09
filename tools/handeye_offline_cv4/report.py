#!/usr/bin/env python3
"""Berichtsgenerator Hand-Auge-Offline-Auswertung (OpenCV-Vergleich).

Nur Standardbibliothek. Liest ausschliesslich docs/auswertung/handeye_cv4/
(<session>.json, <session>_posen.csv, vergleich.json, drift.json, optional befunde.html)
und schreibt docs/auswertung/calibration_cv4_vergleich.html (eigenstaendig, alles inline).

Aufruf: python3 tools/handeye_offline_cv4/report.py
"""
import csv
import json
import math
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IN_DIR = ROOT / "docs" / "auswertung" / "handeye_cv4"
OUT = ROOT / "docs" / "auswertung" / "calibration_cv4_vergleich.html"

S1 = "20261109T125050Z_a5c964f0"
S2 = "20261109T125825Z_a580eaba"
SESSIONS = [(S1, "S1", "ChArUco"), (S2, "S2", "Schachbrett")]
GROUPS = ["A", "B", "C", "D", "E"]
SHAPES = {"A": "circle", "B": "square", "C": "triangle", "D": "diamond", "E": "plus"}
SHAPE_NAME = {"A": "Kreis", "B": "Quadrat", "C": "Dreieck", "D": "Raute", "E": "Plus"}
METHODS = ["park", "tsai", "daniilidis", "park_numpy"]
EXTRA = ["horaud", "andreff"]
R = 6  # Markerradius (Durchmesser 12 viewBox-Einheiten, >= 8 px auch bei 360 px Breite)


# ---------------------------------------------------------------- Helfer
def de(x, d=2):
    """Deutsche Zahlenformatierung."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "–"
    return f"{x:.{d}f}".replace(".", ",").replace("-", "−")


def sci(x):
    if x == 0:
        return "0"
    e = int(math.floor(math.log10(abs(x))))
    m = x / 10 ** e
    sup = str(e).replace("-", "⁻")
    for a, b in zip("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"):
        sup = sup.replace(a, b)
    return f"{de(m, 1)}·10{sup}"


def norm(v):
    return math.sqrt(sum(a * a for a in v))


def sub(a, b):
    return [x - y for x, y in zip(a, b)]


def median(v):
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def nice_ticks(lo, hi, n=5):
    span = hi - lo or 1.0
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = 10 * mag
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            step = m * mag
            break
    t = math.ceil(lo / step - 1e-9) * step
    ticks = []
    while t <= hi + 1e-9:
        ticks.append(round(t, 10))
        t += step
    return ticks, step


def axis_for(values, pad=0.06, zero=False):
    """Liefert ((lo, hi), ticks, step)."""
    lo, hi = min(values), max(values)
    if zero:
        lo = 0.0
    d = (hi - lo) or 1.0
    plo, phi = (lo, hi + d * pad) if zero else (lo - d * pad, hi + d * pad)
    ticks, step = nice_ticks(plo, phi)
    if zero:
        top = max(phi, ticks[-1])
        if top < hi:
            top = hi
        return (0.0, top), ticks, step
    return (min(plo, ticks[0]), max(phi, ticks[-1])), ticks, step


def tf(step):
    d = 0 if step >= 1 else (1 if step >= 0.1 else (2 if step >= 0.01 else 3))
    return lambda v: de(v, d)


def dodge(items, min_gap, lo, hi):
    """items: Liste (key, y). Gibt dict key->y mit Mindestabstand (einfaches Relaxieren)."""
    it = sorted(items, key=lambda t: t[1])
    ys = [y for _, y in it]
    for _ in range(100):
        moved = False
        for i in range(1, len(ys)):
            if ys[i] - ys[i - 1] < min_gap - 1e-6:
                shift = (min_gap - (ys[i] - ys[i - 1])) / 2
                ys[i - 1] -= shift
                ys[i] += shift
                moved = True
        ys = [min(max(y, lo), hi) for y in ys]
        if not moved:
            break
    for i in range(1, len(ys)):  # Rand-Klemmung darf Abstand nicht verletzen
        ys[i] = max(ys[i], ys[i - 1] + min_gap)
    for i in range(len(ys) - 2, -1, -1):
        if ys[i] > hi - min_gap * (len(ys) - 1 - i):
            ys[i] = hi - min_gap * (len(ys) - 1 - i)
    return {k: y for (k, _), y in zip(it, ys)}


# ---------------------------------------------------------------- SVG
def marker(group, cx, cy, title):
    """Marker mit Gruppenfarbe, fester Form und 2-px-Ring in Flaechenfarbe."""
    col = f"var(--g-{group.lower()})"
    ring = "var(--surface)"
    shp = SHAPES[group]
    t = f"<title>{escape(title)}</title>"
    if shp == "circle":
        return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R}" fill="{col}" stroke="{ring}" stroke-width="2">{t}</circle>'
    if shp == "square":
        return (f'<rect x="{cx - R:.1f}" y="{cy - R:.1f}" width="{2 * R}" height="{2 * R}" fill="{col}" '
                f'stroke="{ring}" stroke-width="2">{t}</rect>')
    if shp == "triangle":
        pts = f"{cx:.1f},{cy - R - 1:.1f} {cx + R + 1:.1f},{cy + R:.1f} {cx - R - 1:.1f},{cy + R:.1f}"
        return f'<polygon points="{pts}" fill="{col}" stroke="{ring}" stroke-width="2" stroke-linejoin="round">{t}</polygon>'
    if shp == "diamond":
        r = R + 2
        pts = f"{cx:.1f},{cy - r:.1f} {cx + r:.1f},{cy:.1f} {cx:.1f},{cy + r:.1f} {cx - r:.1f},{cy:.1f}"
        return f'<polygon points="{pts}" fill="{col}" stroke="{ring}" stroke-width="2" stroke-linejoin="round">{t}</polygon>'
    r = R + 1
    d = f"M{cx - r:.1f},{cy:.1f}H{cx + r:.1f}M{cx:.1f},{cy - r:.1f}V{cy + r:.1f}"
    return (f'<g>{t}<path d="{d}" stroke="{ring}" stroke-width="7" stroke-linecap="round" fill="none"/>'
            f'<path d="{d}" stroke="{col}" stroke-width="3.5" stroke-linecap="round" fill="none"/></g>')


def legend_groups():
    out = ['<div class="legend" role="list" aria-label="Legende Posegruppen">']
    for g in GROUPS:
        m = marker(g, 10, 10, f"Gruppe {g}")
        out.append(f'<span class="lg" role="listitem"><svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">{m}</svg>'
                   f'Gruppe {g} ({SHAPE_NAME[g]})</span>')
    out.append("</div>")
    return "".join(out)


class Chart:
    def __init__(self, w, h, xlim, ylim, xticks, yticks, xfmt, yfmt, xlabel, ylabel,
                 ml=58, mr=14, mt=12, mb=44):
        self.w, self.h = w, h
        self.ml, self.mr, self.mt, self.mb = ml, mr, mt, mb
        self.sx = lambda v: ml + (v - xlim[0]) / ((xlim[1] - xlim[0]) or 1) * (w - ml - mr)
        self.sy = lambda v: h - mb - (v - ylim[0]) / ((ylim[1] - ylim[0]) or 1) * (h - mt - mb)
        self.parts = p = []
        for t in yticks:
            y = self.sy(t)
            p.append(f'<line x1="{ml}" x2="{w - mr}" y1="{y:.1f}" y2="{y:.1f}" class="gl"/>')
            p.append(f'<text x="{ml - 6}" y="{y + 4:.1f}" text-anchor="end" class="tk">{yfmt(t)}</text>')
        for t in xticks:
            x = self.sx(t)
            p.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{mt}" y2="{h - mb}" class="gl"/>')
            p.append(f'<text x="{x:.1f}" y="{h - mb + 16}" text-anchor="middle" class="tk">{xfmt(t)}</text>')
        p.append(f'<line x1="{ml}" x2="{w - mr}" y1="{h - mb}" y2="{h - mb}" class="ax"/>')
        p.append(f'<line x1="{ml}" x2="{ml}" y1="{mt}" y2="{h - mb}" class="ax"/>')
        p.append(f'<text x="{(ml + w - mr) / 2:.1f}" y="{h - 6}" text-anchor="middle" class="al">{escape(xlabel)}</text>')
        p.append(f'<text transform="translate(13 {(mt + h - mb) / 2:.1f}) rotate(-90)" text-anchor="middle" class="al">{escape(ylabel)}</text>')

    def add(self, s):
        self.parts.append(s)

    def text(self, x, y, s, anchor="start"):
        self.parts.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" class="dl">{escape(s)}</text>')

    def svg(self, label):
        return (f'<svg viewBox="0 0 {self.w} {self.h}" width="100%" role="img" aria-label="{escape(label)}">'
                + "".join(self.parts) + "</svg>")


def table(headers, rows, cls="", caption=None):
    h = "".join(f"<th>{escape(x)}</th>" for x in headers)
    b = []
    for r in rows:
        if isinstance(r, str):
            b.append(r)
        else:
            b.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    cap = f"<caption>{escape(caption)}</caption>" if caption else ""
    return f'<div class="scroll"><table class="{cls}">{cap}<thead><tr>{h}</tr></thead><tbody>{"".join(b)}</tbody></table></div>'


def details(inner, summary="Tabelle"):
    return f"<details><summary>{escape(summary)}</summary>{inner}</details>"


# ---------------------------------------------------------------- Daten
def load():
    sess = {}
    for sid, short, kind in SESSIONS:
        j = json.loads((IN_DIR / f"{sid}.json").read_text(encoding="utf-8"))
        rows = []
        with open(IN_DIR / f"{sid}_posen.csv", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append({
                    "id": r["poseId"], "g": r["poseGroup"], "t": float(r["tSeconds"]),
                    "reproj": float(r["reprojRmsPx"]), "pnp": float(r["pnpRmsPx"]),
                    "tilt": float(r["tiltDeg"]), "yaw": float(r["yawDeg"]),
                    "dist": float(r["distanceMm"]), "dev": float(r["boardDevMm"]),
                })
        loo = {p["poseId"]: p["dtNormMm"] for p in j["leaveOneOut"]["perPose"]}
        sess[sid] = {"j": j, "rows": rows, "loo": loo, "short": short, "kind": kind}
    vg = json.loads((IN_DIR / "vergleich.json").read_text(encoding="utf-8"))
    dr = {d["session"]: d for d in json.loads((IN_DIR / "drift.json").read_text(encoding="utf-8"))}
    bf = IN_DIR / "befunde.html"
    befunde = bf.read_text(encoding="utf-8") if bf.exists() else "<p><em>Befundtext folgt.</em></p>"
    return sess, vg, dr, befunde


# ---------------------------------------------------------------- Abschnitte
def kpis(sess):
    park_dev = max(sess[sid]["j"]["methods"]["park"]["diffToParkNumpy"]["dtNormMm"] for sid, _, _ in SESSIONS)
    t = {sid: sess[sid]["j"]["finalSolution"]["translationMm"] for sid, _, _ in SESSIONS}
    dist = norm(sub(t[S1], t[S2]))

    def tile(big, small, extra=""):
        return f'<div class="kpi"><b>{big}</b><span>{small}</span>{extra}</div>'

    def vec(v):
        return f"<em>({de(v[0], 1)} | {de(v[1], 1)} | {de(v[2], 1)}) mm</em>"
    return ('<div class="kpis">'
            + tile(f"{sci(park_dev)} mm", "cv2-Park = park_numpy (max. Abweichung beider Sitzungen)")
            + tile(f"{de(norm(t[S1]), 1)} mm", "Verfeinerte Translation S1 (Betrag)", vec(t[S1]))
            + tile(f"{de(norm(t[S2]), 1)} mm", "Verfeinerte Translation S2 (Betrag)", vec(t[S2]))
            + tile(f"{de(dist, 2)} mm", "Abstand der Sitzungen (S1 − S2, verfeinert)")
            + tile("0,5 mm", "Ziel – nicht belegt")
            + "</div>")


def method_section(sess):
    out = ['<h2 id="verfahren">Daten: Verfahrensvergleich</h2>',
           '<p class="sub">Hand-Auge-Translation der Kamera im Flansch je Verfahren (cv2.calibrateHandEye, OpenCV 4.14) und nach '
           'nichtlinearer Verfeinerung. Δ = Abstand der Translation zu park_numpy.</p>']
    panels = []
    for sid, short, kind in SESSIONS:
        j = sess[sid]["j"]
        ref = j["methods"]["park_numpy"]["translationMm"]
        rows, pts = [], []

        def row(name, t, rot, rms, d):
            return [escape(name), de(t[0]), de(t[1]), de(t[2]), de(rot), de(rms, 3), de(d, 3)]
        for m in METHODS:
            d = j["methods"][m]
            rows.append(row(m, d["translationMm"], d["rotationDeg"], d["reprojectionRmsPx"], d["diffToParkNumpy"]["dtNormMm"]))
            pts.append((m, d["translationMm"], "m"))
        rows.append('<tr class="sec"><td colspan="7">Zusatz</td></tr>')
        for m in EXTRA:
            d = j["methods"][m]
            rows.append(row(m, d["translationMm"], d["rotationDeg"], d["reprojectionRmsPx"], d["diffToParkNumpy"]["dtNormMm"]))
            pts.append((m, d["translationMm"], "m"))
        rows.append('<tr class="sec"><td colspan="7">Verfeinerung (alle Posen)</td></tr>')
        f = j["finalSolution"]
        rows.append(row("verfeinert (soft_l1)", f["translationMm"], f["rotationDeg"], f["rmsPx"], norm(sub(f["translationMm"], ref))))
        pts.append(("verfeinert soft_l1", f["translationMm"], "r"))
        l = j["refinementConvergence"]["linearLossFromBest"]
        rows.append(row("verfeinert (linear)", l["translationMm"], l["rotationDeg"], l["rmsPx"], norm(sub(l["translationMm"], ref))))
        pts.append(("verfeinert linear", l["translationMm"], "r"))
        out.append(f'<h3>{short} · {kind} <small>({j["validPoseCount"]} Posen)</small></h3>')
        out.append(table(["Verfahren", "t x mm", "t y mm", "t z mm", "Rotation °", "RMS px", "Δ mm"], rows, "num"))
        panels.append((short, kind, pts))
    out.append('<h3>Translation x gegen y</h3>')
    out.append('<div class="legend"><span class="lg"><svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">'
               '<circle cx="10" cy="10" r="5" class="mk-m"/></svg>Verfahren (Startlösung)</span>'
               '<span class="lg"><svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">'
               '<rect x="3" y="3" width="14" height="14" class="mk-r"/></svg>verfeinert</span></div>')
    out.append('<div class="multi">')
    tabs = []
    for short, kind, pts in panels:
        xs = [p[1][0] for p in pts]
        ys = [p[1][1] for p in pts]
        W, H = 520, 340
        xl, xt, xst = axis_for(xs, 0.1)
        yl, yt, yst = axis_for(ys, 0.1)
        c = Chart(W, H, xl, yl, xt, yt, tf(xst), tf(yst), "t x (mm)", "t y (mm)", ml=58, mr=130, mb=44)
        lab = dodge([(i, c.sy(p[1][1])) for i, p in enumerate(pts)], 15, c.mt + 8, H - c.mb - 4)
        for i, (name, t, kp) in enumerate(pts):
            c.add(f'<line x1="{c.sx(t[0]):.1f}" y1="{c.sy(t[1]):.1f}" x2="{W - 124}" y2="{lab[i] - 4:.1f}" class="leader"/>')
        for i, (name, t, kp) in enumerate(pts):
            cx, cy = c.sx(t[0]), c.sy(t[1])
            tt = f"{short} {name}: x {de(t[0], 2)} mm, y {de(t[1], 2)} mm"
            if kp == "r":
                c.add(f'<rect x="{cx - R - 1:.1f}" y="{cy - R - 1:.1f}" width="{2 * R + 2}" height="{2 * R + 2}" class="mk-r"><title>{escape(tt)}</title></rect>')
            else:
                c.add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R - 1}" class="mk-m"><title>{escape(tt)}</title></circle>')
            c.text(W - 120, lab[i], name)
        out.append(f'<figure><figcaption>{short} · {kind}</figcaption>{c.svg(f"Translation x gegen y, {short}")}</figure>')
        tabs.append(table(["Sitzung", "Verfahren", "x mm", "y mm"],
                          [[short, escape(n), de(t[0], 3), de(t[1], 3)] for n, t, _ in pts], "num"))
    out.append("</div>")
    out.append(details("".join(tabs)))
    return "\n".join(out)


def reproj_section(sess):
    out = ['<h2 id="reproj">Daten: Reprojektionsfehler je Pose</h2>',
           '<p class="sub">RMS-Reprojektionsfehler je Pose (reprojRmsPx). Je Sitzung vier Ansichten mit '
           'gleicher y-Skala; die Posegruppen A–E haben feste Farbe und Form.</p>', legend_groups()]
    for sid, short, kind in SESSIONS:
        rows = sess[sid]["rows"]
        ymax = max(r["reproj"] for r in rows)
        ylim, yt, ystep = axis_for([0, ymax], 0.04, zero=True)
        yf = tf(ystep)
        yl = "Reprojektion RMS (px)"
        out.append(f'<h3>{short} · {kind} <small>({len(rows)} Posen)</small></h3><div class="multi">')
        W, H = 440, 290

        c = Chart(W, H, (-0.6, 4.6), ylim, [0, 1, 2, 3, 4], yt, lambda v: GROUPS[int(v)], yf, "Posegruppe", yl)
        for gi, g in enumerate(GROUPS):
            vals = [r["reproj"] for r in rows if r["g"] == g]
            if vals:
                m = median(vals)
                c.add(f'<line x1="{c.sx(gi - 0.38):.1f}" x2="{c.sx(gi + 0.38):.1f}" y1="{c.sy(m):.1f}" y2="{c.sy(m):.1f}" class="med"><title>Median Gruppe {g}: {de(m, 3)} px</title></line>')
        for i, r in enumerate(rows):
            jit = (((i * 37) % 11) - 5) / 5 * 0.28
            c.add(marker(r["g"], c.sx(GROUPS.index(r["g"]) + jit), c.sy(r["reproj"]),
                         f'{r["id"]}, Gruppe {r["g"]}: {de(r["reproj"], 3)} px'))
        out.append(f'<figure><figcaption>(a) nach Posegruppe (Linie = Median)</figcaption>{c.svg(f"{short}: Reprojektion nach Gruppe")}</figure>')

        def num_panel(key, xl_, unit, extra=None):
            xlim, xt, xstep = axis_for([r[key] for r in rows], 0.05)
            c = Chart(W, H, xlim, ylim, xt, yt, tf(xstep), yf, xl_, yl)
            if extra:
                extra(c)
            for r in rows:
                c.add(marker(r["g"], c.sx(r[key]), c.sy(r["reproj"]),
                             f'{r["id"]}, Gruppe {r["g"]}: {de(r["reproj"], 3)} px, {xl_.split(" (")[0]} = {de(r[key], 1)} {unit}'))
            return c

        c = num_panel("tilt", "Neigung (°)", "°")
        out.append(f'<figure><figcaption>(b) nach Neigung</figcaption>{c.svg(f"{short}: Reprojektion nach Neigung")}</figure>')
        c = num_panel("yaw", "Yaw (°)", "°")
        out.append(f'<figure><figcaption>(c) nach Yaw</figcaption>{c.svg(f"{short}: Reprojektion nach Yaw")}</figure>')

        tmid = (min(r["t"] for r in rows) + max(r["t"] for r in rows)) / 2
        worst = sorted(rows, key=lambda r: -r["reproj"])[:4]

        def tmid_line(c):
            x = c.sx(tmid)
            c.add(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{c.mt}" y2="{c.h - c.mb}" class="mid"><title>Zeitmitte {de(tmid, 0)} s</title></line>')
            c.add(f'<text x="{x + 4:.1f}" y="{c.mt + 11}" class="dl">Zeitmitte</text>')
        c = num_panel("t", "Aufnahmezeit (s)", "s", tmid_line)
        lab = dodge([(r["id"], c.sy(r["reproj"])) for r in worst], 14, c.mt + 22, c.h - c.mb - 6)
        for r in worst:
            x = c.sx(r["t"])
            left = x > c.w * 0.6
            c.text(x + (-11 if left else 11), lab[r["id"]] + 4, r["id"], "end" if left else "start")
        out.append(f'<figure><figcaption>(d) nach Aufnahmezeit (gestrichelt: Zeitmitte; beschriftet: 4 schlechteste Posen)</figcaption>{c.svg(f"{short}: Reprojektion nach Aufnahmezeit")}</figure>')
        out.append("</div>")
        hdr = ["Pose", "Gruppe", "t s", "Reproj. px", "PnP px", "Neigung °", "Yaw °", "Abstand mm", "Brettabw. mm", "LOO Δt mm"]
        trs = [[r["id"], r["g"], de(r["t"], 1), de(r["reproj"], 3), de(r["pnp"], 3), de(r["tilt"], 1), de(r["yaw"], 1),
                de(r["dist"], 0), de(r["dev"], 2), de(sess[sid]["loo"].get(r["id"]), 3)] for r in rows]
        out.append(details(table(hdr, trs, "num", f"{short} · {kind}: Posen")))
    return "\n".join(out)


SERIES = [("rigid", "starr verfeinert", "s-rigid", ""), ("drift", "Brettdrift-Modell", "s-drift", "7 4"),
          ("park", "Park roh", "s-park", "1.5 4")]


def influence_section(sess, vg, dr):
    out = ['<h2 id="einfluss">Daten: Einfluss auf die Translation</h2>', '<h3>(a) Leave-one-out</h3>',
           '<p class="sub">Änderung der verfeinerten Translation (Betrag Δt), wenn die jeweilige Pose weggelassen wird; '
           'Posen nach Aufnahmezeit sortiert.</p>', legend_groups()]
    for sid, short, kind in SESSIONS:
        rows = sorted(sess[sid]["rows"], key=lambda r: r["t"])
        vals = [sess[sid]["loo"][r["id"]] for r in rows]
        ylim, yt, ystep = axis_for([0, max(vals)], 0.04, zero=True)
        W, H = 600, 280
        n = len(rows)
        c = Chart(W, H, (0, n), ylim, [], yt, lambda v: "", tf(ystep), "Pose, nach Aufnahmezeit sortiert (Zeit in s)", "Δt-Norm (mm)")
        for i in range(0, n, max(1, n // 6)):
            c.add(f'<text x="{c.sx(i + 0.5):.1f}" y="{H - c.mb + 16}" text-anchor="middle" class="tk">{de(rows[i]["t"], 0)}</text>')
        bw = (W - c.ml - c.mr) / n
        for i, (r, v) in enumerate(zip(rows, vals)):
            y = c.sy(v)
            c.add(f'<rect x="{c.sx(i) + 0.5:.1f}" y="{y:.1f}" width="{max(bw - 1.5, 1):.1f}" height="{c.sy(0) - y:.1f}" '
                  f'style="fill:var(--g-{r["g"].lower()})"><title>{escape(r["id"])}, Gruppe {r["g"]}: Δt {de(v, 3)} mm, t = {de(r["t"], 1)} s</title></rect>')
        out.append(f'<h3>{short} · {kind}</h3><figure>{c.svg(f"{short}: Leave-one-out")}</figure>')
        trs = [[r["id"], r["g"], de(r["t"], 1), de(v, 3)] for r, v in zip(rows, vals)]
        out.append(details(table(["Pose", "Gruppe", "t s", "Δt mm"], trs, "num")))

    out.append('<h3>(b) Posenzahl-Sweep</h3>')
    out.append('<p class="sub">Translation gegen Anzahl der verwendeten Posen. Punkte = einzelne Teilmengen, '
               'Linie = Mittelwert je Anzahl. „Park roh“ = Startlösung ohne Verfeinerung.</p>')
    out.append('<div class="legend">' + "".join(
        f'<span class="lg"><svg width="34" height="20" viewBox="0 0 34 20" aria-hidden="true"><line x1="2" x2="32" y1="10" y2="10" '
        f'style="stroke:var(--{cl})" stroke-width="2.5" stroke-dasharray="{da}" stroke-linecap="round"/></svg>{lb}</span>'
        for _, lb, cl, da in SERIES) + "</div>")
    for sid, short, kind in SESSIONS:
        sw, ds = vg[sid]["sweep"], dr[sid]["sweep"]
        assert len(sw) == len(ds)
        data = {"rigid": [], "drift": [], "park": []}
        for a, b in zip(sw, ds):
            assert a["label"].startswith(str(b["poseCount"])) or a["label"] == "alle"
            data["rigid"].append((b["poseCount"], b["rigid"]["translationMm"], a["label"]))
            data["drift"].append((b["poseCount"], b["boardDrift"]["translationMm"], a["label"]))
            data["park"].append((b["poseCount"], a["park"], a["label"]))
        ns = sorted({p[0] for p in data["rigid"]})
        out.append(f'<h3>{short} · {kind}</h3><div class="multi">')
        tabrows = []
        for ai, axn in enumerate(("x", "y")):
            allv = [t[ai] for k in data for _, t, _ in data[k]]
            ylim, yt, ystep = axis_for(allv, 0.08)
            W, H = 440, 300
            c = Chart(W, H, (ns[0] - 3, ns[-1] + 3), ylim, ns, yt, lambda v: str(int(v)), tf(ystep), "Posenzahl", f"t {axn} (mm)", mr=110)
            ends = []
            for k, lb, cl, da in SERIES:
                means = []
                for n in ns:
                    vs = [t[ai] for nn, t, _ in data[k] if nn == n]
                    means.append((n, sum(vs) / len(vs)))
                pts = " ".join(f"{c.sx(n):.1f},{c.sy(v):.1f}" for n, v in means)
                c.add(f'<polyline points="{pts}" fill="none" style="stroke:var(--{cl})" stroke-width="2.5" '
                      f'stroke-dasharray="{da}" stroke-linecap="round" stroke-linejoin="round"/>')
                for nn, t, lab_ in data[k]:
                    c.add(f'<circle cx="{c.sx(nn):.1f}" cy="{c.sy(t[ai]):.1f}" r="3.5" style="fill:var(--{cl})" stroke="var(--surface)" stroke-width="1.5">'
                          f'<title>{escape(lb)} ({escape(lab_)}), n = {nn}: t {axn} = {de(t[ai], 2)} mm</title></circle>')
                    if ai == 0:
                        tabrows.append([escape(lb), escape(lab_), str(nn), de(t[0], 2), de(t[1], 2)])
                ends.append((k, c.sy(means[-1][1]), lb))
            lab = dodge([(k, y) for k, y, _ in ends], 15, c.mt + 8, H - c.mb - 4)
            for k, y, lb in ends:
                c.text(c.w - c.mr + 8, lab[k] + 4, lb)
            out.append(f'<figure><figcaption>t {axn} gegen Posenzahl</figcaption>{c.svg(f"{short}: t {axn} gegen Posenzahl")}</figure>')
        out.append("</div>")
        out.append(details(table(["Serie", "Teilmenge", "Posen", "t x mm", "t y mm"], tabrows, "num")))

    out.append('<h3>(c) Driftmodelle</h3>')
    out.append('<p class="sub">rigid = feste Brett- und Hand-Auge-Lage; boardDrift = Brett wandert linear mit der Zeit; '
               'mountDrift = Kamerahalterung wandert linear. Drift über die gesamte Sitzung.</p>')
    for sid, short, kind in SESSIONS:
        d = dr[sid]
        trs = []
        for key in ("rigid", "boardDrift", "mountDrift"):
            m = d["models"][key]
            t = m["translationMm"]
            dm = f'{de(m["driftOverSessionNormMm"], 2)} mm / {de(m["driftOverSessionDeg"], 2)} °' if "driftOverSessionNormMm" in m else "–"
            trs.append([key, de(m["reprojectionRmsPx"], 3), de(m["rmsFirstHalfPx"], 3), de(m["rmsSecondHalfPx"], 3),
                        f'{de(t[0], 1)} | {de(t[1], 1)} | {de(t[2], 1)}', dm])
        out.append(f'<h3>{short} · {kind} <small>({d["poseCount"]} Posen, {de(d["sessionSpanS"], 0)} s)</small></h3>')
        out.append(table(["Modell", "RMS gesamt px", "1. Hälfte px", "2. Hälfte px", "t x | y | z mm", "Drift"], trs, "num"))
    return "\n".join(out)


def repro_section(sess):
    env = sess[S1]["j"]["environment"]
    code = (
        "python3 -m venv .venv-cv4\n"
        ".venv-cv4/bin/pip install -r tools/handeye_offline_cv4/requirements.txt\n"
        "PYTHONPATH=src .venv-cv4/bin/python tools/handeye_offline_cv4/analyse.py\n"
        "PYTHONPATH=src .venv-cv4/bin/python tools/handeye_offline_cv4/drift.py\n"
        "python3 tools/handeye_offline_cv4/report.py   # nur Standardbibliothek")
    return (f'<h2 id="repro">Reproduzierbarkeit</h2><pre><code>{escape(code)}</code></pre>'
            f'<p>Versionen aus den Ergebnisdateien: OpenCV {escape(env["cv2"])}, NumPy {escape(env["numpy"])}, SciPy {escape(env["scipy"])}. '
            'Die Rohdaten der Sitzungen werden nur gelesen; die Analyse arbeitet auf einer Kopie in einem temporären Verzeichnis (tempfile). '
            'Es wird nichts aktiviert und keine Roboterbewegung ausgelöst.</p>'
            f'<p class="sub">Sitzungen: <code>{S1}</code> (ChArUco), <code>{S2}</code> (Schachbrett).</p>')


DARK = ("--surface:#1a1a19;--text:#f0efec;--text-2:#c3c2b7;--grid:#383835;"
        "--g-a:#3987e5;--g-b:#d95926;--g-c:#199e70;--g-d:#c98500;--g-e:#d55181;"
        "--s-rigid:#c3c2b7;--s-drift:#9085e9;--s-park:#008300;--card:#232321")

CSS = """
:root{--surface:#fcfcfb;--text:#0b0b0b;--text-2:#52514e;--grid:#e4e3df;
--g-a:#2a78d6;--g-b:#eb6834;--g-c:#1baf7a;--g-d:#eda100;--g-e:#e87ba4;
--s-rigid:#52514e;--s-drift:#6250d6;--s-park:#008300;--card:#f4f4f1}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){%DARK%}}
:root[data-theme="dark"]{%DARK%}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--text);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.6rem;margin:.2em 0}h2{font-size:1.25rem;margin:2.2em 0 .5em;border-bottom:1px solid var(--grid);padding-bottom:4px}
h3{font-size:1.02rem;margin:1.6em 0 .4em}h3 small{color:var(--text-2);font-weight:400}
.sub{color:var(--text-2)}code{font-size:.88em;overflow-wrap:anywhere}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr));gap:10px;margin:16px 0}
.kpi{background:var(--card);border:1px solid var(--grid);border-radius:8px;padding:12px 14px}
.kpi b{display:block;font-size:1.45rem;line-height:1.2;overflow-wrap:anywhere}.kpi span,.kpi em{display:block;color:var(--text-2);font-size:.85rem;font-style:normal}
.scroll{overflow-x:auto;margin:8px 0}
table{border-collapse:collapse;width:100%;font-size:.86rem}caption{text-align:left;color:var(--text-2);padding:4px 0}
th,td{padding:4px 8px;border-bottom:1px solid var(--grid);text-align:left;white-space:nowrap}
table.num td:not(:first-child),table.num th:not(:first-child){text-align:right;font-variant-numeric:tabular-nums}
tr.sec td{color:var(--text-2);font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;background:var(--card)}
details{margin:8px 0}summary{cursor:pointer;color:var(--text-2)}
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;margin:10px 0 4px}.lg{display:inline-flex;align-items:center;gap:6px;font-size:.85rem}
.multi{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:12px}
figure{margin:0;min-width:0}figcaption{font-size:.88rem;color:var(--text-2);margin:4px 0}
svg{display:block;max-width:100%;height:auto}
.gl{stroke:var(--grid);stroke-width:1}.ax{stroke:var(--text-2);stroke-width:1;opacity:.6}
.tk{fill:var(--text-2);font-size:11.5px}.al{fill:var(--text-2);font-size:12.5px}
.dl{fill:var(--text-2);font-size:12px;paint-order:stroke;stroke:var(--surface);stroke-width:3px;stroke-linejoin:round}
.med{stroke:var(--text);stroke-width:2.5;stroke-linecap:round}.mid{stroke:var(--text-2);stroke-width:1.5;stroke-dasharray:5 4}
.leader{stroke:var(--text-2);stroke-width:1;opacity:.5}
.mk-m{fill:var(--text-2);stroke:var(--surface);stroke-width:2}.mk-r{fill:var(--s-drift);stroke:var(--surface);stroke-width:2}
pre{background:var(--card);border:1px solid var(--grid);padding:10px;overflow-x:auto;font-size:.82rem;border-radius:6px}
.befunde{margin:20px 0}
""".replace("%DARK%", DARK)


def main():
    sess, vg, dr, befunde = load()
    head = ('<h1>Hand-Auge OpenCV-Vergleich</h1>'
            f'<p class="sub">Sitzungen S1 <code>{S1}</code> (ChArUco) und S2 <code>{S2}</code> (Schachbrett) · Branch '
            '<code>handauge-offline-cv4</code> · offline ausgewertet, keine Roboterbewegung, nichts aktiviert · '
            'OpenCV 4.14 nur in separater Umgebung.</p>')
    html = ('<!doctype html><html lang="de"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Hand-Auge OpenCV-Vergleich</title><style>' + CSS + '</style></head><body><main>'
            + head + kpis(sess)
            + '<section class="befunde" id="befunde">' + befunde + '</section>'
            + method_section(sess) + reproj_section(sess) + influence_section(sess, vg, dr) + repro_section(sess)
            + '</main></body></html>')
    OUT.write_text(html, encoding="utf-8")
    print(f"geschrieben: {OUT} ({len(html.encode('utf-8')) / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()

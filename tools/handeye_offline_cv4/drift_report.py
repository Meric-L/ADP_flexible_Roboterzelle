#!/usr/bin/env python3
"""Erzeugt doc/auswertung/handeye_drift.html aus den Offline-Auswertungen.

Nur Python-Standardbibliothek. Liest doc/auswertung/handeye_cv4/ (nur lesen).
Aufruf: python3 tools/handeye_offline_cv4/drift_report.py
"""
import csv
import json
import math
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "docs" / "auswertung" / "handeye_cv4"
OUT = ROOT / "docs" / "auswertung" / "handeye_drift.html"

SESSIONS = [
    {"id": "20261109T125050Z_a5c964f0", "short": "S1", "name": "Sitzung 1", "board": "ChArUco"},
    {"id": "20261109T125825Z_a580eaba", "short": "S2", "name": "Sitzung 2", "board": "Schachbrett"},
]
GROUPS = "ABCDE"  # "F" kommt in load() dazu, falls eine Sitzung die Gruppe enthaelt
MINUS = "−"


# ---------------------------------------------------------------- Zahlen
def de(x, d=1, sign=False):
    s = f"{abs(x):.{d}f}".replace(".", ",")
    if x < 0 and float(f"{abs(x):.{d}f}") != 0:
        return MINUS + s
    return ("+" + s) if sign and float(f"{abs(x):.{d}f}") != 0 else s


def regress(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return slope, my - slope * mx, sxy / math.sqrt(sxx * syy)


def nice_ticks(lo, hi, n=5):
    span = hi - lo
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    a = math.floor(lo / step + 1e-9) * step
    b = math.ceil(hi / step - 1e-9) * step
    ticks = []
    v = a
    while v <= b + step * 1e-6:
        ticks.append(round(v, 10))
        v += step
    return ticks, step


def decimals(step):
    for d in range(0, 4):
        if abs(step * 10 ** d - round(step * 10 ** d)) < 1e-6:
            return d
    return 3


# ---------------------------------------------------------------- Daten
def load():
    drift = json.loads((DATA / "drift.json").read_text())
    by = {s["session"]: s for s in drift}
    out = []
    for meta in SESSIONS:
        rows = []
        with open(DATA / f"{meta['id']}_posen.csv", newline="") as f:
            for r in csv.DictReader(f):
                rows.append(r)
        out.append({**meta, "rows": rows, "drift": by[meta["id"]]})
    global GROUPS
    if any(r["poseGroup"] == "F" for s in out for r in s["rows"]) and "F" not in GROUPS:
        GROUPS += "F"
    return out


def col(rows, key):
    return [float(r[key]) for r in rows]


# ---------------------------------------------------------------- SVG-Bausteine
W, H = 460, 290
ML, MR, MT, MB = 50, 14, 30, 40
PW, PH = W - ML - MR, H - MT - MB


def shape(g, cx, cy, title, s=5.5):
    """Gruppenmarker: feste Form und Farbe je Gruppe."""
    t = f"<title>{escape(title)}</title>"
    if g == "A":
        return f'<circle class="g g-A" cx="{cx:.1f}" cy="{cy:.1f}" r="{s}">{t}</circle>'
    if g == "B":
        k = s * 0.9
        return f'<rect class="g g-B" x="{cx-k:.1f}" y="{cy-k:.1f}" width="{2*k:.1f}" height="{2*k:.1f}">{t}</rect>'
    if g == "C":
        pts = f"{cx:.1f},{cy-s*1.15:.1f} {cx+s*1.1:.1f},{cy+s*0.85:.1f} {cx-s*1.1:.1f},{cy+s*0.85:.1f}"
        return f'<polygon class="g g-C" points="{pts}">{t}</polygon>'
    if g == "D":
        k = s * 1.25
        pts = f"{cx:.1f},{cy-k:.1f} {cx+k:.1f},{cy:.1f} {cx:.1f},{cy+k:.1f} {cx-k:.1f},{cy:.1f}"
        return f'<polygon class="g g-D" points="{pts}">{t}</polygon>'
    if g == "F":  # diagonales Kreuz
        d = f"M{cx-s:.1f} {cy-s:.1f}L{cx+s:.1f} {cy+s:.1f}M{cx-s:.1f} {cy+s:.1f}L{cx+s:.1f} {cy-s:.1f}"
        return (f'<g class="g-plus"><path class="plus-ring" d="{d}"/>'
                f'<path class="plus g-F-s" d="{d}"/>{t}</g>')
    d = f"M{cx-s:.1f} {cy:.1f}H{cx+s:.1f}M{cx:.1f} {cy-s:.1f}V{cy+s:.1f}"
    return (f'<g class="g-plus"><path class="plus-ring" d="{d}"/>'
            f'<path class="plus g-E-s" d="{d}"/>{t}</g>')


def legend_group():
    items = []
    for g in GROUPS:
        sv = f'<svg class="lg" viewBox="0 0 18 18" width="18" height="18" aria-hidden="true">{shape(g, 9, 9, "", 5)}</svg>'
        items.append(f'<span class="li">{sv}Gruppe {g}</span>')
    items.append('<span class="li"><svg class="lg" viewBox="0 0 26 18" width="26" height="18" aria-hidden="true">'
                 '<line class="trend" x1="2" y1="9" x2="24" y2="9"/></svg>Lineare Regression</span>')
    return '<div class="legend">' + "".join(items) + "</div>"


class Panel:
    def __init__(self, xlim, ylim, xlabel, ylabel, xticks=None, ytick_fmt=None):
        self.xlim, self.ylim = xlim, ylim
        self.xt, self.xstep = (xticks, None) if xticks else nice_ticks(*xlim)
        self.yt, self.ystep = nice_ticks(*ylim)
        self.xlim = (min(self.xlim[0], self.xt[0]) if not xticks else xlim[0],
                     max(self.xlim[1], self.xt[-1]) if not xticks else xlim[1])
        self.ylim = (self.yt[0], self.yt[-1])
        self.parts = []
        self.xlabel, self.ylabel = xlabel, ylabel
        self.yd = decimals(self.ystep)

    def X(self, v):
        return ML + (v - self.xlim[0]) / (self.xlim[1] - self.xlim[0]) * PW

    def Y(self, v):
        return MT + PH - (v - self.ylim[0]) / (self.ylim[1] - self.ylim[0]) * PH

    def add(self, s):
        self.parts.append(s)

    def render(self, label):
        o = [f'<svg class="plot" viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="{escape(label)}">']
        for v in self.yt:
            y = self.Y(v)
            cls = "zero" if abs(v) < 1e-9 else "gl"
            o.append(f'<line class="{cls}" x1="{ML}" x2="{W-MR}" y1="{y:.1f}" y2="{y:.1f}"/>')
            o.append(f'<text class="tk" x="{ML-6}" y="{y+4:.1f}" text-anchor="end">{de(v, self.yd)}</text>')
        for v in self.xt:
            x = self.X(v)
            o.append(f'<line class="gl" x1="{x:.1f}" x2="{x:.1f}" y1="{MT}" y2="{MT+PH}"/>')
            o.append(f'<text class="tk" x="{x:.1f}" y="{MT+PH+16}" text-anchor="middle">{de(v, 0)}</text>')
        o.append(f'<text class="ax" x="{ML}" y="14">{escape(self.ylabel)}</text>')
        o.append(f'<text class="ax" x="{ML+PW/2:.0f}" y="{H-6}" text-anchor="middle">{escape(self.xlabel)}</text>')
        o.extend(self.parts)
        o.append("</svg>")
        return "".join(o)


def halo_text(x, y, s, anchor="end", cls="dl"):
    return f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}">{escape(s)}</text>'


def trend_label(slope, span, r, unit, axis):
    return f"{de(slope * span, 1, True)} {unit} über die Sitzung · r = {de(r, 2)}".replace("+", "+")


def ylim_of(vals, pad=0.08, include_zero=False):
    lo, hi = min(vals), max(vals)
    if include_zero:
        lo, hi = min(lo, 0), max(hi, 0)
    p = (hi - lo) * pad or 1
    return lo - p, hi + p


# ---------------------------------------------------------------- Diagramme
def scatter_panel(sess, key, ylim, unit, axis_name, tmax):
    rows = sess["rows"]
    ts, vs = col(rows, "tSeconds"), col(rows, key)
    slope, icpt, r = regress(ts, vs)
    p = Panel((0, tmax), ylim, "Aufnahmezeit (s)", f"{axis_name} ({unit})", xticks=list(range(0, 401, 100)))
    p.xlim = (0, 420)
    for row, t, v in zip(rows, ts, vs):
        p.add(shape(row["poseGroup"], p.X(t), p.Y(v),
                    f"{row['poseId']}, Gruppe {row['poseGroup']}, {de(t, 0)} s, {axis_name} = {de(v, 2)} {unit}"))
    t0, t1 = 0, ts[-1]
    x1, y1 = p.X(t1), p.Y(slope * t1 + icpt)
    p.add(f'<line class="trend" x1="{p.X(t0):.1f}" y1="{p.Y(slope*t0+icpt):.1f}" x2="{x1:.1f}" y2="{y1:.1f}"/>')
    span = sess["drift"]["sessionSpanS"]
    txt = trend_label(slope, span, r, unit, axis_name)
    p.add(f'<line class="trend" x1="{ML+4}" y1="{MT+9}" x2="{ML+22}" y2="{MT+9}"/>')
    p.add(halo_text(ML + 28, MT + 13, txt, "start"))
    table = [(row["poseId"], row["poseGroup"], t, v) for row, t, v in zip(rows, ts, vs)]
    return p.render(f"{sess['name']}: {axis_name} gegen Aufnahmezeit"), table, (slope, slope * span, r)


def table_html(head, rows, caption=""):
    h = "".join(f"<th>{escape(x)}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


def details(inner):
    return f"<details><summary>Werte als Tabelle</summary>{inner}</details>"


def panel_title(sess, extra=""):
    return f'<h4>{sess["name"]} · {sess["board"]}{extra}</h4>'


def multiples(sessions, specs, tmax, stats):
    """specs: [(key, axis_name, unit)] -> Zeilen = Achse, Spalten = Sitzung."""
    cells, tab_rows = [], {}
    for key, name, unit in specs:
        allv = []
        for s in sessions:
            allv += col(s["rows"], key)
        # y-Skala: Daten plus Regressionsgeraden
        lo, hi = ylim_of(allv, 0.12)
        hi += (hi - lo) * 0.14  # Kopfraum fuer die Beschriftung der Geraden
        for si, s in enumerate(sessions):
            svg, table, st = scatter_panel(s, key, (lo, hi), unit, name, tmax)
            stats[(key, si)] = st
            cells.append(f'<div class="cell">{panel_title(s, " · " + name)}{svg}</div>')
            for pid, g, t, v in table:
                tab_rows.setdefault((s["short"], pid, g, t), {})[key] = v
    head = ["Sitzung", "Pose", "Gruppe", "Zeit (s)"] + [f"{n} ({u})" for _, n, u in specs]
    rows = [[k[0], k[1], k[2], de(k[3], 1)] + [de(v[key], 2) for key, _, _ in specs] for k, v in tab_rows.items()]
    return '<div class="grid">' + "".join(cells) + "</div>", details(table_html(head, rows))


def chart4(sessions, stats4):
    allv = []
    for s in sessions:
        m = s["drift"]["models"]
        allv += list(m["rigid"]["perPoseRmsPx"].values()) + list(m["boardDrift"]["perPoseRmsPx"].values())
    lo, hi = 0, max(allv) * 1.06
    tmax = max(max(col(s["rows"], "tSeconds")) for s in sessions)
    cells, trows = [], []
    for s in sessions:
        m = s["drift"]["models"]
        rg, bd = m["rigid"]["perPoseRmsPx"], m["boardDrift"]["perPoseRmsPx"]
        p = Panel((0, tmax), (lo, hi), "Aufnahmezeit (s)", "Reprojektions-RMS je Pose (px)", xticks=list(range(0, 401, 100)))
        p.xlim = (0, 420)
        better = 0
        pts = []
        for row in s["rows"]:
            pid, t = row["poseId"], float(row["tSeconds"])
            a, b = rg[pid], bd[pid]
            better += b < a
            pts.append((pid, row["poseGroup"], t, a, b))
            p.add(f'<line class="link" x1="{p.X(t):.1f}" x2="{p.X(t):.1f}" y1="{p.Y(a):.1f}" y2="{p.Y(b):.1f}"/>')
        for pid, g, t, a, b in pts:
            p.add(f'<circle class="sr" cx="{p.X(t):.1f}" cy="{p.Y(a):.1f}" r="4"><title>{pid}, Gruppe {g}, {de(t,0)} s, starr: {de(a,2)} px</title></circle>')
            p.add(f'<circle class="sd" cx="{p.X(t):.1f}" cy="{p.Y(b):.1f}" r="3.6"><title>{pid}, Gruppe {g}, {de(t,0)} s, Brettdrift: {de(b,2)} px</title></circle>')
        # Direktbeschriftung: Serien am Plotrand links oben mit Marker
        lx, ly = ML + 10, MT + 12
        p.add(f'<circle class="sr" cx="{lx}" cy="{ly}" r="4"/>' + halo_text(lx + 9, ly + 4, "starr (rigid)", "start"))
        p.add(f'<circle class="sd" cx="{lx}" cy="{ly+18}" r="3.6"/>' + halo_text(lx + 9, ly + 22, "Brettdrift", "start"))
        N = len(s["rows"])
        stats4.append((s, better, N))
        cells.append(f'<div class="cell">{panel_title(s)}{p.render(s["name"] + ": Fehler je Pose")}</div>')
        trows += [[s["short"], pid, g, de(t, 1), de(a, 2), de(b, 2)] for pid, g, t, a, b in pts]
    return ('<div class="grid">' + "".join(cells) + "</div>",
            details(table_html(["Sitzung", "Pose", "Gruppe", "Zeit (s)", "starr (px)", "Brettdrift (px)"], trows)))


def chart5(sessions, info):
    cells, trows = [], []
    for ai, (axname, idx) in enumerate((("t x", 0), ("t y", 1))):
        allv = []
        for s in sessions:
            for e in s["drift"]["sweep"]:
                allv += [e["rigid"]["translationMm"][idx], e["boardDrift"]["translationMm"][idx]]
        lo, hi = ylim_of(allv, 0.2)
        for s in sessions:
            sw = s["drift"]["sweep"]
            counts = sorted({e["poseCount"] for e in sw})
            xmax = counts[-1]
            p = Panel((8, xmax + 6), (lo, hi), "Posenzahl der Teilmenge", f"{axname} (mm)", xticks=[c for c in counts])
            p.xlim = (8, xmax + 6)
            means = {"rigid": [], "boardDrift": []}
            for c in counts:
                sub = [e for e in sw if e["poseCount"] == c]
                for k, dx in (("rigid", -3), ("boardDrift", 3)):
                    vals = [e[k]["translationMm"][idx] for e in sub]
                    means[k].append(sum(vals) / len(vals))
                    cls = "sr" if k == "rigid" else "sd"
                    for v in vals:
                        p.add(f'<circle class="{cls} small" cx="{p.X(c)+dx:.1f}" cy="{p.Y(v):.1f}" r="3.2"><title>{c} Posen, {"starr" if k=="rigid" else "Brettdrift"}, {axname} = {de(v,2)} mm</title></circle>')
            for k, cls in (("rigid", "ln-r"), ("boardDrift", "ln-d")):
                pts = " ".join(f"{p.X(c):.1f},{p.Y(m):.1f}" for c, m in zip(counts, means[k]))
                p.add(f'<polyline class="{cls}" points="{pts}"/>')
            ex, er, ed = p.X(counts[-1]), means["rigid"][-1], means["boardDrift"][-1]
            up_r = er >= ed
            p.add(halo_text(ex + 6, p.Y(er) + (-9 if up_r else 17), "starr"))
            p.add(halo_text(ex + 6, p.Y(ed) + (17 if up_r else -9), "Brettdrift"))
            cells.append(f'<div class="cell">{panel_title(s, " · " + axname)}{p.render(s["name"] + ": " + axname + " über Posenzahl")}</div>')
            rng = {k: max(e[k]["translationMm"][idx] for e in sw) - min(e[k]["translationMm"][idx] for e in sw)
                   for k in ("rigid", "boardDrift")}
            info[(s["short"], axname)] = rng
            for i, e in enumerate(sw):
                trows.append([s["short"], axname, e["poseCount"], i + 1,
                              de(e["rigid"]["translationMm"][idx], 2), de(e["boardDrift"]["translationMm"][idx], 2)])
    return ('<div class="grid">' + "".join(cells) + "</div>",
            details(table_html(["Sitzung", "Achse", "Posenzahl", "Teilmenge", "starr (mm)", "Brettdrift (mm)"], trows)))


def chart6(sessions):
    vals = [s["drift"]["models"]["boardDrift"]["driftOverSessionMm"] for s in sessions]
    lo, hi = ylim_of([v for vv in vals for v in vv], 0.25, include_zero=True)
    p = Panel((0, 3), (lo, hi), "", "Drift über die Sitzung (mm)", xticks=[])
    p.xlim = (0, 3)
    p.xt = []
    p.add('<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
          '<rect width="6" height="6" class="hbg"/><line x1="0" y1="0" x2="0" y2="6" class="hl" /></pattern></defs>')
    names = ["x", "y", "z"]
    bw = 0.32
    zero = p.Y(0)
    for ai, nm in enumerate(names):
        for si in (0, 1):
            v = vals[si][ai]
            cx = ai + 0.5 + (si - 0.5) * (bw + 0.04)
            x0, x1 = p.X(cx - bw / 2), p.X(cx + bw / 2)
            y = p.Y(v)
            cls = "bar1" if si == 0 else "bar2"
            p.add(f'<rect class="{cls}" x="{x0:.1f}" y="{min(y, zero):.1f}" width="{x1-x0:.1f}" height="{abs(y-zero):.1f}">'
                  f'<title>{sessions[si]["short"]}, {nm}: {de(v,2)} mm</title></rect>')
            ty = y - 6 if v >= 0 else y + 14
            p.add(halo_text((x0 + x1) / 2, ty, f"{sessions[si]['short']}: {de(v,1)}", "middle", "dl"))
        p.add(f'<text class="ax" x="{p.X(ai+0.5):.1f}" y="{MT+PH+18}" text-anchor="middle">{nm}</text>')
    svg = p.render("Drift der Brettlage je Achse")
    legend = ('<div class="legend"><span class="li"><svg class="lg" viewBox="0 0 20 14" width="20" height="14" aria-hidden="true"><rect class="bar1" x="1" y="1" width="18" height="12"/></svg>Sitzung 1</span>'
              '<span class="li"><svg class="lg" viewBox="0 0 20 14" width="20" height="14" aria-hidden="true"><defs><pattern id="hatch2" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" class="hbg"/><line x1="0" y1="0" x2="0" y2="6" class="hl"/></pattern></defs><rect class="bar2" x="1" y="1" width="18" height="12" style="fill:url(#hatch2)"/></svg>Sitzung 2 (schraffiert)</span></div>')
    trows = [[nm, de(vals[0][i], 2), de(vals[1][i], 2)] for i, nm in enumerate(names)]
    return legend, f'<div class="one">{svg}</div>', details(table_html(["Achse", "S1 (mm)", "S2 (mm)"], trows))


def models_table(sessions):
    head = ["Sitzung", "Modell", "RMS gesamt (px)", "1. Hälfte (px)", "2. Hälfte (px)",
            "t x (mm)", "t y (mm)", "t z (mm)", "Drift (mm)", "Drift (°)"]
    rows = []
    for s in sessions:
        for name in ("rigid", "boardDrift", "mountDrift"):
            m = s["drift"]["models"][name]
            t = m["translationMm"]
            dm = de(m["driftOverSessionNormMm"], 1) if "driftOverSessionNormMm" in m else "–"
            dd = de(m["driftOverSessionDeg"], 2) if "driftOverSessionDeg" in m else "–"
            rows.append([s["short"], name, de(m["reprojectionRmsPx"], 2), de(m["rmsFirstHalfPx"], 2),
                         de(m["rmsSecondHalfPx"], 2), de(t[0], 1), de(t[1], 1), de(t[2], 1), dm, dd])
    return table_html(head, rows)


# ---------------------------------------------------------------- Seite
CSS = """
:root{--bg:#f7f8f8;--surface:#ffffff;--fg:#15191c;--muted:#56616a;--grid:#e2e6e9;--accent:#1d5f8c;
--gA:#2a78d6;--gB:#eb6834;--gC:#1baf7a;--gD:#eda100;--gE:#e87ba4;--gF:#7a7a2e;--m-rigid:#52514e;--m-drift:#6250d6}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#141617;--surface:#1c1f21;--fg:#e8ecee;--muted:#a3aeb6;--grid:#2c3236;--accent:#6aaee0;
--gA:#3987e5;--gB:#d95926;--gC:#199e70;--gD:#c98500;--gE:#d55181;--gF:#a8a83c;--m-rigid:#c3c2b7;--m-drift:#9085e9;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#141617;--surface:#1c1f21;--fg:#e8ecee;--muted:#a3aeb6;--grid:#2c3236;--accent:#6aaee0;
--gA:#3987e5;--gB:#d95926;--gC:#199e70;--gD:#c98500;--gE:#d55181;--gF:#a8a83c;--m-rigid:#c3c2b7;--m-drift:#9085e9;color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);margin:0;font:16px/1.55 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1032px;margin:0 auto;padding-block:28px 56px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
h1,h2,h3,h4{font-family:"IBM Plex Sans Condensed","Arial Narrow",system-ui,sans-serif;text-wrap:balance;line-height:1.2;margin:0}
h1{font-size:clamp(1.7rem,5vw,2.4rem);font-weight:600}
h2{font-size:1.4rem;font-weight:600;margin:2.6rem 0 .6rem;padding-top:1.2rem;border-top:1px solid var(--grid)}
h4{font-size:.95rem;font-weight:500;color:var(--muted);margin:0 0 .2rem}
.sub{color:var(--muted);margin:.4rem 0 0;max-width:70ch}
p,ul,ol{max-width:70ch;margin:.7rem 0}
li{margin:.35rem 0}
code,.num,td,th,.kz b{font-family:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace}
code{font-size:.88em;overflow-wrap:anywhere}
.num,td,th,.kz,text{font-variant-numeric:tabular-nums}
.kz{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:10px;margin:1.4rem 0 0}
.kz div{background:var(--surface);border-radius:6px;padding:.7rem .9rem;font-size:.88rem;color:var(--muted);line-height:1.35}
.kz b{display:block;color:var(--fg);font-size:1.3rem;font-weight:500;margin-bottom:.15rem}
.legend{display:flex;flex-wrap:wrap;gap:.3rem 1.1rem;font-size:.85rem;color:var(--muted);margin:.8rem 0 .4rem}
.li{display:inline-flex;align-items:center;gap:.3rem}
.lg{display:block;flex:none}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:.9rem 1.4rem;max-width:1000px}
.cell{min-width:0}
.one{max-width:640px}
.plot{display:block;height:auto;font-family:"IBM Plex Sans",system-ui,sans-serif}
.plot text{font-size:12px;fill:var(--muted)}
.plot .tk{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:11px}
.plot .ax{fill:var(--muted)}
.plot .dl{fill:var(--fg);stroke:var(--bg);stroke-width:3.5px;paint-order:stroke;stroke-linejoin:round}
.gl{stroke:var(--grid);stroke-width:1}
.zero{stroke:var(--muted);stroke-width:1}
.trend{stroke:var(--accent);stroke-width:2;fill:none}
.g{stroke:var(--bg);stroke-width:1.5}
.g-A{fill:var(--gA)}.g-B{fill:var(--gB)}.g-C{fill:var(--gC)}.g-D{fill:var(--gD)}
.plus{stroke:var(--gE);stroke-width:3;fill:none;stroke-linecap:butt}
.plus-ring{stroke:var(--bg);stroke-width:6;fill:none}
.plus.g-F-s{stroke:var(--gF)}
.link{stroke:var(--grid);stroke-width:1.2}
.sr{fill:var(--m-rigid);stroke:var(--bg);stroke-width:1}
.sd{fill:none;stroke:var(--m-drift);stroke-width:1.8}
.small.sd{fill:var(--bg)}
.ln-r{stroke:var(--m-rigid);stroke-width:2;fill:none}
.ln-d{stroke:var(--m-drift);stroke-width:2;fill:none;stroke-dasharray:6 4}
.bar1{fill:var(--accent)}
.bar2{fill:url(#hatch);stroke:var(--muted);stroke-width:1.2}
.hbg{fill:var(--bg)}.hl{stroke:var(--muted);stroke-width:2.5}
details{margin:.5rem 0 0;font-size:.88rem;color:var(--muted);max-width:1000px}
summary{cursor:pointer;padding:.2rem 0}
.tw{overflow-x:auto;max-height:22rem;overflow-y:auto;margin-top:.4rem}
table{border-collapse:collapse;font-size:.8rem;white-space:nowrap}
th,td{padding:.25rem .5rem;text-align:right;border-bottom:1px solid var(--grid)}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{font-weight:500;color:var(--muted);position:sticky;top:0;background:var(--bg)}
.tw.big{max-height:none}
.klein{color:var(--muted);font-size:.9rem}
.klein code{color:var(--fg)}
"""


def build():
    S = load()
    tmax = max(max(col(s["rows"], "tSeconds")) for s in S)
    stats = {}
    d1 = S[0]["drift"]["models"]
    d2 = S[1]["drift"]["models"]
    kz = [
        (f"{de(d1['boardDrift']['driftOverSessionNormMm'],1)} mm", "Brettdrift S1 (Modell)"),
        (f"{de(d2['boardDrift']['driftOverSessionNormMm'],1)} mm", "Brettdrift S2 (Modell)"),
        (f"{de(d1['rigid']['rmsFirstHalfPx'],2)} → {de(d1['rigid']['rmsSecondHalfPx'],2)} px", "RMS 1. → 2. Hälfte S1"),
        (f"{de(d2['rigid']['rmsFirstHalfPx'],2)} → {de(d2['rigid']['rmsSecondHalfPx'],2)} px", "RMS 1. → 2. Hälfte S2"),
    ]
    kz_html = '<div class="kz">' + "".join(f"<div><b>{a}</b>{b}</div>" for a, b in kz) + "</div>"

    g1, t1 = multiples(S, [("boardDevXMm", "x", "mm")], tmax, stats)
    g2, t2 = multiples(S, [("boardDevYMm", "y", "mm"), ("boardDevZMm", "z", "mm")], tmax, stats)
    g3, t3 = multiples(S, [("meanResidualDxPx", "dx", "px"), ("meanResidualDyPx", "dy", "px")], tmax, stats)
    stats4 = []
    g4, t4 = chart4(S, stats4)
    better = " ".join(f"In {s['name']} ist das Driftmodell bei {b} von {n} Posen besser." for s, b, n in stats4)
    info5 = {}
    g5, t5 = chart5(S, info5)
    leg6, g6, t6 = chart6(S)
    mt = models_table(S)

    def rm(m):
        return f"{de(m['rigid']['reprojectionRmsPx'],2)} auf {de(m['boardDrift']['reprojectionRmsPx'],2)}"

    out = []
    A = out.append
    A('<title>Drift der Hand-Auge-Aufnahmen</title>')
    A('<meta name="claude:viewport-fit-sides" content="cover">')
    A('<link rel="preconnect" href="https://fonts.googleapis.com">')
    A('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500&display=swap">')
    A(f"<style>{CSS}</style>")
    A('<div class="wrap">')
    A('<h1>Drift der Hand-Auge-Aufnahmen</h1>')
    A('<p class="sub">Sitzung 1 (ChArUco) und Sitzung 2 (Schachbrett), offline ausgewertet, 09.11.2026</p>')
    A(kz_html)
    A('<p style="margin-top:1.6rem">Zwei Hand-Auge-Aufnahmen vom 09.11.2026, offline ausgewertet, ohne Roboterbewegung. '
      'Der Fehler je Pose ist ein starrer Versatz des ganzen Bretts im Bild und wächst mit der Aufnahmezeit. '
      'Die Frage: Wandert etwas während der Aufnahme?</p>')

    A('<h2>Wo das Brett aus Sicht des Roboters liegt</h2>')
    A('<p>Abweichung der Brettlage in x gegen die Aufnahmezeit. Jeder Punkt ist eine Pose.</p>')
    A(legend_group()); A(g1); A(t1)
    A('<p>In Sitzung 1 wandert die Brettlage deutlich und fast gleichmäßig in −x. In Sitzung 2 ist in x kaum ein Trend zu sehen.</p>')

    A('<h3 style="margin-top:2rem">y und z</h3>')
    A(legend_group()); A(g2); A(t2)
    A('<p>Diese Abweichungen stammen aus dem starren Modell. Es schiebt einen Teil der Drift in die Hand-Auge-Lösung, '
      'deshalb sind die Trends hier nicht die Drift selbst. In Sitzung 2 steigt z hier leicht (+1,3 mm), '
      'das Driftmodell weiter unten schätzt dort −1,2 mm.</p>')

    A('<h2>Versatz im Bild</h2>')
    A('<p>Mittlerer Residuenversatz der Eckpunkte im Bild, in x- und y-Richtung.</p>')
    A(legend_group()); A(g3); A(t3)

    A('<h2>Fehler je Pose: starr gegen Driftmodell</h2>')
    A(g4); A(t4)
    A(f'<p>{better}</p>')
    A(f'<p>Das Driftmodell (Brettlage darf linear mit der Zeit wandern, 6 zusätzliche Parameter) senkt den RMS in Sitzung 1 von {rm(d1)} px, '
      f'in Sitzung 2 nur von {rm(d2)} px. Der Fehler steigt in Sitzung 2 trotzdem mit der Zeit; '
      'dort hängen Aufnahmezeit und Neigung stark zusammen (ρ = 0,74), weil die Posegruppen blockweise gefahren wurden. '
      'Zeit- und Neigungseffekt lassen sich in Sitzung 2 nicht trennen.</p>')

    A('<h2>Translation über der Posenzahl</h2>')
    A('<p>Hand-Auge-Translation, geschätzt aus Teilmengen der Posen. Punkte sind einzelne Teilmengen, die Linie das Mittel je Posenzahl.</p>')
    A('<div class="legend"><span class="li"><svg class="lg" viewBox="0 0 30 14" width="30" height="14" aria-hidden="true"><line class="ln-r" x1="2" y1="7" x2="28" y2="7"/></svg>starr</span>'
      '<span class="li"><svg class="lg" viewBox="0 0 30 14" width="30" height="14" aria-hidden="true"><line class="ln-d" x1="2" y1="7" x2="28" y2="7"/></svg>Brettdrift</span></div>')
    A(g5); A(t5)
    A('<p>Mit Driftmodell schwankt t x in Sitzung 1 über die Teilmengen nur noch um 1,7 statt 2,4 mm, t y um 2,2 statt 4,8 mm. '
      'In Sitzung 2 wird x ruhiger (1,0 statt 2,6 mm), y nicht (~5 mm).</p>')

    A('<h2>Driftrichtung beider Sitzungen</h2>')
    A('<p>Drift der Brettlage über die Sitzung im Modell Brettdrift, je Achse in Basiskoordinaten.</p>')
    A(leg6); A(g6); A(t6)
    A('<p>In x und z zeigt die Drift in beiden Sitzungen in dieselbe Richtung (−x, −z), in y nicht; in der späteren Sitzung ist sie kleiner. '
      'Beide Sitzungen nutzten verschiedene Bretter. Das passt eher zu einer Bewegung von Roboter/Basis (z. B. Erwärmung) als zu einem verrutschten Brett – belegt ist es nicht. '
      'Ein Modell „Kamerahalterung wandert“ passt ähnlich gut, bräuchte aber 6–12 mm Bewegung und ist damit unplausibel.</p>')

    A('<h2>Modelle</h2>')
    A(f'<div class="tw big">{mt}</div>')

    A('<h2>Was daraus folgt</h2>')
    A('<ol>'
      '<li>Keine der bisherigen Lösungen aktivieren; das 0,5-mm-Ziel ist mit diesen Daten nicht erreichbar.</li>'
      '<li>Umgesetzt im Frontend (webskillcomposition, Branch handauge-schachbrett-offline): Warmfahren vor der Aufnahme (Vorgabe 15 min) und Posegruppen reihum statt blockweise – damit trennt die nächste Aufnahme Zeit und Neigung.</li>'
      '<li>Weiter empfohlen: eine feste Referenzpose alle ~8 Posen erneut anfahren; sie zeigt Drift direkt, ohne Modell.</li>'
      '<li>Nach der nächsten Aufnahme <code>drift.py</code> und diesen Bericht erneut erzeugen und vergleichen.</li>'
      '</ol>')

    A('<h2>Methode und Daten</h2>')
    A('<div class="klein">')
    A('<p><b>rigid</b>: Hand-Auge-Transformation und Brettlage sind über die ganze Sitzung konstant. '
      '<b>boardDrift</b>: die Brettlage darf linear mit der Zeit wandern (6 zusätzliche Parameter). '
      '<b>mountDrift</b>: die Kamerahalterung (Hand-Auge-Transformation) darf linear mit der Zeit wandern.</p>')
    A('<p>Brettlage-Abweichung: Brett aus Flansch · Hand-Auge · PnP gegen die gemittelte Brettlage, in Basiskoordinaten. '
      'Die Regressionen (Steigung, Pearson r) sind einfache lineare Regressionen über die Aufnahmezeit.</p>')
    A('<p>Befehle:<br><code>PYTHONPATH=src python tools/handeye_offline_cv4/analyse.py</code><br>'
      '<code>PYTHONPATH=src python tools/handeye_offline_cv4/drift.py</code><br>'
      '<code>python3 tools/handeye_offline_cv4/drift_report.py</code></p>')
    A('<p>Umgebung: OpenCV 4.14 separat, die Pis sind unverändert. Rohdaten wurden nur gelesen.</p>')
    A('<p>Sitzungen:<br>'
      + "<br>".join(f'{s["short"]}: <code>doc/auswertung/handeye_cv4/{s["id"]}_posen.csv</code>' for s in S)
      + '<br>Auswertung: <code>doc/auswertung/handeye_cv4/drift.json</code></p>')
    A('</div></div>')

    OUT.write_text("\n".join(out), encoding="utf-8")

    print(f"geschrieben: {OUT} ({OUT.stat().st_size} Bytes)")
    for (key, si), (sl, tot, r) in stats.items():
        print(f"{key} S{si+1}: Steigung {sl:.5f}/s, ueber Sitzung {tot:+.2f}, r = {r:.3f}")
    for k, v in info5.items():
        print("Sweep-Spannweite", k, {a: round(b, 2) for a, b in v.items()})
    for s, b, n in stats4:
        print(s["short"], "besser", b, "von", n)


if __name__ == "__main__":
    build()

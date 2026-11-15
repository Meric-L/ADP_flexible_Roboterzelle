#!/usr/bin/env python3
"""Erzeugt doc/auswertung/handeye_warm_vergleich.html (kalt gegen aufgewaermt).

Vergleicht vier Hand-Auge-Sitzungen vom 09.11.2026 (S1/S2 kalt, S3/S4 nach 15 min
Aufwaermen). Liest nur doc/auswertung/handeye_cv4/ (Ausgaben von analyse.py und
drift.py), nichts wird veraendert. Diagramme: matplotlib, als Inline-SVG mit
transparentem Hintergrund; die Farben sind Platzhalter, die nach dem Export durch
CSS-Variablen ersetzt werden (Hell/Dunkel).
Schreibt zusaetzlich handeye_cv4/warm_vergleich.json mit allen Kennzahlen.

Aufruf (Repo-Wurzel, separate Umgebung):
    PYTHONPATH=src .venv/bin/python tools/handeye_offline_cv4/warm_report.py [--out doc/auswertung/handeye_warm_vergleich.html]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from html import escape
from itertools import combinations
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from scipy.stats import rankdata, spearmanr  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Hilfsfunktionen und CSS aus dem bestehenden Driftbericht uebernehmen
from drift_report import CSS as BASE_CSS, de, details, table_html  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "doc" / "auswertung" / "handeye_cv4"
DEFAULT_OUT = ROOT / "doc" / "auswertung" / "handeye_warm_vergleich.html"
JSON_OUT = DATA / "warm_vergleich.json"
RAW = ROOT / "data" / "rohbilder" / "calibration"

SESSIONS = [
    {"id": "20261109T125050Z_a5c964f0", "short": "S1", "state": "kalt", "board": "ChArUco 11×7", "marker": "o", "link": None},
    {"id": "20261109T125825Z_a580eaba", "short": "S2", "state": "kalt", "board": "Schachbrett 7×10", "marker": "s", "link": None},
    {"id": "20261109T142717Z_a7d466a1", "short": "S3", "state": "warm", "board": "Schachbrett 7×10", "marker": "s",
     "link": "calibration_schachbrett_2.html"},
    {"id": "20261109T143834Z_6852cbc7", "short": "S4", "state": "warm", "board": "ChArUco 11×7", "marker": "o",
     "link": "calibration_charuco_2.html"},
]
GROUPS = "ABCDE"
MINUS = "−"

# ----------------------------------------------------------------- Farben
# Platzhalter-Hexwerte im SVG -> CSS-Variablen (Ersetzung nach dem Export)
PH = {
    "#010101": "var(--muted)",
    "#020202": "var(--grid)",
    "#030303": "var(--fg)",
    "#040404": "var(--bg)",
    "#0b0b01": "var(--c1)",
    "#0b0b02": "var(--c2)",
    "#0b0b03": "var(--c3)",
    "#0b0b04": "var(--c4)",
}
MUTED, GRIDC, FG, BG = "#010101", "#020202", "#030303", "#040404"
SCOL = ["#0b0b01", "#0b0b02", "#0b0b03", "#0b0b04"]

EXTRA_CSS = """
:root{--c1:#1d4f9c;--c2:#3f9fd9;--c3:#c4410c;--c4:#d99200}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--c1:#6a97f0;--c2:#8cc9f2;--c3:#ec7140;--c4:#f2b92f}}
:root[data-theme="dark"]{--c1:#6a97f0;--c2:#8cc9f2;--c3:#ec7140;--c4:#f2b92f}
.mp{display:block;height:auto;width:100%}
.mp text{font-family:"IBM Plex Sans",system-ui,sans-serif}
.sw1{fill:var(--c1)}.sw2{fill:var(--c2)}.sw3{fill:var(--c3)}.sw4{fill:var(--c4)}
.fazit{background:var(--surface);border-radius:6px;padding:.7rem 1rem;max-width:70ch;margin:1rem 0}
.fazit p{margin:.3rem 0}
.tw.big{max-width:1000px}
a{color:var(--accent)}
"""

plt.rcParams.update({
    "svg.fonttype": "none",
    "svg.hashsalt": "warm-vergleich",
    "font.family": ["DejaVu Sans"],  # im SVG durch IBM Plex Sans ersetzt (Browser)
    "font.size": 11,
    "axes.unicode_minus": True,
    "axes.edgecolor": GRIDC,
    "axes.labelcolor": MUTED,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "text.color": FG,
    "axes.titlecolor": MUTED,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.grid.axis": "y",
    "grid.color": GRIDC,
    "grid.linewidth": 0.9,
    "axes.axisbelow": True,
    "figure.facecolor": "none",
    "axes.facecolor": "none",
    "savefig.transparent": True,
    "legend.frameon": False,
})


# ----------------------------------------------------------------- Daten
def load():
    drift = {d["session"]: d for d in json.loads((DATA / "drift.json").read_text())}
    out = []
    for meta in SESSIONS:
        a = json.loads((DATA / f"{meta['id']}.json").read_text())
        with open(DATA / f"{meta['id']}_posen.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        raw_n = len(list((RAW / meta["id"] / "poses").glob("pose_*")))
        out.append({**meta, "a": a, "rows": rows, "drift": drift[meta["id"]], "rawPoses": raw_n})
    return out


def col(rows, key):
    return np.array([float(r[key]) for r in rows])


def rot_angle_deg(Ra, Rb):
    c = (np.trace(Ra.T @ Rb) - 1) / 2
    return float(np.degrees(np.arccos(np.clip(c, -1, 1))))


def partial_spearman(y, x, controls):
    """Teilkorrelation der Raenge: Residuen einer linearen Regression auf [1, Raenge der Kontrollvariablen]."""
    A = np.column_stack([np.ones(len(y))] + [rankdata(c) for c in controls])

    def res(v):
        r = rankdata(v)
        return r - A @ np.linalg.lstsq(A, r, rcond=None)[0]

    return float(np.corrcoef(res(y), res(x))[0, 1])


# ----------------------------------------------------------------- Kennzahlen
def compute(S):
    K = {"sessions": {}, "pairwise": {}, "kennzahlen": {}}
    finals = {}
    for s in S:
        a, rows, dr = s["a"], s["rows"], s["drift"]
        fs = a["finalSolution"]
        t = np.array(fs["translationMm"])
        R = np.array(fs["T_flange_camera"])[:3, :3]
        finals[s["short"]] = (t, R)
        ts = col(rows, "tSeconds")
        rm = col(rows, "reprojRmsPx")
        tilt = col(rows, "tiltDeg")
        dist = col(rows, "distanceMm")
        g = [r["poseGroup"] for r in rows]
        sw = sum(1 for i in range(1, len(g)) if g[i] != g[i - 1])
        frac = sw / (len(g) - 1)
        m = dr["models"]
        rho_t = spearmanr(rm, ts)
        rho_tt = spearmanr(ts, tilt)
        rigid, bd, md = m["rigid"], m["boardDrift"], m["mountDrift"]
        gw = a["groupSummary"]
        bdev = col(rows, "boardDevMm")
        info = {
            "state": s["state"], "board": s["board"],
            "posesValid": a["validPoseCount"], "posesTotal": s["rawPoses"],
            "durationS": float(ts.max()),
            "startUtc": rows[0]["capturedAt"],
            "groupSequence": "".join(g),
            "groupSwitchFraction": frac,
            "groupOrder": "gemischt (reihum)" if frac >= 0.5 else "blockweise",
            "groupTimeWindowsS": {k: gw[k]["timeWindowS"] for k in GROUPS if k in gw},
            "rmsPx": fs["rmsPx"],
            "translationMm": t.tolist(),
            "rotvecDeg": fs["rotvecDeg"],
            "rotationDeg": fs["rotationDeg"],
            "rmsFirstHalfPx": rigid["rmsFirstHalfPx"], "rmsSecondHalfPx": rigid["rmsSecondHalfPx"],
            "halfRatio": rigid["rmsSecondHalfPx"] / rigid["rmsFirstHalfPx"],
            "spearmanRmsVsT": float(rho_t[0]), "spearmanRmsVsTp": float(rho_t[1]),
            "spearmanTVsTilt": float(rho_tt[0]), "spearmanTVsTiltP": float(rho_tt[1]),
            "partialRmsVsT_tiltDist": partial_spearman(rm, ts, [tilt, dist]),
            "models": {},
            "boardDevMm": {"median": float(np.median(bdev)), "p90": float(np.percentile(bdev, 90)),
                           "max": float(bdev.max()), "mean": float(bdev.mean())},
            "boardDevMedianByGroup": {k: float(np.median([b for b, gg in zip(bdev, g) if gg == k]))
                                      for k in GROUPS if k in g},
            "boardDevRotMedianDeg": float(np.median(col(rows, "boardDevRotDeg"))),
            "medianTiltDeg": float(np.median(tilt)),
        }
        for name in ("rigid", "boardDrift", "mountDrift"):
            mm = m[name]
            e = {"rmsPx": mm["reprojectionRmsPx"], "rmsFirstHalfPx": mm["rmsFirstHalfPx"],
                 "rmsSecondHalfPx": mm["rmsSecondHalfPx"], "translationMm": mm["translationMm"],
                 "gainPct": 100 * (1 - mm["reprojectionRmsPx"] / rigid["reprojectionRmsPx"])}
            if name != "rigid":
                e["driftMm"] = mm["driftOverSessionMm"]
                e["driftNormMm"] = mm["driftOverSessionNormMm"]
                e["driftDeg"] = mm["driftOverSessionDeg"]
            info["models"][name] = e
        # Sweep (starr): je Posenzahl Teilmengen, Spannweite und Abstand zur Endloesung
        sweep = {}
        for c in (15, 25, 40):
            sub = [np.array(e["rigid"]["translationMm"]) for e in dr["sweep"] if e["poseCount"] == c]
            if not sub:
                continue
            arr = np.array(sub)
            pair = [float(np.linalg.norm(p - q)) for p, q in combinations(sub, 2)]
            sweep[str(c)] = {
                "subsets": len(sub), "rangeMm": (arr.max(0) - arr.min(0)).tolist(),
                "maxPairMm": max(pair, default=0.0), "maxDistToFinalMm": float(max(np.linalg.norm(p - t) for p in sub)),
                "meanDistToFinalMm": float(np.mean([np.linalg.norm(p - t) for p in sub])),
            }
        info["sweep"] = sweep
        cum = a["temporal"]["cumulative"]
        info["cumulative"] = [{"k": c["k"], "distToFinalMm": float(np.linalg.norm(np.array(c["translationMm"]) - t)),
                               "rmsPx": c["rmsPx"]} for c in cum]
        K["sessions"][s["short"]] = info

    names = [s["short"] for s in S]
    for p, q in combinations(names, 2):
        (tp, Rp), (tq, Rq) = finals[p], finals[q]
        K["pairwise"][f"{p}-{q}"] = {"mm": float(np.linalg.norm(tp - tq)), "deg": rot_angle_deg(Rp, Rq),
                                     "dMm": (tq - tp).tolist()}
    allt = np.array([finals[n][0] for n in names])
    k = K["kennzahlen"]
    k["spanMaxPairMm"] = max(v["mm"] for v in K["pairwise"].values())
    k["spanMaxPairDeg"] = max(v["deg"] for v in K["pairwise"].values())
    k["axisRangeMm"] = (allt.max(0) - allt.min(0)).tolist()
    k["warmPairMm"] = K["pairwise"]["S3-S4"]["mm"]
    k["coldPairMm"] = K["pairwise"]["S1-S2"]["mm"]
    k["crossPairsMm"] = {key: v["mm"] for key, v in K["pairwise"].items() if key not in ("S1-S2", "S3-S4")}
    k["minPairMm"] = min(v["mm"] for v in K["pairwise"].values())
    cold = [K["sessions"][n]["models"]["boardDrift"]["gainPct"] for n in ("S1", "S2")]
    warm = [K["sessions"][n]["models"]["boardDrift"]["gainPct"] for n in ("S3", "S4")]
    k["boardDriftGainColdPct"], k["boardDriftGainWarmPct"] = cold, warm
    k["target05mm"] = "belegt" if k["spanMaxPairMm"] <= 0.5 else "nicht belegt"
    # Sweep: gepoolt je Zustand
    for st, ns in (("kalt", ("S1", "S2")), ("warm", ("S3", "S4"))):
        v = [K["sessions"][n]["sweep"][c]["maxDistToFinalMm"] for n in ns for c in ("15", "25", "40")]
        w = [K["sessions"][n]["sweep"][c]["maxPairMm"] for n in ns for c in ("15", "25", "40")]
        k[f"sweepMaxDistToFinalMean_{st}"] = float(np.mean(v))
        k[f"sweepMaxPairMean_{st}"] = float(np.mean(w))
    return K


# ----------------------------------------------------------------- Plot-Bausteine
def fmt_axis(ax, dec=1, axis="y"):
    f = FuncFormatter(lambda v, _: de(v, dec))
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(f)


def new_fig(w=6.4, h=4.0):
    fig, ax = plt.subplots(figsize=(w, h), layout="constrained")
    ax.tick_params(length=0)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(GRIDC)
    return fig, ax


def to_svg(fig, label):
    buf = io.StringIO()
    fig.savefig(buf, format="svg", metadata={"Date": None, "Creator": None})
    plt.close(fig)
    s = buf.getvalue()
    s = s[s.index("<svg"):]
    s = re.sub(r"<metadata>.*?</metadata>", "", s, flags=re.S)
    s = re.sub(r"<style type=\"text/css\">.*?</style>", "", s, flags=re.S)
    s = re.sub(r"<defs>\s*</defs>", "", s)
    for k, v in PH.items():
        s = s.replace(k, v)
    s = re.sub(r"font: ([0-9.]+px) '[^']*'", r"font: \1 'IBM Plex Sans', 'DejaVu Sans', sans-serif", s)
    s = re.sub(r"font-family: '[^']*'", "font-family: 'IBM Plex Sans', 'DejaVu Sans', sans-serif", s)
    head_end = s.index(">")
    head = re.sub(r'\swidth="[^"]*"|\sheight="[^"]*"', "", s[:head_end])
    head = head.replace("<svg", f'<svg class="mp" role="img" aria-label="{escape(label)}"', 1)
    return head + s[head_end:]


def cell(title, svg):
    return f'<div class="cell"><h4>{escape(title)}</h4>{svg}</div>'


def grid(cells):
    return '<div class="grid">' + "".join(cells) + "</div>"


def sess_legend(S, extra=""):
    items = []
    for i, s in enumerate(S):
        if s["marker"] == "o":
            shp = '<circle cx="9" cy="9" r="5.5"/>'
        else:
            shp = '<rect x="3.5" y="3.5" width="11" height="11"/>'
        items.append(f'<span class="li"><svg class="lg sw{i+1}" viewBox="0 0 18 18" width="18" height="18" aria-hidden="true">{shp}</svg>'
                     f'{s["short"]} · {s["state"]} · {escape(s["board"].split()[0])}</span>')
    return '<div class="legend">' + "".join(items) + extra + "</div>"


def scatter_marker(ax, x, y, i, S, size=9, **kw):
    ax.plot([x], [y], ls="", marker=S[i]["marker"], ms=size, color=SCOL[i], mec=BG, mew=1.1, zorder=3, **kw)


# ----------------------------------------------------------------- Diagramme
def plot_final(S, K):
    cells = []
    for ai, ax_name in enumerate("xyz"):
        fig, ax = new_fig()
        vals = [K["sessions"][s["short"]]["translationMm"][ai] for s in S]
        lo, hi = min(vals), max(vals)
        span = max(hi - lo, 0.4)
        ax.axhspan(lo, hi, color=MUTED, alpha=0.12, lw=0, zorder=1)
        for i, v in enumerate(vals):
            scatter_marker(ax, i, v, i, S, 11)
            ax.annotate(de(v, 2), (i, v), xytext=(0, 12), textcoords="offset points", ha="center",
                        fontsize=10, color=FG)
        ax.set_ylim(lo - 0.9 * span, hi + 0.9 * span)
        ax.set_xlim(-0.6, 3.6)
        ax.set_xticks(range(4), [f"{s['short']}\n{s['state']}" for s in S])
        ax.set_ylabel("mm")
        fmt_axis(ax, 1)
        ax.text(3.55, lo - 0.82 * span, f"Spannweite {de(hi - lo, 2)} mm", ha="right", fontsize=10, color=MUTED)
        cells.append(cell(f"T_flange_camera, {ax_name}", to_svg(fig, f"Endlösungen, Translation {ax_name}")))
    return grid(cells)


def plot_sweep(S, K):
    cells = []
    allv = {ai: [] for ai in range(3)}
    for s in S:
        for e in s["drift"]["sweep"]:
            for ai in range(3):
                allv[ai].append(e["rigid"]["translationMm"][ai])
    for ai, ax_name in enumerate("xyz"):
        fig, ax = new_fig()
        cats = [15, 25, 40, "alle"]
        for si, s in enumerate(S):
            off = (si - 1.5) * 0.17
            n_all = s["drift"]["poseCount"]
            for e in s["drift"]["sweep"]:
                c = e["poseCount"]
                xi = 3 if c == n_all else cats.index(c)
                v = e["rigid"]["translationMm"][ai]
                big = c == n_all
                ax.plot([xi + off], [v], ls="", marker=s["marker"], ms=9 if big else 6.5, color=SCOL[si],
                        mec=BG, mew=1.0, alpha=1 if big else 0.8, zorder=3)
        lo, hi = min(allv[ai]), max(allv[ai])
        p = (hi - lo) * 0.08
        ax.set_ylim(lo - p, hi + p)
        ax.set_xlim(-0.55, 3.55)
        ax.set_xticks(range(4), ["15", "25", "40", "alle"])
        ax.set_xlabel("Posenzahl der Teilmenge")
        ax.set_ylabel("mm")
        fmt_axis(ax, 0 if (hi - lo) > 6 else 1)
        cells.append(cell(f"Translation {ax_name} (starr)", to_svg(fig, f"Translation {ax_name} über die Posenzahl")))
    return grid(cells)


def plot_cumulative(S, K):
    fig, ax = new_fig(6.4, 4.0)
    for si, s in enumerate(S):
        cum = K["sessions"][s["short"]]["cumulative"]
        ks = [c["k"] for c in cum]
        ds = [c["distToFinalMm"] for c in cum]
        ax.plot(ks, ds, color=SCOL[si], lw=2, marker=s["marker"], ms=6, mec=BG, mew=0.8, zorder=3)
    ax.set_xlabel("erste k Posen (Aufnahmereihenfolge)")
    ax.set_ylabel("Abstand zur Endlösung (mm)")
    ax.set_ylim(0, None)
    ax.set_xlim(8, None)
    fmt_axis(ax, 0)
    return to_svg(fig, "Abstand der kumulativen Lösung zur Endlösung")


def running_median(t, v, w=9):
    o = np.argsort(t)
    ts, vs = t[o], v[o]
    h = w // 2
    return ts, np.array([np.median(vs[max(0, i - h): i + h + 1]) for i in range(len(vs))])


def plot_time(S, K):
    ymax = max(col(s["rows"], "reprojRmsPx").max() for s in S) * 1.06
    cells = []
    for si, s in enumerate(S):
        ts, rm = col(s["rows"], "tSeconds"), col(s["rows"], "reprojRmsPx")
        fig, ax = new_fig()
        ax.plot(ts, rm, ls="", marker=s["marker"], ms=6.5, color=SCOL[si], mec=BG, mew=0.8, alpha=0.9, zorder=3)
        mt, mv = running_median(ts, rm)
        ax.plot(mt, mv, color=FG, lw=1.6, ls="--", zorder=4)
        k = K["sessions"][s["short"]]
        ax.text(0.02, 0.97, f"ρ = {de(k['spearmanRmsVsT'], 2)} · Teil-ρ = {de(k['partialRmsVsT_tiltDist'], 2)}",
                transform=ax.transAxes, va="top", fontsize=10.5, color=FG)
        ax.set_ylim(0, ymax)
        ax.set_xlim(-8, 410)
        ax.set_xlabel("Aufnahmezeit (s)")
        ax.set_ylabel("Reprojektions-RMS je Pose (px)")
        fmt_axis(ax, 0)
        cells.append(cell(f"{s['short']} · {s['state']} · {s['board']}", to_svg(fig, f"{s['short']}: RMS je Pose über die Zeit")))
    return grid(cells)


def plot_halves(S, K):
    fig, ax = new_fig(6.4, 4.0)
    w = 0.36
    for si, s in enumerate(S):
        k = K["sessions"][s["short"]]
        for j, (key, alpha) in enumerate((("rmsFirstHalfPx", 0.4), ("rmsSecondHalfPx", 1.0))):
            v = k[key]
            x = si + (j - 0.5) * (w + 0.03)
            ax.bar(x, v, w, color=SCOL[si], alpha=alpha, lw=0, zorder=3)
            ax.annotate(de(v, 2), (x, v), xytext=(0, 3), textcoords="offset points", ha="center", fontsize=9.5, color=FG)
    ax.set_xticks(range(4), [f"{s['short']}\n{s['state']}" for s in S])
    ax.set_ylabel("RMS starr (px)")
    ax.set_ylim(0, max(K["sessions"][s["short"]]["rmsSecondHalfPx"] for s in S) * 1.15)
    fmt_axis(ax, 1)
    return to_svg(fig, "RMS erste und zweite Hälfte")


def plot_gain(S, K):
    fig, ax = new_fig(6.4, 4.0)
    w = 0.36
    for si, s in enumerate(S):
        m = K["sessions"][s["short"]]["models"]
        for j, (key, alpha) in enumerate((("boardDrift", 1.0), ("mountDrift", 0.4))):
            v = m[key]["gainPct"]
            x = si + (j - 0.5) * (w + 0.03)
            ax.bar(x, v, w, color=SCOL[si], alpha=alpha, lw=0, zorder=3)
            ax.annotate(de(v, 1), (x, v), xytext=(0, 3), textcoords="offset points", ha="center", fontsize=9.5, color=FG)
    ax.set_xticks(range(4), [f"{s['short']}\n{s['state']}" for s in S])
    ax.set_ylabel("RMS-Gewinn gegenüber starr (%)")
    ax.set_ylim(0, max(K["sessions"][s["short"]]["models"]["mountDrift"]["gainPct"] for s in S) * 1.15)
    fmt_axis(ax, 0)
    return to_svg(fig, "RMS-Gewinn der Driftmodelle")


def plot_driftmm(S, K):
    fig, ax = new_fig(6.4, 4.0)
    for si, s in enumerate(S):
        v = K["sessions"][s["short"]]["models"]["boardDrift"]["driftNormMm"]
        ax.bar(si, v, 0.5, color=SCOL[si], lw=0, zorder=3)
        ax.annotate(de(v, 1), (si, v), xytext=(0, 3), textcoords="offset points", ha="center", fontsize=9.5, color=FG)
    ax.set_xticks(range(4), [f"{s['short']}\n{s['state']}" for s in S])
    ax.set_ylabel("Brettdrift über die Sitzung (mm)")
    ax.set_ylim(0, max(K["sessions"][s["short"]]["models"]["boardDrift"]["driftNormMm"] for s in S) * 1.15)
    fmt_axis(ax, 0)
    return to_svg(fig, "Betrag der Brettdrift")


def box_strip(ax, data, positions, colors, rng, jitter=0.16, ms=5.5, markers=None):
    bp = ax.boxplot(data, positions=positions, widths=0.5, patch_artist=True, showfliers=False,
                    boxprops=dict(facecolor="none", edgecolor=MUTED, lw=1.2),
                    whiskerprops=dict(color=MUTED, lw=1.2), capprops=dict(color=MUTED, lw=1.2),
                    medianprops=dict(color=FG, lw=2.0), manage_ticks=False)
    for b in bp["boxes"]:
        b.set_zorder(2)
    for i, (d, x) in enumerate(zip(data, positions)):
        xs = x + rng.uniform(-jitter, jitter, len(d))
        ax.plot(xs, d, ls="", marker=(markers[i] if markers else "o"), ms=ms, color=colors[i], mec=BG, mew=0.6,
                alpha=0.85, zorder=3)


def plot_boarddev(S, K):
    rng = np.random.default_rng(7)
    fig, ax = new_fig(6.4, 4.2)
    data = [col(s["rows"], "boardDevMm") for s in S]
    box_strip(ax, data, range(4), SCOL, rng, markers=[s["marker"] for s in S])
    ax.set_xticks(range(4), [f"{s['short']}\n{s['state']}" for s in S])
    ax.set_ylabel("Brettlage-Abweichung je Pose (mm)")
    ax.set_ylim(0, None)
    fmt_axis(ax, 0)
    return to_svg(fig, "Brettlage-Streuung je Sitzung")


def plot_boarddev_groups(S, K):
    ymax = max(col(s["rows"], "boardDevMm").max() for s in S) * 1.06
    cells = []
    for si, s in enumerate(S):
        rng = np.random.default_rng(11 + si)
        rows = s["rows"]
        gg = [r["poseGroup"] for r in rows]
        bd = col(rows, "boardDevMm")
        fig, ax = new_fig()
        data = [bd[[i for i, g in enumerate(gg) if g == k]] for k in GROUPS]
        box_strip(ax, data, range(5), [SCOL[si]] * 5, rng, jitter=0.15, ms=5, markers=[s["marker"]] * 5)
        ax.set_xticks(range(5), [f"{k}\n(n={len(d)})" for k, d in zip(GROUPS, data)])
        ax.set_ylim(0, ymax)
        ax.set_ylabel("Abweichung (mm)")
        fmt_axis(ax, 0)
        cells.append(cell(f"{s['short']} · {s['state']} · {s['board']}", to_svg(fig, f"{s['short']}: Brettlage-Streuung je Gruppe")))
    return grid(cells)


# ----------------------------------------------------------------- Seite
def build(out_path: Path):
    S = load()
    K = compute(S)
    k = K["kennzahlen"]
    ks = K["sessions"]
    pw = K["pairwise"]
    n = lambda x, d=1: de(x, d)  # noqa: E731

    # ---- Diagramme
    svg_final = plot_final(S, K)
    svg_sweep = plot_sweep(S, K)
    svg_cum = plot_cumulative(S, K)
    svg_time = plot_time(S, K)
    svg_halves = plot_halves(S, K)
    svg_gain = plot_gain(S, K)
    svg_dmm = plot_driftmm(S, K)
    svg_bd = plot_boarddev(S, K)
    svg_bdg = plot_boarddev_groups(S, K)

    # ---- Tabellen
    def dur(x):
        return f"{int(x // 60)}:{int(x % 60):02d} min"

    t_sess = table_html(
        ["Sitzung", "Zustand", "Brett", "Posen gültig/gesamt", "Dauer", "Gruppen-Reihenfolge", "Gruppenwechsel",
         "RMS (px)", "Translation x/y/z (mm)", "Rotation (rotvec, °)"],
        [[s["short"], s["state"], s["board"], f"{ks[s['short']]['posesValid']}/{ks[s['short']]['posesTotal']}",
          dur(ks[s["short"]]["durationS"]), ks[s["short"]]["groupOrder"],
          f"{n(100 * ks[s['short']]['groupSwitchFraction'], 0)} %",
          n(ks[s["short"]]["rmsPx"], 3), " / ".join(n(v, 2) for v in ks[s["short"]]["translationMm"]),
          " / ".join(n(v, 2) for v in ks[s["short"]]["rotvecDeg"])] for s in S])

    def win(sn, g):
        w = ks[sn]["groupTimeWindowsS"].get(g)
        return f"{n(w[0], 0)}–{n(w[1], 0)}" if w else "–"

    t_win = table_html(["Gruppe"] + [f"{s['short']} {s['state']} (s)" for s in S],
                       [[g] + [win(s["short"], g) for s in S] for g in GROUPS])

    names = [s["short"] for s in S]

    def pair(a, b, key):
        if a == b:
            return "–"
        v = pw.get(f"{a}-{b}") or pw.get(f"{b}-{a}")
        return n(v[key], 2)

    t_pmm = table_html(["mm"] + names, [[a] + [pair(a, b, "mm") for b in names] for a in names])
    t_pdeg = table_html(["°"] + names, [[a] + [pair(a, b, "deg") for b in names] for a in names])

    rows_sw = []
    for s in S:
        for c in ("15", "25", "40"):
            e = ks[s["short"]]["sweep"].get(c)
            if e:
                rows_sw.append([s["short"], s["state"], c, e["subsets"], *[n(v, 2) for v in e["rangeMm"]],
                                n(e["maxPairMm"], 2), n(e["maxDistToFinalMm"], 2)])
    t_sw = table_html(["Sitzung", "Zustand", "Posen", "Teilmengen", "Spannweite x (mm)", "y (mm)", "z (mm)",
                       "max. Abstand untereinander (mm)", "max. Abstand zur Endlösung (mm)"], rows_sw)

    def cum_at(sn, kk):
        for c in ks[sn]["cumulative"]:
            if c["k"] == kk:
                return c["distToFinalMm"]
        return None

    t_cum = table_html(["k"] + [f"{s['short']} (mm)" for s in S],
                       [[kk] + [n(cum_at(s["short"], kk), 2) if cum_at(s["short"], kk) is not None else "–" for s in S]
                        for kk in (10, 15, 20, 25, 30, 35, 40, 45)])

    t_time = table_html(
        ["Sitzung", "RMS 1. Hälfte (px)", "RMS 2. Hälfte (px)", "Verhältnis", "ρ(RMS, t)", "p", "ρ(t, Kippung)",
         "Teil-ρ(RMS, t | Kippung, Abstand)"],
        [[s["short"], n(ks[s["short"]]["rmsFirstHalfPx"], 2), n(ks[s["short"]]["rmsSecondHalfPx"], 2),
          n(ks[s["short"]]["halfRatio"], 2), n(ks[s["short"]]["spearmanRmsVsT"], 2),
          ("<0,001" if ks[s["short"]]["spearmanRmsVsTp"] < 0.001 else n(ks[s["short"]]["spearmanRmsVsTp"], 3)), n(ks[s["short"]]["spearmanTVsTilt"], 2),
          n(ks[s["short"]]["partialRmsVsT_tiltDist"], 2)] for s in S])

    rows_m = []
    label = {"rigid": "starr", "boardDrift": "Brettdrift", "mountDrift": "Halterungsdrift"}
    for s in S:
        for mname in ("rigid", "boardDrift", "mountDrift"):
            e = ks[s["short"]]["models"][mname]
            dm = n(e["driftNormMm"], 2) if "driftNormMm" in e else "–"
            dd = n(e["driftDeg"], 2) if "driftDeg" in e else "–"
            dxyz = " / ".join(n(v, 2) for v in e["driftMm"]) if mname == "boardDrift" else "–"
            rows_m.append([s["short"], s["state"], label[mname], n(e["rmsPx"], 3),
                           "–" if mname == "rigid" else n(e["gainPct"], 1), dm, dd, dxyz,
                           f"{n(e['rmsFirstHalfPx'], 2)} / {n(e['rmsSecondHalfPx'], 2)}"])
    t_models = table_html(["Sitzung", "Zustand", "Modell", "RMS (px)", "Gewinn (%)", "Drift (mm)", "Drift (°)",
                           "Drift x/y/z (mm)", "RMS 1./2. Hälfte (px)"], rows_m)

    t_bd = table_html(
        ["Sitzung", "Median (mm)", "90. Perzentil (mm)", "Maximum (mm)", "Mittel (mm)", "Median Drehung (°)"] +
        [f"Median Gruppe {g} (mm)" for g in GROUPS],
        [[s["short"], n(ks[s["short"]]["boardDevMm"]["median"], 2), n(ks[s["short"]]["boardDevMm"]["p90"], 2),
          n(ks[s["short"]]["boardDevMm"]["max"], 2), n(ks[s["short"]]["boardDevMm"]["mean"], 2),
          n(ks[s["short"]]["boardDevRotMedianDeg"], 2)] +
         [n(ks[s["short"]]["boardDevMedianByGroup"][g], 2) for g in GROUPS] for s in S])

    # ---- Texte aus den Kennzahlen
    gc, gw = k["boardDriftGainColdPct"], k["boardDriftGainWarmPct"]
    cross = k["crossPairsMm"]
    s1, s2, s3, s4 = (ks[x] for x in ("S1", "S2", "S3", "S4"))
    kz = [
        (f"{n(k['spanMaxPairMm'], 2)} mm", "Spannweite der vier Endlösungen (größter paarweiser Abstand)"),
        (f"{n(k['warmPairMm'], 2)} mm", "Abstand der beiden warmen Lösungen (S3–S4)"),
        (f"{n(k['coldPairMm'], 2)} mm", "Abstand der beiden kalten Lösungen (S1–S2)"),
        (f"warm {n(min(gw), 0)}–{n(max(gw), 0)} %", f"RMS-Gewinn durch Brettdrift; kalt {n(min(gc), 0)}–{n(max(gc), 0)} %"),
        (k["target05mm"], f"0,5-mm-Ziel (Sitzungen bis {n(k['spanMaxPairMm'], 2)} mm auseinander)"),
    ]
    kz_html = '<div class="kz">' + "".join(f"<div><b>{a}</b>{b}</div>" for a, b in kz) + "</div>"

    sweep_cold = k["sweepMaxDistToFinalMean_kalt"]
    sweep_warm = k["sweepMaxDistToFinalMean_warm"]

    o = []
    A = o.append
    A("<title>Hand-Auge: kalt gegen aufgewärmt</title>")
    A('<meta name="claude:viewport-fit-sides" content="cover">')
    A('<link rel="preconnect" href="https://fonts.googleapis.com">')
    A('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500&display=swap">')
    A(f"<style>{BASE_CSS}{EXTRA_CSS}</style>")
    A('<div class="wrap">')
    A("<h1>Hand-Auge: kalt gegen aufgewärmt</h1>")
    A('<p class="sub">Vier Sitzungen vom 09.11.2026, offline ausgewertet: zwei kalt aufgenommen, zwei nach 15 min Aufwärmen des Roboters.</p>')
    A(kz_html)

    # 1 Kurzfassung
    A("<h2>1. Kurzfassung</h2>")
    A("<ul>")
    A(f"<li>Die vier Endlösungen (Translation) liegen höchstens {n(k['spanMaxPairMm'], 2)} mm auseinander, die Rotationen höchstens "
      f"{n(k['spanMaxPairDeg'], 2)}°. Spannweite je Achse: x {n(k['axisRangeMm'][0], 2)}, y {n(k['axisRangeMm'][1], 2)}, z {n(k['axisRangeMm'][2], 2)} mm.</li>")
    A(f"<li>Die beiden warmen Lösungen (S3–S4) liegen {n(k['warmPairMm'], 2)} mm auseinander, die beiden kalten (S1–S2) {n(k['coldPairMm'], 2)} mm. "
      f"Die Paare kalt/warm liegen {n(min(cross.values()), 2)} bis {n(max(cross.values()), 2)} mm auseinander.</li>")
    A(f"<li>Der Reprojektions-RMS der Endlösung liegt bei {n(s1['rmsPx'], 2)} / {n(s2['rmsPx'], 2)} px (kalt) und "
      f"{n(s3['rmsPx'], 2)} / {n(s4['rmsPx'], 2)} px (warm). Aufwärmen senkt ihn nicht.</li>")
    A(f"<li>Zeiteffekt bleibt: Der starre RMS der 2. Hälfte ist in S3 {n(s3['halfRatio'], 1)}-mal und in S4 {n(s4['halfRatio'], 1)}-mal so groß wie in der 1. Hälfte "
      f"(kalt: S1 {n(s1['halfRatio'], 1)}-mal, S2 {n(s2['halfRatio'], 1)}-mal).</li>")
    A(f"<li>In S4 bleibt nach Herausrechnen von Kippung und Abstand eine Teilkorrelation von {n(s4['partialRmsVsT_tiltDist'], 2)} zwischen RMS und Zeit "
      f"(ρ roh {n(s4['spearmanRmsVsT'], 2)}). In S3 sinkt sie auf {n(s3['partialRmsVsT_tiltDist'], 2)} (roh {n(s3['spearmanRmsVsT'], 2)}); "
      f"dort steigt die Kippung mit der Zeit (ρ = {n(s3['spearmanTVsTilt'], 2)}), die Reihenfolge ist also nicht vollständig gemischt.</li>")
    A(f"<li>Eine lineare Brettdrift senkt den RMS nach dem Aufwärmen nur um {n(gw[0], 1)} % (S3) und {n(gw[1], 1)} % (S4); in S1 (kalt) um {n(gc[0], 1)} %, in S2 (kalt) um {n(gc[1], 1)} %. "
      "Die große Drift aus S1 ist nicht wieder aufgetreten, S2 war aber ebenfalls kalt und zeigte kaum Drift. Aufwärmen ist als Ursache nicht eindeutig belegt.</li>")
    A(f"<li>Das 0,5-mm-Ziel bleibt {k['target05mm']}: die Endlösungen liegen bis {n(k['spanMaxPairMm'], 2)} mm auseinander, nur einzelne Paare unter 0,5 mm (kleinster Abstand {n(k['minPairMm'], 2)} mm). "
      "Die Übereinstimmung zwischen Sitzungen ist zudem nur Wiederholbarkeit, keine Genauigkeit; die Genauigkeit braucht eine unabhängige Messung.</li>")
    A("</ul>")

    # 2 Sitzungen
    A("<h2>2. Sitzungen</h2>")
    A(f'<div class="tw big">{t_sess}</div>')
    A("<p>Einzelberichte der warmen Sitzungen: "
      '<a href="calibration_schachbrett_2.html">S3 Schachbrett</a>, '
      '<a href="calibration_charuco_2.html">S4 ChArUco</a>. '
      'Frühere Berichte: <a href="calibration_cv4_vergleich.html">Vergleich mit cv2.calibrateHandEye (S1, S2)</a>, '
      '<a href="handeye_drift.html">Drift (S1, S2)</a>.</p>')
    A(f"<p>Gruppenwechsel: Anteil der Posenfolgen, bei denen die Gruppe von einer Pose zur nächsten wechselt "
      f"(S1 {n(100 * s1['groupSwitchFraction'], 0)} %, S2 {n(100 * s2['groupSwitchFraction'], 0)} %, "
      f"S3 {n(100 * s3['groupSwitchFraction'], 0)} %, S4 {n(100 * s4['groupSwitchFraction'], 0)} %). "
      "Die kalten Sitzungen fuhren die Gruppen überwiegend blockweise, die warmen reihum (A, B, C, D, E, A, …). "
      "Zeitfenster der Gruppen (erste bis letzte Pose, Sekunden ab Sitzungsstart):</p>")
    A(f'<div class="tw big">{t_win}</div>')

    # 3 Endloesungen
    A("<h2>3. Endlösungen im Vergleich</h2>")
    A("<p>Translation der Endlösung (Reprojektions-Verfeinerung) je Achse. Das graue Band ist die Spannweite der vier Sitzungen.</p>")
    A(sess_legend(S))
    A(svg_final)
    A("<p>Paarweise Abstände der Endlösungen, Translation in mm:</p>")
    A(f'<div class="tw big">{t_pmm}</div>')
    A("<p>Drehwinkel zwischen den Rotationen der Endlösungen in Grad:</p>")
    A(f'<div class="tw big">{t_pdeg}</div>')
    A(f"<p>Der größte Abstand ({n(k['spanMaxPairMm'], 2)} mm) liegt zwischen den Sitzungen "
      f"{max(pw, key=lambda q: pw[q]['mm'])}. Die Rotationen unterscheiden sich um höchstens {n(k['spanMaxPairDeg'], 2)}°.</p>")

    # 4 Sweep
    A("<h2>4. Translation über die Posenzahl</h2>")
    A("<p>Starre Lösung aus D-optimalen Teilmengen mit 15, 25 und 40 Posen (je 2–3 Teilmengen) und aus allen Posen. "
      "Je Sitzung ist die Teilmenge eine eigene Marke; die größere Marke ganz rechts ist die Lösung mit allen Posen.</p>")
    A(sess_legend(S))
    A(svg_sweep)
    A(f'<div class="tw big">{t_sw}</div>')
    A("<p>Kumulativ in Aufnahmereihenfolge: Abstand der Lösung aus den ersten k Posen zur Endlösung.</p>")
    A(sess_legend(S))
    A(f'<div class="one">{svg_cum}</div>')
    A(f'<div class="tw big">{t_cum}</div>')
    A(f"<p>Über die Teilmengen mit 15, 25 und 40 Posen gemittelt weichen die Lösungen höchstens {n(sweep_cold, 2)} mm (kalt) beziehungsweise "
      f"{n(sweep_warm, 2)} mm (warm) von der jeweiligen Endlösung ab; der größte Abstand zweier Teilmengen untereinander beträgt im Mittel "
      f"{n(k['sweepMaxPairMean_kalt'], 2)} mm (kalt) und {n(k['sweepMaxPairMean_warm'], 2)} mm (warm). "
      "Bei festen Posenzahlen sind die warmen Sitzungen also nicht stabiler. "
      f"Kumulativ in Aufnahmereihenfolge liegt die Lösung der ersten 10 Posen in den kalten Sitzungen {n(cum_at('S1', 10), 1)} und {n(cum_at('S2', 10), 1)} mm von der Endlösung, "
      f"in den warmen {n(cum_at('S3', 10), 1)} und {n(cum_at('S4', 10), 1)} mm. Das liegt vermutlich an der Reihenfolge und nicht am Aufwärmen: "
      "die kalten Sitzungen fuhren zuerst einzelne Gruppen (S1 und S2 beginnen mit Gruppe E), die warmen decken ab der ersten Runde alle Gruppen ab. "
      "Ob das Aufwärmen selbst die Lösung stabiler macht, trennen diese Daten nicht.</p>")

    # 5 Zeiteffekt
    A("<h2>5. Zeiteffekt</h2>")
    A("<p>Reprojektions-RMS je Pose gegen die Aufnahmezeit. Die gestrichelte Linie ist der gleitende Median über 9 Posen; "
      "ρ ist die Spearman-Korrelation zwischen RMS und Zeit, Teil-ρ dieselbe Korrelation nach Herausrechnen von Kippung und Abstand "
      "(Ränge, Residuen einer linearen Regression auf Kippung und Abstand).</p>")
    A(sess_legend(S))
    A(svg_time)
    A("<p>RMS der ersten und zweiten Hälfte der Posen (starres Modell); hell: 1. Hälfte, voll: 2. Hälfte.</p>")
    A(f'<div class="one">{svg_halves}</div>')
    A(f'<div class="tw big">{t_time}</div>')
    A(f"<p>Nach dem Aufwärmen bleibt die 2. Hälfte schlechter als die erste: {n(s3['rmsFirstHalfPx'], 2)} auf {n(s3['rmsSecondHalfPx'], 2)} px in S3 "
      f"und {n(s4['rmsFirstHalfPx'], 2)} auf {n(s4['rmsSecondHalfPx'], 2)} px in S4 (Faktor {n(s3['halfRatio'], 1)} und {n(s4['halfRatio'], 1)}). "
      f"In S4 bleibt nach Herausrechnen von Kippung und Abstand ein Zeiteffekt (Teil-ρ {n(s4['partialRmsVsT_tiltDist'], 2)}). "
      f"In S3 erklärt die Geometrie den größten Teil (Teil-ρ {n(s3['partialRmsVsT_tiltDist'], 2)}): "
      f"die Kippung steigt dort mit der Zeit (ρ = {n(s3['spearmanTVsTilt'], 2)}). "
      "Die Posen kommen reihum in den Gruppen A bis E, die Kippung wächst aber von Runde zu Runde; die Reihenfolge ist daher nicht vollständig gemischt.</p>")

    # 6 Driftmodelle
    A("<h2>6. Driftmodelle</h2>")
    A(f'<div class="tw big">{t_models}</div>')
    A("<p>RMS-Gewinn gegenüber dem starren Modell (Brettdrift voll, Halterungsdrift hell) und Betrag der Brettdrift über die Sitzung.</p>")
    A(sess_legend(S))
    A(grid([cell("RMS-Gewinn (%)", svg_gain), cell("Brettdrift über die Sitzung (mm)", svg_dmm)]))
    A(f"<p>Die lineare Brettdrift erklärt nach dem Aufwärmen nur {n(gw[0], 0)} % (S3) und {n(gw[1], 0)} % (S4) des RMS, in S1 (kalt) {n(gc[0], 0)} %, in S2 (kalt) {n(gc[1], 0)} %. "
      f"Der Driftbetrag liegt bei {n(s3['models']['boardDrift']['driftNormMm'], 2)} mm (S3) und {n(s4['models']['boardDrift']['driftNormMm'], 2)} mm (S4) "
      f"gegen {n(s1['models']['boardDrift']['driftNormMm'], 2)} mm (S1) und {n(s2['models']['boardDrift']['driftNormMm'], 2)} mm (S2); "
      f"in S4 steckt er vor allem in z ({n(s4['models']['boardDrift']['driftMm'][2], 2)} mm). "
      "Die große Drift aus S1 ist nach dem Aufwärmen nicht wieder aufgetreten, S2 (kalt) hatte aber ebenfalls nur einen kleinen Gewinn. "
      "Aufwärmen ist damit nicht eindeutig als Ursache belegt: zwei kalte Sitzungen mit sehr verschiedenem Verhalten stehen zwei warmen gegenüber.</p>")

    # 7 Brettlage-Streuung
    A("<h2>7. Brettlage-Streuung je Pose</h2>")
    A("<p>Abweichung der Brettlage je Pose von der gemittelten Brettlage (Endlösung), in mm. Box: Quartile, Strich: Median; Punkte sind einzelne Posen.</p>")
    A(sess_legend(S))
    A(f'<div class="one">{svg_bd}</div>')
    A("<p>Je Gruppe (A bis E) und Sitzung:</p>")
    A(svg_bdg)
    A(f'<div class="tw big">{t_bd}</div>')
    A(f"<p>Der Median der Brettlage-Abweichung liegt bei {n(s1['boardDevMm']['median'], 2)} / {n(s2['boardDevMm']['median'], 2)} mm (kalt) und "
      f"{n(s3['boardDevMm']['median'], 2)} / {n(s4['boardDevMm']['median'], 2)} mm (warm), das 90. Perzentil bei "
      f"{n(s1['boardDevMm']['p90'], 2)} / {n(s2['boardDevMm']['p90'], 2)} und {n(s3['boardDevMm']['p90'], 2)} / {n(s4['boardDevMm']['p90'], 2)} mm. "
      f"In allen vier Sitzungen hat Gruppe E die größte Gruppenmedian-Abweichung ({n(s1['boardDevMedianByGroup']['E'], 2)}, {n(s2['boardDevMedianByGroup']['E'], 2)}, "
      f"{n(s3['boardDevMedianByGroup']['E'], 2)}, {n(s4['boardDevMedianByGroup']['E'], 2)} mm), unabhängig vom Aufwärmen; "
      "die übrigen Gruppen liegen zwischen "
      f"{n(min(v for s in (s1, s2, s3, s4) for g, v in s['boardDevMedianByGroup'].items() if g != 'E'), 2)} und "
      f"{n(max(v for s in (s1, s2, s3, s4) for g, v in s['boardDevMedianByGroup'].items() if g != 'E'), 2)} mm.</p>")

    # 8 Folgerungen
    A("<h2>8. Was daraus folgt</h2>")
    A('<div class="fazit">')
    A(f"<p>Das 0,5-mm-Ziel bleibt {k['target05mm']}. Die paarweisen Abstände der vier Lösungen reichen von {n(k['minPairMm'], 2)} bis {n(k['spanMaxPairMm'], 2)} mm "
      f"(warm untereinander {n(k['warmPairMm'], 2)} mm, kalt untereinander {n(k['coldPairMm'], 2)} mm); S2 liegt nur {n(cross['S2-S3'], 2)} und {n(cross['S2-S4'], 2)} mm von S3 und S4 entfernt, "
      f"S1 ({n(cross['S1-S3'], 2)} und {n(cross['S1-S4'], 2)} mm) ist der Ausreißer. "
      f"Der Zeiteffekt im Fehler je Pose bleibt (Faktor {n(s3['halfRatio'], 1)} und {n(s4['halfRatio'], 1)} zwischen 2. und 1. Hälfte nach dem Aufwärmen).</p>")
    A("</div>")
    A("<p>Nächste Schritte (brauchen Roboterfahrt, erst nach Freigabe):</p>")
    A("<ol>"
      f"<li>Echte Zufallsreihenfolge der Posen, damit die Kippung nicht mit der Zeit korreliert (jetzt S3 ρ = {n(s3['spearmanTVsTilt'], 2)}, S4 ρ = {n(s4['spearmanTVsTilt'], 2)}).</li>"
      "<li>Eine feste Referenzpose alle etwa 10 Posen erneut anfahren; die Abweichung zwischen den Wiederholungen misst die Drift direkt, ohne Modell.</li>"
      "<li>Prüfpunkt Gelenkwinkel: Eine Wiederholgenauigkeitsmessung vom selben Tag zeigt eine bleibende Regelabweichung der Handgelenke 5 und 7 von etwa 5 bis 6 mrad "
      "(Messung nicht Teil dieser Daten). Für Hand-Auge zählt die gemeldete Ist-Pose; solange <code>robot_state.json</code> die Ist-Pose enthält und nicht den Sollwert, "
      "ist das kein Fehler. Das ist zu prüfen, nicht ein Befund.</li>"
      "<li>Nach der nächsten Aufnahme <code>analyse.py</code>, <code>drift.py</code> und diesen Bericht erneut erzeugen und vergleichen.</li>"
      "</ol>")

    # 9 Methode
    A("<h2>9. Methode und Daten</h2>")
    A('<div class="klein">')
    A("<p>Alle Zahlen stammen aus <code>doc/auswertung/handeye_cv4/</code> (nur gelesen). "
      "Endlösung: Reprojektions-Verfeinerung (<code>finalSolution</code> in <code>&lt;sitzung&gt;.json</code>). "
      "Abstände: euklidischer Abstand der Translationen beziehungsweise Drehwinkel der Relativrotation. "
      "Sweep: <code>sweep</code> in <code>drift.json</code> (starres Modell, D-optimale Teilmengen), "
      "kumulativ: <code>temporal.cumulative</code> in <code>&lt;sitzung&gt;.json</code>. "
      "Hälften: erste und zweite Hälfte der gültigen Posen in Aufnahmereihenfolge. "
      "Spearman über Ränge; Teilkorrelation: Ränge von RMS und Zeit werden auf [1, Rang(Kippung), Rang(Abstand)] regressiert, "
      "korreliert werden die Residuen. Brettlage-Abweichung: Spalte <code>boardDevMm</code> der Posen-CSV. "
      "Gewinn = 1 − RMS(Modell)/RMS(starr).</p>")
    A("<p>Befehle (Repo-Wurzel, separate Umgebung mit OpenCV 4.14):</p>")
    cmds = []
    for s in S:
        cmds.append(f"PYTHONPATH=src .venv/bin/python tools/handeye_offline_cv4/analyse.py --session data/rohbilder/calibration/{s['id']}")
    for s in S:
        cmds.append(f"PYTHONPATH=src .venv/bin/python tools/handeye_offline_cv4/drift.py --session data/rohbilder/calibration/{s['id']}")
    cmds.append("PYTHONPATH=src .venv/bin/python tools/handeye_offline_cv4/warm_report.py")
    A("<p>" + "<br>".join(f"<code>{escape(c)}</code>" for c in cmds) + "</p>")
    A("<p>Sitzungen:<br>" + "<br>".join(
        f"{s['short']}: <code>{s['id']}</code> ({s['state']}, {s['board']}), "
        f"<code>doc/auswertung/handeye_cv4/{s['id']}_posen.csv</code>" for s in S) + "</p>")
    A("<p>Kennzahlen: <code>doc/auswertung/handeye_cv4/warm_vergleich.json</code>. "
      "Diagramme: matplotlib, als Inline-SVG; Farben folgen den Hell-/Dunkel-Tokens der Seite.</p>")
    A("</div></div>")

    out_path.write_text("\n".join(o), encoding="utf-8")
    JSON_OUT.write_text(json.dumps(K, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"geschrieben: {out_path} ({out_path.stat().st_size} Bytes), {JSON_OUT}")
    return K


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    out = args.out if args.out.is_absolute() else Path.cwd() / args.out
    build(out)


if __name__ == "__main__":
    main()

"""Berichtsgenerator Roboter-Wiederholgenauigkeit (ISO 9283), ein eigenstaendiges HTML.

Aufruf (aus dem Repo-Wurzelverzeichnis):
    PYTHONPATH=src .venv/bin/python tools/wiederholgenauigkeit/bericht.py \
        [--daten data/rohbilder/roboterwiderholgenauigkeit] \
        [--out doc/auswertung/roboter_wiederholgenauigkeit.html]

Liest nur die Bild-JSONs (Live-Messung + Client-Record). Neben dem HTML entsteht eine
JSON mit allen Kennzahlen. Abweichend von der Standardauswertung (`repeatability_eval`):
Laeufe werden getrennt, Bilder nach `settled`/`movedDuringCapture`/Ecken gefiltert und
Zyklen als Burst-Mittel gebildet.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from repeatability_eval.metrics import repeatability

PHASEN = [
    ("warmlauf", "Warmlauf"),
    ("rauschen", "Rauschen"),
    ("zyklen_auto", "Auto-Zyklen"),
    ("zyklen_hand", "Hand-Zyklen"),
]
PHASENAME = dict(PHASEN)
SOLL_ECKEN = 60  # innere ChArUco-Ecken 11x7
P_TOL_MM = 5.0  # Flansch weiter als das von P weg -> Aufnahme nicht an P
RAUSCH_MAX = 0.2  # gueltig, wenn RP_Rauschen / RP_Zyklen <= 0.2
LAUFFARBEN = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
GRAU = "#707b83"


# ----------------------------------------------------------------------------- Einlesen
def lade(daten: Path) -> list[dict]:
    rows = []
    for f in sorted(daten.glob("*/*/*.json")):
        if f.parent.name == "auswertung":
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("schema") != "adp.repeatability.image/1":
            continue
        c = d.get("client") or {}
        m = d.get("messung") or {}
        a = c.get("actual") or {}
        fl = (a.get("flangeInBase") or {}).get("position")
        tp = ((c.get("taught") or {}).get("P") or {}).get("flangeInBase", {}).get("position")
        ok = bool(m.get("ok"))
        ist_j = a.get("joints")
        soll_j = ((c.get("target") or {}).get("joints") or c.get("targetJoints")
                  or ((c.get("taught") or {}).get("P") or {}).get("joints"))
        dq = (np.array(ist_j, float) - np.array(soll_j, float)) * 1000 if ist_j and soll_j else None  # mrad
        rows.append(
            dict(
                datei=f"{f.parent.name}/{f.name}",
                sess=d["session"],
                phase=d["phase"],
                idx=int(d["index"]),
                t=datetime.fromisoformat(d["gespeichert"]),
                cyc=c.get("cycle"),
                burst=c.get("burstIndex"),
                ok=ok,
                n=int(m.get("nCorners") or 0) if ok else 0,
                xy=np.array(m["centerOnBoardMm"], float) if ok and m.get("centerOnBoardMm") else None,
                rot=m.get("rotationDeg") if ok else None,
                sc=m.get("scalePxPerMm") if ok else None,
                fl=np.array(fl, float) * 1000 if fl else None,
                P=np.array(tp, float) * 1000 if tp else None,
                settled=a.get("settled"),
                moved=a.get("movedDuringCapture"),
                speed=c.get("speed") or {},
                settings=c.get("settings") or {},
                warmupS=c.get("warmupElapsedS"),
                dq=dq,
                settleS=c.get("settleS"),
                burstCount=c.get("burstCount"),
            )
        )
    return rows


def laeufe_trennen(rows: list[dict]) -> None:
    """Neuer Lauf, sobald `cycle` kleiner wird als beim vorigen Bild derselben Phase/Session."""
    for (sess, ph) in sorted({(r["sess"], r["phase"]) for r in rows}):
        R = sorted((r for r in rows if r["sess"] == sess and r["phase"] == ph), key=lambda r: r["idx"])
        run, last = 0, None
        for r in R:
            if last is not None and r["cyc"] is not None and r["cyc"] < last:
                run += 1
            r["run"] = run
            if r["cyc"] is not None:
                last = r["cyc"]


def ausschlussgrund(r: dict) -> str | None:
    dP = None
    if r["fl"] is not None and r["P"] is not None:
        dP = float(np.linalg.norm(r["fl"] - r["P"]))
        r["dP"] = dP
    else:
        r["dP"] = None
    if r["settled"] is False or r["moved"]:
        return "Roboter in Bewegung"
    if dP is not None and dP > P_TOL_MM:
        return f"Aufnahme abseits von P (Vorpose V, {dP:.0f} mm entfernt, {r['n']} Ecken)"
    if not r["ok"]:
        return "Messung fehlgeschlagen"
    if r["n"] != SOLL_ECKEN:
        return f"nur {r['n']} Ecken"
    if r["settled"] is None:
        return "kein Beruhigungsstatus"
    return None


# ----------------------------------------------------------------------------- Kennzahlen
def rp_stat(P) -> dict | None:
    P = np.asarray(P, float)
    if len(P) < 2:
        return None
    r = repeatability(P)
    um = 1000.0
    return dict(
        rpUm=r["rp"] * um, lMeanUm=r["lMean"] * um, sLUm=r["sL"] * um,
        stdPerAxisUm=[s * um for s in r["stdPerAxis"]], centroidMm=r["centroid"], n=r["n"],
    )


def zyklen(good: list[dict]) -> list[dict]:
    """Burst-Mittel je (Lauf, cycle)."""
    gr: dict = {}
    for r in good:
        gr.setdefault((r["sess"], r["run"], r["cyc"]), []).append(r)
    out = []
    for (sess, run, cyc), v in gr.items():
        sd = None
        if len(v) > 1:
            sd = float(np.linalg.norm(np.std([r["xy"] for r in v], axis=0, ddof=1))) * 1000
        out.append(
            dict(
                sess=sess, run=run, cyc=cyc, nBilder=len(v), t=v[0]["t"],
                xy=np.mean([r["xy"] for r in v], 0), fl=np.mean([r["fl"] for r in v], 0),
                rot=float(np.mean([r["rot"] for r in v])), sc=float(np.mean([r["sc"] for r in v])),
                P=v[0]["P"], burstSdUm=sd,
                dq=np.mean([r["dq"] for r in v], 0) if all(r["dq"] is not None for r in v) else None,
            )
        )
    out.sort(key=lambda z: (z["t"]))
    return out


def gruppe_stat(Z: list[dict]) -> dict:
    """Kennzahlen einer Zyklengruppe (Kamera 2D, Flansch 3D, Rotation, Massstab, Burst-Streuung)."""
    res: dict = dict(nZyklen=len(Z))
    if len(Z) < 2:
        return res
    cam = rp_stat([z["xy"] for z in Z])
    fl = rp_stat([z["fl"] for z in Z])
    rot = np.array([z["rot"] for z in Z])
    sc = np.array([z["sc"] for z in Z])
    bs = [z["burstSdUm"] for z in Z if z["burstSdUm"] is not None]
    res.update(
        kamera=cam, flansch=fl,
        rotationStdArcsec=float(np.std(rot, ddof=1) * 3600),
        massstabRelStdPpm=float(np.std(sc, ddof=1) / np.mean(sc) * 1e6),
        burstSigmaMedianUm=float(np.median(bs)) if bs else None,
    )
    return res


# ----------------------------------------------------------------------------- Plot-Helfer
def stil() -> None:
    plt.rcParams.update({
        "svg.fonttype": "none", "font.family": ["DejaVu Sans", "sans-serif"],
        "font.size": 9.5, "text.color": GRAU, "axes.labelcolor": GRAU, "xtick.color": GRAU,
        "ytick.color": GRAU, "axes.edgecolor": GRAU, "axes.facecolor": "none", "figure.facecolor": "none",
        "savefig.facecolor": "none", "savefig.transparent": True, "grid.color": GRAU, "grid.alpha": 0.25,
        "axes.grid": True, "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
        "legend.labelcolor": GRAU, "svg.hashsalt": "x",
    })


def svg(fig, name: str, alt: str, maxw: int = 1000) -> str:
    plt.rcParams["svg.hashsalt"] = name
    buf = io.StringIO()
    fig.savefig(buf, format="svg")
    plt.close(fig)
    s = buf.getvalue()
    s = s[s.index("<svg"):]
    s = re.sub(r"<svg([^>]*?)\swidth=\"[^\"]*\"\s+height=\"[^\"]*\"", rf'<svg\1 role="img" aria-label="{html.escape(alt)}" style="width:100%;max-width:{maxw}px;height:auto"', s, count=1)
    return s


def run_label(ph: str, run: int) -> str:
    return f"{PHASENAME[ph]} Lauf {run + 1}"


def kreis(ax, r, **kw):
    ax.add_patch(plt.Circle((0, 0), r, fill=False, color=GRAU, lw=1.2, ls="--", **kw))


def streu(ax, groups, rp_um, title, unit="µm", scale=1000.0):
    """groups: Liste (Label, Farbe, Nx2-Array in mm relativ), Kreis mit RP-Radius."""
    for lab, col, A in groups:
        ax.scatter(A[:, 0] * scale, A[:, 1] * scale, s=26, color=col, label=lab, edgecolor="none", alpha=.9)
    if rp_um:
        kreis(ax, rp_um)
    ax.axhline(0, color=GRAU, lw=.6); ax.axvline(0, color=GRAU, lw=.6)
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel(f"Δx [{unit}]"); ax.set_ylabel(f"Δy [{unit}]"); ax.set_title(title, fontsize=10, color=GRAU)


# ----------------------------------------------------------------------------- Plots
def plots(ctx: dict) -> dict[str, str]:
    stil()
    out = {}
    auto = ctx["auto_z"]; runs_auto = sorted({z["run"] for z in auto})
    cm = np.mean([z["xy"] for z in auto], 0); fm = np.mean([z["fl"] for z in auto], 0)
    A = ctx["auto_gesamt"]

    # 1 Streudiagramm Auto
    fig, axs = plt.subplots(1, 2, figsize=(10, 4.8))
    for ax, key, mean, rp, titel in [
        (axs[0], "xy", cm, A["kamera"]["rpUm"], f"Kamera auf der Brettebene, RP {A['kamera']['rpUm']:.0f} µm"),
        (axs[1], "fl", fm, A["flansch"]["rpUm"], f"Flansch laut Roboter (x/y), RP 3D {A['flansch']['rpUm']:.0f} µm"),
    ]:
        gr = [(f"Lauf {r + 1}", LAUFFARBEN[r % 5], np.array([z[key][:2] - mean[:2] for z in auto if z["run"] == r])) for r in runs_auto]
        streu(ax, gr, rp, titel)
    axs[0].legend(loc="upper left", fontsize=8, ncol=2)
    fig.tight_layout()
    out["streu_auto"] = svg(fig, "p1", "Streudiagramme der Auto-Zyklen, Kamera und Flansch")

    # 2 Zeitverlauf Kamera
    ref = np.mean([r["xy"] for r in ctx["auto_img"]], 0)
    fig, axs = plt.subplots(2, 1, figsize=(10, 5.4), sharex=True)
    for ph, marker in [("warmlauf", "s"), ("rauschen", "^"), ("zyklen_auto", "o")]:
        for run in sorted({r["run"] for r in ctx["gut"] if r["phase"] == ph and r["sess"] == ctx["sess1"]}):
            G = [r for r in ctx["gut"] if r["phase"] == ph and r["run"] == run and r["sess"] == ctx["sess1"]]
            t = [r["t"] for r in G]
            col = LAUFFARBEN[run % 5]
            for k, ax in enumerate(axs):
                ax.plot(t, [(r["xy"][k] - ref[k]) * 1000 for r in G], marker, color=col, ms=4,
                        label=f"{PHASENAME[ph]} {run + 1}" if k == 0 else None)
    for k, ax in enumerate(axs):
        ax.set_ylabel(f"Δ{'xy'[k]} zum Auto-Mittel [µm]"); ax.axhline(0, color=GRAU, lw=.6); ax.margins(y=0.15)
    axs[1].set_xlabel("Uhrzeit (Sitzung 1)")
    axs[1].xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M"))
    axs[0].legend(ncol=5, fontsize=7.5, loc="lower center", bbox_to_anchor=(.5, 1.12))
    fig.tight_layout()
    out["zeit"] = svg(fig, "p2", "Kameraposition über der Uhrzeit, Läufe markiert")

    # 3 Rauschen Lauf 1
    N = ctx["rausch_l1_img"]
    Pn = np.array([r["xy"] for r in N]); Pn = (Pn - Pn.mean(0)) * 1000
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.8), gridspec_kw=dict(width_ratios=[1, 1.5]))
    streu(axs[0], [("Bilder", LAUFFARBEN[0], Pn / 1000)], ctx["rausch_l1"]["kamera"]["rpUm"], f"{len(N)} Bilder, RP {ctx['rausch_l1']['kamera']['rpUm']:.1f} µm")
    for k, lab in enumerate("xy"):
        axs[1].plot(range(1, len(N) + 1), Pn[:, k], "o-", ms=3.5, lw=1, color=LAUFFARBEN[k], label=f"Δ{lab}")
    axs[1].axhline(0, color=GRAU, lw=.6); axs[1].set_xlabel("Bildnummer im Lauf"); axs[1].set_ylabel("Abweichung [µm]")
    axs[1].legend(fontsize=8); fig.tight_layout()
    out["rauschen"] = svg(fig, "p3", "Streuung der Rauschbilder von Lauf 1", 1000)

    # 4 Hand
    H = ctx["hand_z"]; Hs = ctx["hand_gesamt"]
    hc = np.mean([z["xy"] for z in H], 0); hf = np.mean([z["fl"] for z in H], 0)
    fig, axs = plt.subplots(1, 2, figsize=(10, 4.8))
    nfern = 0
    for ax, key, mean, rp, titel in [
        (axs[0], "xy", hc, Hs["kamera"]["rpUm"], f"Kamera, RP {Hs['kamera']['rpUm'] / 1000:.2f} mm"),
        (axs[1], "fl", hf, Hs["flansch"]["rpUm"], f"Flansch (x/y), RP 3D {Hs['flansch']['rpUm'] / 1000:.2f} mm"),
    ]:
        pts = np.array([z[key][:2] - mean[:2] for z in H])
        ax.scatter(pts[:, 0], pts[:, 1], s=26, color=LAUFFARBEN[0], label=f"verwendet ({len(pts)} Zyklen)", edgecolor="none")
        ex = [r[key][:2] - mean[:2] for r in ctx["hand_aus"] if r[key] is not None]
        ex = np.array(ex).reshape(-1, 2)
        lim = 3 * max(1.0, float(np.abs(pts).max()))
        nah = ex[np.max(np.abs(ex), axis=1) <= lim] if len(ex) else ex
        nfern = max(nfern, len(ex) - len(nah))
        if len(nah):
            ax.scatter(nah[:, 0], nah[:, 1], marker="x", s=30, color=LAUFFARBEN[1], label=f"ausgeschlossen ({len(nah)} im Ausschnitt)")
        kreis(ax, rp / 1000)
        ax.axhline(0, color=GRAU, lw=.6); ax.axvline(0, color=GRAU, lw=.6); ax.set_aspect("equal", adjustable="datalim")
        ax.set_xlabel("Δx [mm]"); ax.set_ylabel("Δy [mm]"); ax.set_title(titel, fontsize=10, color=GRAU)
    axs[0].legend(fontsize=8, loc="upper left"); fig.tight_layout()
    out["hand"] = svg(fig, "p4", "Hand-Zyklen: Streudiagramm Kamera und Flansch")
    ctx["hand_ausserhalb"] = nfern

    # 5 Balken
    lab, cam_v, fl_v = [], [], []
    for r in runs_auto:
        s = ctx["auto_laeufe"].get(r)
        if s and "kamera" in s:
            lab.append(f"Auto {r + 1}\n(n={s['nZyklen']})"); cam_v.append(s["kamera"]["rpUm"]); fl_v.append(s["flansch"]["rpUm"])
    lab.append(f"Auto\ngesamt (n={A['nZyklen']})"); cam_v.append(A["kamera"]["rpUm"]); fl_v.append(A["flansch"]["rpUm"])
    lab.append(f"Hand\n(n={Hs['nZyklen']})"); cam_v.append(Hs["kamera"]["rpUm"]); fl_v.append(Hs["flansch"]["rpUm"])
    x = np.arange(len(lab)); w = .38
    fig, ax = plt.subplots(figsize=(10, 4.4))
    b1 = ax.bar(x - w / 2, cam_v, w, color=LAUFFARBEN[0], label="Kamera (Brettebene, 2D)")
    b2 = ax.bar(x + w / 2, fl_v, w, color=LAUFFARBEN[3], label="Flansch (Roboter, 3D)")
    for b in list(b1) + list(b2):
        ax.annotate(f"{b.get_height():.0f}", (b.get_x() + b.get_width() / 2, b.get_height()), ha="center", va="bottom", fontsize=8, color=GRAU)
    nz = ctx["rausch_l1"]["kamera"]["rpUm"]
    ax.axhline(nz, color=LAUFFARBEN[1], lw=1.5, ls="--", label=f"Messrauschen {nz:.1f} µm")
    ax.set_yscale("log"); ax.set_ylabel("RP [µm], logarithmisch"); ax.set_xticks(x, lab); ax.legend(fontsize=8, loc="upper left")
    ax.grid(axis="x", visible=False); ax.set_ylim(top=max(cam_v + fl_v) * 2.2); fig.tight_layout()
    out["balken"] = svg(fig, "p5", "RP je Lauf, Kamera gegen Flansch, mit Rauschniveau")

    # 6 Versatz zu P
    fig, axs = plt.subplots(3, 1, figsize=(10, 6.2), sharex=True)
    Zs = [("Auto", auto, "o"), ("Hand", H, "s")]
    off = 0; zpos = {}
    for name, Z, mk in Zs:
        for k, ax in enumerate(axs):
            xs = np.arange(len(Z)) + off
            ax.plot(xs, [z["fl"][k] - z["P"][k] for z in Z], mk, ms=3.8, color=LAUFFARBEN[0] if name == "Auto" else LAUFFARBEN[1], label=name if k == 0 else None)
        off += len(Z) + 2
    for k, ax in enumerate(axs):
        ax.axhline(0, color=GRAU, lw=.6); ax.set_ylabel(f"Δ{'xyz'[k]} Ist − P [mm]")
    axs[2].set_xlabel("Zyklus (Auto, dann Hand)"); axs[0].legend(fontsize=8, ncol=2)
    fig.tight_layout()
    out["versatz_p"] = svg(fig, "p6", "Flansch-Ist minus eingelerntes P je Zyklus")

    # 7 Gelenkabweichung
    fig, axs = plt.subplots(1, 3, figsize=(10, 3.9), gridspec_kw=dict(width_ratios=[1, 1, 1.1]))
    for ax, j in [(axs[0], 6), (axs[1], 4)]:
        off = 0
        for name, Z, col in [("Auto", auto, LAUFFARBEN[0]), ("Hand", H, LAUFFARBEN[1])]:
            ax.plot(np.arange(len(Z)) + off, [z["dq"][j] for z in Z], "o", ms=3.5, color=col, label=name)
            off += len(Z) + 2
        ax.axhline(0, color=GRAU, lw=.6); ax.set_xlabel("Zyklus (Auto, dann Hand)"); ax.set_ylabel(f"Gelenk {j + 1}: Ist − Soll [mrad]")
        ax.set_title(f"Gelenk {j + 1}", fontsize=10, color=GRAU)
    axs[0].legend(fontsize=8)
    ai = ctx["auto_img"]
    rot = np.array([r["rot"] for r in ai]); d7 = np.array([r["dq"][6] for r in ai])
    for run in sorted({r["run"] for r in ai}):
        m = np.array([r["run"] == run for r in ai])
        axs[2].plot(d7[m], rot[m], "o", ms=3.5, color=LAUFFARBEN[run % 5], label=f"Lauf {run + 1}")
    axs[2].set_xlabel("Gelenk 7: Ist − Soll [mrad]"); axs[2].set_ylabel("Bilddrehung [°]"); axs[2].legend(fontsize=7.5)
    axs[2].set_title(f"Auto-Bilder, r = {np.corrcoef(d7, rot)[0, 1]:.2f}", fontsize=10, color=GRAU)
    fig.tight_layout()
    out["gelenk"] = svg(fig, "p7", "Gelenkabweichung Ist minus Soll für Gelenk 7 und 5, Bilddrehung gegen Gelenk 7")
    return out


# ----------------------------------------------------------------------------- Formatierung
def de(x, nd=1) -> str:
    if x is None:
        return "–"
    return f"{x:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def um_mm(um, nd=2) -> str:
    return de(um / 1000, nd)


def tab(head: list[str], rows: list[list], cls="") -> str:
    h = "".join(f"<th>{html.escape(str(c))}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw big {cls}"><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


CSS = """
:root{--bg:#f7f8f8;--surface:#ffffff;--fg:#15191c;--muted:#56616a;--grid:#e2e6e9;--accent:#1d5f8c}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#141617;--surface:#1c1f21;--fg:#e8ecee;--muted:#a3aeb6;--grid:#2c3236;--accent:#6aaee0;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#141617;--surface:#1c1f21;--fg:#e8ecee;--muted:#a3aeb6;--grid:#2c3236;--accent:#6aaee0;color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);margin:0;font:16px/1.55 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1032px;margin:0 auto;padding-block:28px 56px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
h1,h2,h3,h4{font-family:"IBM Plex Sans Condensed","Arial Narrow",system-ui,sans-serif;text-wrap:balance;line-height:1.2;margin:0}
h1{font-size:clamp(1.7rem,5vw,2.4rem);font-weight:600}
h2{font-size:1.4rem;font-weight:600;margin:2.6rem 0 .6rem;padding-top:1.2rem;border-top:1px solid var(--grid)}
.sub{color:var(--muted);margin:.4rem 0 0;max-width:70ch}
p,ul,ol{max-width:70ch;margin:.7rem 0}
li{margin:.35rem 0}
code,.num,td,th,.kz b{font-family:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace}
code{font-size:.88em;overflow-wrap:anywhere}
.num,td,th,.kz{font-variant-numeric:tabular-nums}
.kz{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr));gap:10px;margin:1.4rem 0 0}
.kz div{background:var(--surface);border-radius:6px;padding:.7rem .9rem;font-size:.88rem;color:var(--muted);line-height:1.35}
.kz b{display:block;color:var(--fg);font-size:1.3rem;font-weight:500;margin-bottom:.15rem}
figure{margin:1rem 0;max-width:1000px}
figcaption{color:var(--muted);font-size:.88rem;max-width:80ch;margin-top:.3rem}
svg text{font-family:"IBM Plex Sans",system-ui,sans-serif}
.tw{overflow-x:auto;margin-top:.6rem;max-width:1000px}
table{border-collapse:collapse;font-size:.8rem;white-space:nowrap}
th,td{padding:.25rem .5rem;text-align:right;border-bottom:1px solid var(--grid)}
th:first-child,td:first-child{text-align:left}
th{font-weight:500;color:var(--muted)}
.tw.txt td{text-align:left;white-space:normal}
.klein{color:var(--muted);font-size:.9rem}
.klein code{color:var(--fg)}
"""


# ----------------------------------------------------------------------------- Hauptteil
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--daten", default="data/rohbilder/roboterwiderholgenauigkeit")
    ap.add_argument("--out", default="doc/auswertung/roboter_wiederholgenauigkeit.html")
    args = ap.parse_args()
    daten, out = Path(args.daten), Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows = lade(daten)
    if not rows:
        raise SystemExit(f"Keine Bild-JSONs unter {daten}")
    laeufe_trennen(rows)
    for r in rows:
        r["grund"] = ausschlussgrund(r)
    gut = [r for r in rows if r["grund"] is None]
    aus = [r for r in rows if r["grund"] is not None]
    sess1 = min(r["sess"] for r in rows)

    # Laeufe
    keys = sorted({(r["sess"], r["phase"], r["run"]) for r in rows}, key=lambda k: min(r["t"] for r in rows if (r["sess"], r["phase"], r["run"]) == k))
    laufliste = []
    for (sess, ph, run) in keys:
        G = [r for r in rows if (r["sess"], r["phase"], r["run"]) == (sess, ph, run)]
        U = [r for r in G if r["grund"] is None]
        Z = zyklen(U)
        laufliste.append(dict(
            sess=sess, phase=ph, run=run, von=min(r["t"] for r in G), bis=max(r["t"] for r in G),
            nBilder=len(G), nVerwendet=len(U), nZyklen=len(Z), z=Z, stat=gruppe_stat(Z) if ph != "warmlauf" else {},
        ))

    def lauf(ph, run, sess=None):
        return next((l for l in laufliste if l["phase"] == ph and l["run"] == run and (sess is None or l["sess"] == sess)), None)

    ctx: dict = dict(gut=gut, sess1=sess1)
    auto_ls = [l for l in laufliste if l["phase"] == "zyklen_auto"]
    ctx["auto_z"] = sorted([z for l in auto_ls for z in l["z"]], key=lambda z: z["t"])
    ctx["auto_img"] = [r for r in gut if r["phase"] == "zyklen_auto"]
    ctx["auto_laeufe"] = {l["run"]: l["stat"] for l in auto_ls}
    ctx["auto_gesamt"] = A = gruppe_stat(ctx["auto_z"])
    hand_ls = [l for l in laufliste if l["phase"] == "zyklen_hand"]
    ctx["hand_z"] = sorted([z for l in hand_ls for z in l["z"]], key=lambda z: z["t"])
    ctx["hand_gesamt"] = Hs = gruppe_stat(ctx["hand_z"])
    ctx["hand_aus"] = [r for r in aus if r["phase"] == "zyklen_hand"]
    rl = [l for l in laufliste if l["phase"] == "rauschen"]
    # Rauschen: RP ueber die Einzelbilder (kein Burst-Mittel)
    def rausch_stat(l):
        imgs = [r for r in gut if (r["sess"], r["phase"], r["run"]) == (l["sess"], l["phase"], l["run"])]
        s = dict(nBilder=len(imgs))
        if len(imgs) >= 2:
            s["kamera"] = rp_stat([r["xy"] for r in imgs])
            if all(r["fl"] is not None for r in imgs):
                s["flansch"] = rp_stat([r["fl"] for r in imgs])
            s["rotationStdArcsec"] = float(np.std([r["rot"] for r in imgs], ddof=1) * 3600)
            s["massstabRelStdPpm"] = float(np.std([r["sc"] for r in imgs], ddof=1) / np.mean([r["sc"] for r in imgs]) * 1e6)
        s["mittelKameraMm"] = np.mean([r["xy"] for r in imgs], 0).tolist() if imgs else None
        s["mittelFlanschMm"] = np.mean([r["fl"] for r in imgs], 0).tolist() if imgs else None
        return s, imgs
    rausch = []
    for l in rl:
        s, imgs = rausch_stat(l)
        l["stat"] = s; rausch.append((l, s, imgs))
    ctx["rausch_l1"] = rausch[0][1]; ctx["rausch_l1_img"] = rausch[0][2]

    rp_rausch = ctx["rausch_l1"]["kamera"]["rpUm"]
    verh = rp_rausch / A["kamera"]["rpUm"]
    verh_fl = rp_rausch / A["flansch"]["rpUm"]
    gueltig = verh <= RAUSCH_MAX
    faktor = A["kamera"]["rpUm"] / rp_rausch
    # Lauf-zu-Lauf
    mittel_l = {l["run"]: np.mean([z["xy"] for z in l["z"]], 0) for l in auto_ls if l["z"]}
    mittel_f = {l["run"]: np.mean([z["fl"] for z in l["z"]], 0) for l in auto_ls if l["z"]}
    gm = np.mean([z["xy"] for z in ctx["auto_z"]], 0)
    versatz_l = {r: float(np.linalg.norm(m - gm) * 1000) for r, m in mittel_l.items()}
    paare = [(a, b, float(np.linalg.norm(mittel_l[a] - mittel_l[b]) * 1000)) for a in mittel_l for b in mittel_l if a < b]
    max_lauf_versatz = max(p[2] for p in paare) if paare else None
    # Rauschen Lauf 1 vs 2
    r_versatz_cam = r_versatz_fl_y = None
    if len(rausch) > 1:
        m1, m2 = rausch[0][1], rausch[1][1]
        r_versatz_cam = float(np.linalg.norm(np.array(m1["mittelKameraMm"]) - m2["mittelKameraMm"]))
        r_versatz_fl_y = (m1["mittelFlanschMm"][1], m2["mittelFlanschMm"][1])
    # Versatz Ist - P
    dP = np.array([z["fl"] - z["P"] for z in ctx["auto_z"]])
    dPh = np.array([z["fl"] - z["P"] for z in ctx["hand_z"]])
    dP_mean = dP.mean(0).tolist(); dP_std = dP.std(0, ddof=1).tolist()
    dPh_mean = dPh.mean(0).tolist()

    # Gelenkabweichung (mrad) je Phase, nur verwendete Bilder
    gelenk = {}
    for ph, _ in PHASEN:
        D = np.array([r["dq"] for r in gut if r["phase"] == ph and r["dq"] is not None])
        if len(D) >= 2:
            gelenk[ph] = dict(n=len(D), mittelMrad=D.mean(0).tolist(), sigmaMrad=D.std(0, ddof=1).tolist())
    ai = ctx["auto_img"]
    rot_a = np.array([r["rot"] for r in ai])
    gelenk_korr = [float(np.corrcoef(np.array([r["dq"][j] for r in ai]), rot_a)[0, 1]) for j in range(7)]
    jA = gelenk["zyklen_auto"]; jH = gelenk["zyklen_hand"]
    # Hand-Zyklen: Gelenk 7 endet je nach Anfahrseite unter- oder oberhalb des Solls (Totband).
    q7h = np.array([r["dq"][6] for r in gut if r["phase"] == "zyklen_hand" and r["dq"] is not None])
    hand_q7_seiten = dict(unter=dict(n=int((q7h < 0).sum()), mittelMrad=float(q7h[q7h < 0].mean()) if (q7h < 0).any() else None),
                          ueber=dict(n=int((q7h >= 0).sum()), mittelMrad=float(q7h[q7h >= 0].mean()) if (q7h >= 0).any() else None))
    jH["gelenk7Seiten"] = hand_q7_seiten

    # Drift innerhalb der Laeufe (lineare Regression ueber die Zeit, Einzelbilder)
    def regr(t, y):
        A_ = np.c_[t, np.ones_like(t)]
        k, c0 = np.linalg.lstsq(A_, y, rcond=None)[0]
        res = y - (k * t + c0)
        tot = np.sum((y - y.mean()) ** 2)
        return float(k), float(1 - np.sum(res ** 2) / tot) if tot > 0 else 0.0
    drift = []
    for l in laufliste:
        imgs = sorted([r for r in gut if (r["sess"], r["phase"], r["run"]) == (l["sess"], l["phase"], l["run"])], key=lambda r: r["t"])
        if len(imgs) < 3:
            continue
        t = np.array([(r["t"] - imgs[0]["t"]).total_seconds() / 60 for r in imgs])
        if t[-1] <= 0:
            continue
        e = dict(phase=l["phase"], lauf=l["run"] + 1, nBilder=len(imgs), dauerMin=float(t[-1]))
        for nm, key, ax_ in [("kameraX", "xy", 0), ("kameraY", "xy", 1), ("flanschX", "fl", 0), ("flanschY", "fl", 1), ("flanschZ", "fl", 2)]:
            y = np.array([r[key][ax_] for r in imgs]) * 1000
            k, r2 = regr(t, y)
            e[nm] = dict(umProMin=k, r2=r2)
        drift.append(e)

    # Rauschlauf 1 gegen Auto-Mittel
    ref_a = np.mean([r["xy"] for r in ai], 0)
    n1i = ctx["rausch_l1_img"]
    rausch_vs_auto = dict(
        kameraMm=float(np.linalg.norm(np.mean([r["xy"] for r in n1i], 0) - ref_a)),
        flanschMm=float(np.linalg.norm(np.mean([r["fl"] for r in n1i], 0) - np.mean([r["fl"] for r in ai], 0))),
        rotNoiseDeg=float(np.mean([r["rot"] for r in n1i])), rotAutoDeg=float(np.mean(rot_a)),
        gelenk7NoiseMrad=float(np.mean([r["dq"][6] for r in n1i])), gelenk7AutoMrad=float(jA["mittelMrad"][6]),
    )

    # Standardauswertung (nur Lesen)
    std = {}
    for p in sorted(daten.glob("*/auswertung/ergebnis.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        std[p.parent.parent.name] = dict(
            basisEcken=d.get("referenzEcken"), nBilder=d.get("nBilder"), nVerwendet=d.get("nVerwendet"),
            serien={k: {a: v.get(a) for a in ("n", "rpUm", "noiseRatio", "valid")} for k, v in (d.get("serien") or {}).items()},
        )

    # Einstellungen
    # Massgeblich sind die Einstellungen der Auto-Zyklen; Warmlauf und Rauschen haben burstCount 1.
    r0 = next((r for r in rows if r["phase"] == "zyklen_auto"), rows[0])
    sp = r0["speed"]; st = r0["settings"]
    setup = dict(speedFraction=sp.get("fraction"), maxSpeed=sp.get("maxSpeed"), settleS=r0["settleS"], burstCount=st.get("burstCount", r0["burstCount"]),
                 warmupMinutes=st.get("warmupMinutes"), noiseCount=st.get("noiseCount"), autoCycles=st.get("autoCycles"), handCycles=st.get("handCycles"))

    svgs = plots(ctx)

    # ------------------------------------------------------------ JSON
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if k not in ("z",)}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, np.ndarray):
            return clean(o.tolist())
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, datetime):
            return o.isoformat(timespec="seconds")
        return o
    ergebnis = clean(dict(
        schema="adp.repeatability.bericht/1", einstellungen=setup, regeln=dict(
            laufTrennung="cycle kleiner als beim vorigen Bild", bildVerwendet=f"ok, {SOLL_ECKEN} Ecken, settled, nicht movedDuringCapture, Flansch <= {P_TOL_MM} mm von P",
            zyklus="Burst-Mittel", rauschGrenze=RAUSCH_MAX),
        laeufe=[dict(phase=l["phase"], lauf=l["run"] + 1, sitzung=l["sess"], von=l["von"], bis=l["bis"], nBilder=l["nBilder"],
                     nVerwendet=l["nVerwendet"], nZyklen=l["nZyklen"], kennzahlen=l["stat"]) for l in laufliste],
        autoGesamt=A, handGesamt=Hs,
        messrauschen=dict(rpUm=rp_rausch, noiseRatioKamera=verh, noiseRatioFlansch=verh_fl, gueltig=gueltig, faktorFeiner=faktor,
                          versatzLauf1Lauf2KameraMm=r_versatz_cam, flanschYLauf1Lauf2Mm=r_versatz_fl_y),
        laufVersatz=dict(zumGesamtmittelUm=versatz_l, paareUm={f"{a + 1}-{b + 1}": v for a, b, v in paare}, maxUm=max_lauf_versatz),
        gelenkSollIst=dict(einheit="mrad, Ist - Soll, verwendete Bilder", phasen=gelenk, korrelationBilddrehungAuto=gelenk_korr),
        driftImLauf=drift, rauschenGegenAuto=rausch_vs_auto,
        versatzZuP=dict(autoMittelMm=dP_mean, autoStdMm=dP_std, handMittelMm=dPh_mean),
        standardauswertung=std,
        ausgeschlossen=[dict(datei=r["datei"], phase=r["phase"], zyklus=r["cyc"], burst=r["burst"], ecken=r["n"], grund=r["grund"], abstandPMm=r.get("dP")) for r in aus],
        bilder=dict(gesamt=len(rows), verwendet=len(gut), ausgeschlossen=len(aus)),
    ))
    jpath = out.with_suffix(".json")
    jpath.write_text(json.dumps(ergebnis, indent=2, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------ HTML
    def uhr(t): return t.strftime("%H:%M")
    def rp_cell(s, k="kamera"):
        return de(s[k]["rpUm"], 1) if s and k in s and s[k] else "–"
    H = []
    ad = H.append
    ad(f"<!doctype html><html lang='de'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
       "<title>Roboter-Wiederholgenauigkeit 09.11.2026</title>"
       '<link rel="preconnect" href="https://fonts.googleapis.com">'
       '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500&display=swap">'
       f"<style>{CSS}</style></head><body><div class='wrap'>")
    ad("<h1>Roboter-Wiederholgenauigkeit 09.11.2026</h1>")
    ad(f"<p class='sub'>Franka, Eye-in-Hand-Kamera (RealSense 640×480) auf ChArUco 11×7 (25 mm), Geschwindigkeit {de(sp.get('fraction', 0) * 100, 0)} % von {de(sp.get('maxSpeed'), 2)} "
       f"(speed.fraction {de(sp.get('fraction'), 0)}, maxSpeed {de(sp.get('maxSpeed'), 2)}), Beruhigung {de(setup['settleS'], 0)} s, Burst {setup['burstCount']}. Wiederholgenauigkeit nach ISO 9283 (RP = l̄ + 3·S<sub>l</sub>).</p>")
    kz = [
        (f"{de(A['kamera']['rpUm'], 0)} µm", f"RP Auto, Kamera ({A['nZyklen']} Zyklen)"),
        (f"{de(A['flansch']['rpUm'], 0)} µm", "RP Auto, Flansch laut Roboter (3D)"),
        (f"{de(rp_rausch, 1)} µm", "Messrauschen (Rauschlauf 1)"),
        (f"{de(verh, 3)}", f"Rauschverhältnis ({'gültig' if gueltig else 'ungültig'}, Grenze {de(RAUSCH_MAX, 1)})"),
        (f"{um_mm(Hs['kamera']['rpUm'], 1)} mm", f"RP Hand, Kamera ({Hs['nZyklen']} Zyklen)"),
    ]
    ad("<div class='kz'>" + "".join(f"<div><b>{a}</b>{b}</div>" for a, b in kz) + "</div>")

    dr1 = next((d for d in drift if d["phase"] == "zyklen_auto" and d["lauf"] == 1), drift[0])
    dr_ref = (dr1["kameraY"]["umProMin"], f"{PHASENAME[dr1['phase']]} Lauf {dr1['lauf']}")
    # Kurzfassung
    ad("<h2>Kurzfassung</h2><ul>")
    ad(f"<li><b>Messkette gültig:</b> Das Messrauschen liegt bei {de(rp_rausch, 1)} µm RP, rund {de(faktor, 0)}× feiner als die Streuung der Auto-Zyklen (Rauschverhältnis {de(verh, 3)}, Grenze {de(RAUSCH_MAX, 1)}).</li>")
    ad(f"<li><b>Auto-Zyklen (V→P):</b> RP {de(A['kamera']['rpUm'] / 1000, 2)} mm an der Kamera, {de(A['flansch']['rpUm'] / 1000, 2)} mm laut Roboter ({A['nZyklen']} Zyklen aus {len(auto_ls)} Läufen).</li>")
    ad(f"<li><b>Lauf-zu-Lauf:</b> Die Läufe unterscheiden sich im Mittel um bis zu {de(max_lauf_versatz / 1000, 2)} mm; innerhalb eines Laufs ist die RP kleiner ({de(min(l['stat']['kamera']['rpUm'] for l in auto_ls if 'kamera' in l['stat']) / 1000, 2)}–{de(max(l['stat']['kamera']['rpUm'] for l in auto_ls if 'kamera' in l['stat']) / 1000, 2)} mm Kamera).</li>")
    ad(f"<li><b>Hand-Zyklen:</b> RP {de(Hs['kamera']['rpUm'] / 1000, 1)} mm Kamera, {de(Hs['flansch']['rpUm'] / 1000, 1)} mm Flansch, also eine Größenordnung schlechter als V→P.</li>")
    ad(f"<li><b>Fester Versatz zu P:</b> Der Flansch steht reproduzierbar um z = {de(dP_mean[2], 2)} mm gegenüber dem eingelernten P (Streuung {de(dP_std[2] * 1000, 0)} µm).</li>")
    ad(f"<li><b>Bleibende Regelabweichung:</b> Der Roboter bleibt in den Auto-Zyklen systematisch vor dem Soll stehen, am stärksten Gelenk 5 und 7 ({de(jA['mittelMrad'][4], 1)} bzw. {de(jA['mittelMrad'][6], 1)} mrad); V→P ist dabei sehr reproduzierbar (σ höchstens {de(max(jA['sigmaMrad']), 1)} mrad). Bei Hand-Zyklen streut sie (σ Gelenk 7 {de(jH['sigmaMrad'][6], 1)} mrad, Gelenk 5 {de(jH['sigmaMrad'][4], 1)} mrad).</li>")
    ad(f"<li><b>Drift im Lauf:</b> Die Kamera sieht eine langsame Wanderung (z. B. {de(dr_ref[0], 0)} µm/min in y bei {dr_ref[1]}), die der Flansch nicht zeigt; das Vorzeichen wechselt zwischen den Läufen.</li>")
    ad(f"<li><b>Verworfene Bilder:</b> {len(aus)} von {len(rows)} Bildern ausgeschlossen (Tabelle unten).</li></ul>")

    # Ablauf
    ad("<h2>Messablauf und Läufe</h2>")
    ad("<p>Eine Phase kann aus mehreren Läufen bestehen (Abbruch und Neustart). Ein neuer Lauf beginnt, wenn der Zyklusindex kleiner wird als beim vorigen Bild.</p>")
    ad(tab(["Phase", "Lauf", "Sitzung", "von", "bis", "Bilder", "verwendet", "Zyklen"],
           [[PHASENAME[l["phase"]], l["run"] + 1, l["sess"], uhr(l["von"]), uhr(l["bis"]), l["nBilder"], l["nVerwendet"], l["nZyklen"]] for l in laufliste]))

    # Rauschen
    ad("<h2>Messrauschen</h2>")
    ad(f"<p>Der Roboter steht still, die Kamera nimmt {len(ctx['rausch_l1_img'])} Bilder nacheinander auf. Die RP dieser Streuung ist die Messunsicherheit der Kette (Rauschlauf 1): <b>{de(rp_rausch, 1)} µm</b>, σ je Achse {de(ctx['rausch_l1']['kamera']['stdPerAxisUm'][0], 1)} / {de(ctx['rausch_l1']['kamera']['stdPerAxisUm'][1], 1)} µm.</p>")
    ad(tab(["Lauf", "Bilder", "RP Kamera [µm]", "σx [µm]", "σy [µm]", "σ Rotation [″]", "σ Maßstab [ppm]", "Mittel Kamera [mm]", "Mittel Flansch y [mm]"],
           [[i + 1, s["nBilder"], de(s["kamera"]["rpUm"], 1) if "kamera" in s else "–",
             de(s["kamera"]["stdPerAxisUm"][0], 1) if "kamera" in s else "–", de(s["kamera"]["stdPerAxisUm"][1], 1) if "kamera" in s else "–",
             de(s.get("rotationStdArcsec"), 1), de(s.get("massstabRelStdPpm"), 0),
             f"{de(s['mittelKameraMm'][0], 2)} / {de(s['mittelKameraMm'][1], 2)}", de(s["mittelFlanschMm"][1], 2) if s["mittelFlanschMm"] else "–"]
            for i, (l, s, _) in enumerate(rausch)]))
    rv = rausch_vs_auto
    ad(f"<p>Rauschlauf 1 liegt selbst {de(rv['kameraMm'], 2)} mm (Kamera) neben dem Auto-Mittel, der Flansch nur {de(rv['flanschMm'], 2)} mm; die Bilddrehung beträgt {de(rv['rotNoiseDeg'], 3)}° gegen {de(rv['rotAutoDeg'], 3)}° (Auto). "
       f"Die Orientierung (Gelenk 7: Ist − Soll {de(rv['gelenk7NoiseMrad'], 1)} gegen {de(rv['gelenk7AutoMrad'], 1)} mrad) ist also eine andere. Das Rauschmaß ist davon nicht betroffen, es misst nur die Streuung an einer festen Stelle.</p>")
    ad(f"<figure>{svgs['rauschen']}<figcaption>Rauschlauf 1: Abweichung der Einzelbilder vom Mittel (links Brettebene, rechts über die Bildnummer).</figcaption></figure>")

    # Auto
    ad("<h2>Auto-Zyklen (ISO-RP)</h2>")
    ad("<p>Je Zyklus ein Burst-Mittel. Kamera: Position der Bildmitte auf der Brettebene (2D). Flansch: robotergemeldete Position in Basis-Koordinaten (3D). Die Achsen beider Systeme sind nicht direkt vergleichbar, nur die Beträge.</p>")
    rowsA = []
    for l in auto_ls:
        s = l["stat"]
        rowsA.append([f"Lauf {l['run'] + 1} ({uhr(l['von'])}–{uhr(l['bis'])})", l["nZyklen"], rp_cell(s), rp_cell(s, "flansch"),
                      de(s["kamera"]["stdPerAxisUm"][0], 1) + " / " + de(s["kamera"]["stdPerAxisUm"][1], 1) if "kamera" in s else "–",
                      de(s.get("rotationStdArcsec"), 0), de(s.get("massstabRelStdPpm"), 0), de(s.get("burstSigmaMedianUm"), 1)])
    rowsA.append([f"<b>gesamt</b>", A["nZyklen"], de(A["kamera"]["rpUm"], 1), de(A["flansch"]["rpUm"], 1),
                  de(A["kamera"]["stdPerAxisUm"][0], 1) + " / " + de(A["kamera"]["stdPerAxisUm"][1], 1), de(A["rotationStdArcsec"], 0), de(A["massstabRelStdPpm"], 0), de(A["burstSigmaMedianUm"], 1)])
    ad(tab(["Lauf", "Zyklen", "RP Kamera [µm]", "RP Flansch 3D [µm]", "σx / σy Kamera [µm]", "σ Rotation [″]", "σ Maßstab [ppm]", "Burst-σ Median [µm]"], rowsA))
    fs = A["flansch"]["stdPerAxisUm"]
    ad(f"<p>Gesamt: RP Kamera {de(A['kamera']['rpUm'], 1)} µm, RP Flansch {de(A['flansch']['rpUm'], 1)} µm. σ des Flanschs je Achse (x/y/z) {de(fs[0], 0)} / {de(fs[1], 0)} / {de(fs[2], 0)} µm. "
       f"Die Kamera sitzt mit Hebelarm vor dem Flansch, kleine Orientierungsfehler (σ Rotation {de(A['rotationStdArcsec'], 0)}″) vergrößern die seitliche Streuung der Kameraposition; "
       f"die Brettebene-Messung ist 2D, der Flansch 3D, ein Betragsvergleich ist deshalb nur grob. Maßstabsstreuung {de(A['massstabRelStdPpm'], 0)} ppm.</p>")
    ad(f"<figure>{svgs['streu_auto']}<figcaption>Auto-Zyklen als Abweichung vom Gesamtmittel, Farbe je Lauf. Gestrichelter Kreis: RP-Radius.</figcaption></figure>")

    # Lauf-zu-Lauf
    ad("<h2>Lauf-zu-Lauf-Versatz</h2>")
    ad(f"<p>Die Mittelpunkte der Läufe liegen {de(min(versatz_l.values()), 0)}–{de(max(versatz_l.values()), 0)} µm vom Gesamtmittel entfernt, der größte Abstand zweier Läufe beträgt {de(max_lauf_versatz, 0)} µm. Innerhalb eines Laufs ist die RP kleiner als im Gesamtkollektiv. "
       f"Ob zeitliche Drift oder Erwärmung die Ursache ist, lässt sich nicht trennen: der Warmlauf lief nur wenige Minuten statt der 30 min nach ISO 9283 (siehe Abschnitt Warmlauf).</p>")
    def dcell(e, k): return f"{de(e[k]['umProMin'], 0)} ({de(e[k]['r2'], 2)})"
    ad("<p>Drift innerhalb der Läufe: lineare Regression der Einzelbilder über die Zeit, Steigung in µm/min (in Klammern R²). Nur Läufe mit mindestens 3 Bildern.</p>")
    ad(tab(["Lauf", "Bilder", "Dauer [min]", "Kamera x", "Kamera y", "Flansch x", "Flansch y", "Flansch z"],
           [[f"{PHASENAME[e['phase']]} {e['lauf']}", e["nBilder"], de(e["dauerMin"], 1)] + [dcell(e, k) for k in ("kameraX", "kameraY", "flanschX", "flanschY", "flanschZ")] for e in drift]))
    ad("<p>Die Kamera sieht eine langsame Wanderung, die die Gelenkgeber nicht zeigen (Flansch-Steigungen klein, R² niedrig). Das Vorzeichen wechselt zwischen den Läufen, es ist also keine monotone Erwärmung. "
       "Mögliche Quellen (Kamera-Erwärmung, Strukturverformung, Brett) lassen sich nicht trennen.</p>")
    ad(f"<figure>{svgs['zeit']}<figcaption>Kameraposition (Einzelbilder) gegen die Uhrzeit, relativ zum Auto-Mittel. Sprünge zwischen den Farbgruppen sind Lauf-Versätze.</figcaption></figure>")
    if r_versatz_cam is not None:
        ad(f"<p>Rauschlauf 2 liegt {de(r_versatz_cam, 2)} mm (Kamera) neben Rauschlauf 1; der Flansch meldet in y {de(r_versatz_fl_y[0], 2)} gegenüber {de(r_versatz_fl_y[1], 2)} mm. "
           "Die Rauschserie bewegt den Roboter nicht, sie misst dort, wo er gerade steht. Nach dem Abbruch von Auto-Lauf 1 (letzte Aufnahme an V) "
           "stand der Roboter also an einer anderen Stelle als bei Rauschlauf 1; wie er dorthin kam, geht aus den Daten nicht hervor. "
           "Rauschlauf 2 taugt deshalb als Rauschmaß, nicht als Referenzlage.</p>")

    # Hand
    ad("<h2>Hand-Zyklen</h2>")
    ad(f"<p>Rückfahrt nach P aus beliebiger Lage. RP Kamera {de(Hs['kamera']['rpUm'] / 1000, 2)} mm, RP Flansch {de(Hs['flansch']['rpUm'] / 1000, 2)} mm ({Hs['nZyklen']} Zyklen), Burst-σ Median {de(Hs['burstSigmaMedianUm'], 1)} µm. "
       f"Die Rückfahrt nach P aus beliebiger Lage ist eine Größenordnung schlechter als V→P; die Restabweichung der Gelenke hängt von der Anfahrrichtung ab (σ Gelenk 7 {de(jH['sigmaMrad'][6], 1)} mrad, Gelenk 5 {de(jH['sigmaMrad'][4], 1)} mrad, siehe Abschnitt Gelenkwinkel). Das ist die belegte Hauptursache der Hand-RP: Haftreibung bzw. Regelabweichung der Handgelenke, richtungsabhängig. "
       f"{len(ctx['hand_aus'])} Bilder wurden ausgeschlossen (während Bewegung oder abseits von P).</p>")
    ad(f"<figure>{svgs['hand']}<figcaption>Hand-Zyklen, Abweichung vom Mittel in mm. Kreuze: ausgeschlossene Bilder{' (' + str(ctx['hand_ausserhalb']) + ' weitere liegen außerhalb des Ausschnitts)' if ctx['hand_ausserhalb'] else ''}.</figcaption></figure>")

    # Kamera vs Flansch
    ad("<h2>Kamera gegen Robotermeldung</h2>")
    ad(f"<p>Bei den Auto-Zyklen meldet der Roboter eine kleinere Streuung ({de(A['flansch']['rpUm'], 0)} µm) als die Kamera ({de(A['kamera']['rpUm'], 0)} µm). Die Kamera misst die Lage des Flansches plus Hebelarm und Orientierung, der Roboter nur die Gelenkwinkel-Vorwärtsrechnung. Gelenkgeber sehen eine mechanische Nachgiebigkeit nicht. Hinzu kommt die langsame Kameradrift im Lauf (Abschnitt Lauf-zu-Lauf-Versatz), die der Flansch nicht zeigt; sie trägt dazu bei, dass die Kamera-RP größer ist als die Flansch-RP.</p>")
    ad(f"<figure>{svgs['balken']}<figcaption>RP je Lauf, Kamera gegen Flansch. Gestrichelt: Messrauschen.</figcaption></figure>")

    # Gelenk
    ad("<h2>Gelenkwinkel: Soll gegen Ist</h2>")
    ad("<p>Je Bild <code>actual.joints − target.joints</code> (Fallback <code>targetJoints</code>, dann <code>taught.P.joints</code>) in mrad, nur verwendete Bilder. Mittel / σ je Gelenk.</p>")
    ad(tab(["Phase", "Bilder"] + [f"Gelenk {j}" for j in range(1, 8)],
           [[PHASENAME[ph], g["n"]] + [f"{de(g['mittelMrad'][j], 2)} / {de(g['sigmaMrad'][j], 2)}" for j in range(7)] for ph, g in gelenk.items()]))
    ad(f"<p>Korrelation Bilddrehung gegen Gelenkabweichung (Auto-Bilder), Gelenk 1–7: {', '.join(de(v, 2) for v in gelenk_korr)}. Am größten bei Gelenk 7 ({de(gelenk_korr[6], 2)}) und Gelenk 5 ({de(gelenk_korr[4], 2)}).</p>")
    ad(f"<p>Der Roboter bleibt systematisch vor dem Soll stehen (bleibende Regelabweichung, vor allem Handgelenke 5 und 7: {de(jA['mittelMrad'][4], 1)} bzw. {de(jA['mittelMrad'][6], 1)} mrad). V→P ist sehr reproduzierbar (σ höchstens {de(max(jA['sigmaMrad']), 1)} mrad). "
       f"Diese bleibende Abweichung erklärt den festen Versatz zu P. Bei den Hand-Zyklen hängt die Restabweichung von der Anfahrrichtung ab (σ Gelenk 7 {de(jH['sigmaMrad'][6], 1)} mrad, Gelenk 5 {de(jH['sigmaMrad'][4], 1)} mrad). "
       f"Gelenk 7 endet dabei in zwei Gruppen: {hand_q7_seiten['unter']['n']} Bilder bei im Mittel {de(hand_q7_seiten['unter']['mittelMrad'], 1)} mrad, "
       f"{hand_q7_seiten['ueber']['n']} Bilder bei {de(hand_q7_seiten['ueber']['mittelMrad'], 1)} mrad. Das Gelenk bleibt also je nach Anfahrseite vor oder hinter dem Soll stehen, "
       "ein Totband um den Sollwert. V→P kommt immer von derselben Seite und landet deshalb immer an derselben Stelle.</p>")
    ad(f"<figure>{svgs['gelenk']}<figcaption>Gelenk 7 und 5: Ist − Soll je Zyklus (Auto, dann Hand); rechts Bilddrehung gegen Gelenk-7-Abweichung der Auto-Bilder.</figcaption></figure>")

    # Versatz P
    ad("<h2>Versatz zum eingelernten P</h2>")
    ad(f"<p>Mittelwert Ist − P der Auto-Zyklen (x/y/z): {de(dP_mean[0], 2)} / {de(dP_mean[1], 2)} / {de(dP_mean[2], 2)} mm, σ {de(dP_std[0] * 1000, 0)} / {de(dP_std[1] * 1000, 0)} / {de(dP_std[2] * 1000, 0)} µm. "
       f"Hand: {de(dPh_mean[0], 2)} / {de(dPh_mean[1], 2)} / {de(dPh_mean[2], 2)} mm. Der Flansch steht reproduzierbar rund {de(abs(dP_mean[2]), 1)} mm tiefer als eingelernt: ein absoluter Fehler, keine Streuung. "
       "Die Ursache ist die bleibende Gelenkabweichung (siehe Abschnitt Gelenkwinkel: Soll gegen Ist). Für die Wiederholgenauigkeit unerheblich, für die Absolutgenauigkeit wichtig.</p>")
    ad(f"<figure>{svgs['versatz_p']}<figcaption>Flansch-Ist minus eingelerntes P (Basis-Koordinaten) je Zyklus.</figcaption></figure>")

    # Warmlauf
    ad("<h2>Warmlauf</h2>")
    wl = [l for l in laufliste if l["phase"] == "warmlauf"]
    ad(tab(["Lauf", "von", "bis", "Bilder", "verwendet"], [[l["run"] + 1, uhr(l["von"]), uhr(l["bis"]), l["nBilder"], l["nVerwendet"]] for l in wl]))
    wl_text = []
    for l in wl:
        G = [r for r in rows if r["phase"] == "warmlauf" and r["run"] == l["run"]]
        soll = G[0]["settings"].get("warmupMinutes")
        ist = max((r["warmupS"] or 0) for r in G)
        wl_text.append(f"Lauf {l['run'] + 1}: eingestellt {de(soll, 0)} min, letzte Aufnahme nach {de(ist / 60, 1)} min")
    ad(f"<p>{'; '.join(wl_text)} (ISO 9283: 30 min). Zusammen {sum(l['nVerwendet'] for l in wl)} verwendbare Bilder. "
       "Der Erwärmungseinfluss ist damit weder getrennt noch bewertbar.</p>")

    # Standardauswertung
    ad("<h2>Vergleich mit der Standardauswertung</h2>")
    srows = []
    s1 = std.get(sess1, {}).get("serien", {}); s2 = {k: v for d in std.values() for k, v in d["serien"].items()}
    def stdv(k): return s2.get(k, {})
    srows.append(["Rauschen", de(stdv("rauschen").get("rpUm"), 1), "–", de(rp_rausch, 1) + " (Lauf 1)", de(rausch[1][1]["kamera"]["rpUm"], 1) + " (Lauf 2)" if len(rausch) > 1 else "–"])
    srows.append(["Auto", de(stdv("zyklen_auto").get("rpUm"), 1), f"{de(stdv('zyklen_auto').get('noiseRatio'), 1)} ({'gültig' if stdv('zyklen_auto').get('valid') else 'ungültig'})", de(A["kamera"]["rpUm"], 1), f"Rauschverhältnis {de(verh, 3)}"])
    srows.append(["Hand", de(stdv("zyklen_hand").get("rpUm"), 1), "–", de(Hs["kamera"]["rpUm"], 1), ""])
    ad(tab(["Phase", "Standard RP [µm]", "Standard Rauschverh.", "Bericht RP Kamera [µm]", "Bemerkung"], srows))
    ad("<ul><li>Die Standardauswertung trennt Läufe nicht: Beide Rauschläufe liegen ~"
       f"{de(r_versatz_cam, 1) if r_versatz_cam else '–'} mm auseinander und werden gemeinsam ausgewertet, die Rausch-RP steigt auf {de(stdv('rauschen').get('rpUm'), 0)} µm und das Rauschverhältnis auf {de(stdv('zyklen_auto').get('noiseRatio'), 1)}: &quot;ungültig&quot;.</li>"
       "<li>Sie filtert Bewegungsbilder nicht über <code>settled</code> und <code>movedDuringCapture</code>. In der Hand-Sitzung ist die Basis nur 14 Ecken groß und Bewegungsbilder gehen in die Mittelung ein (RP "
       f"{de(stdv('zyklen_hand').get('rpUm') / 1000 if stdv('zyklen_hand').get('rpUm') else None, 1)} mm).</li>"
       "<li>Vorschlag: Lauf-Trennung und Bewegungsfilter in <code>session.py</code> übernehmen.</li></ul>")

    # Ausgeschlossen
    ad("<h2>Ausgeschlossene Bilder</h2>")
    nVl = [r for r in aus if r["phase"] == "zyklen_auto" and r["grund"].startswith("Aufnahme abseits")]
    nV = len(nVl); nVz = {(r["run"], r["cyc"]) for r in nVl}
    n56 = sum(1 for r in aus if r["phase"] == "zyklen_auto" and r["grund"].startswith("nur"))
    ad(f"<p>{len(aus)} von {len(rows)} Bildern. {nV} Auto-Bilder in {len(nVz)} Zyklen entstanden an der Vorpose V (0 Ecken, Lauf gestoppt), sie sind korrekt verworfen. "
       f"{n56} weitere Auto-Bilder haben nur {SOLL_ECKEN - 4} statt {SOLL_ECKEN} Ecken.</p>")
    ad(tab(["Datei", "Zyklus", "Burst", "Ecken", "Abstand P [mm]", "Grund"],
           [[html.escape(r["datei"]), r["cyc"], r["burst"], r["n"], de(r.get("dP"), 1), html.escape(r["grund"])] for r in aus], "txt"))

    # Folgerungen
    ad("<h2>Was daraus folgt</h2><ul>")
    ad(f"<li>Die Messkette ist für Werte im Bereich {de(A['kamera']['rpUm'], 0)} µm tauglich; Auflösung rund {de(rp_rausch, 0)} µm.</li>")
    ad("<li>Für ISO-9283-konforme Werte: Warmlauf auf 30 min, Läufe ohne Abbruch und stets über dieselbe Anfahrrichtung (V→P).</li>")
    ad("<li>Hand-Rückfahrten getrennt bewerten; sie gehören nicht in die V→P-Kennzahl.</li>")
    ad(f"<li>Der feste z-Versatz von {de(abs(dP_mean[2]), 1)} mm folgt aus der bleibenden Gelenkabweichung; für Absolutgenauigkeit berücksichtigen.</li>")
    ad("<li>Bleibende Regelabweichung der Handgelenke prüfen: Zielposition mit engerer Toleranz oder längerer Beruhigung anfahren, oder Ist-Gelenkwinkel statt Soll verwenden.</li>")
    ad("<li>Kameradrift im Lauf beobachten; für eine Trennung von Erwärmung und Mechanik Warmlauf auf 30 min und Kamera-Referenzmessung ohne Roboterbewegung einplanen.</li>")
    ad("<li><code>repeatability_eval</code> um Lauf-Trennung und Bewegungsfilter erweitern.</li></ul>")

    # Methode
    ad("<h2>Methode und Daten</h2>")
    ad(f"<p class='klein'>Daten: <code>{html.escape(str(daten))}</code> (Sitzungen {', '.join(sorted({r['sess'] for r in rows}))}), {len(rows)} Bilder. Gelesen werden nur die Bild-JSONs (<code>adp.repeatability.image/1</code>): Live-Messung unter <code>messung</code>, Client-Record unter <code>client</code>.</p>")
    ad("<ol class='klein'>"
       "<li>Läufe: innerhalb einer Phase beginnt ein neuer Lauf, wenn <code>cycle</code> kleiner wird als beim vorigen Bild.</li>"
       f"<li>Bild verwendet: <code>messung.ok</code>, {SOLL_ECKEN} Ecken, <code>actual.settled</code>, nicht <code>movedDuringCapture</code>, Flansch höchstens {de(P_TOL_MM, 0)} mm von P entfernt.</li>"
       "<li>Zyklus = Mittel der verwendeten Bilder desselben Laufs und Zyklus (Burst).</li>"
       "<li>RP = l̄ + 3·S<sub>l</sub> mit <code>repeatability_eval.metrics.repeatability</code>; Kamera 2D (Brettebene), Flansch 3D (mm).</li>"
       f"<li>Rauschverhältnis = RP Rauschlauf 1 / RP Auto-Zyklen; gültig bei höchstens {de(RAUSCH_MAX, 1)}. Rauschen wird über Einzelbilder gebildet.</li>"
       "<li>Rotations- und Maßstabsstreuung: Stichproben-σ der Zyklusmittel; Burst-σ: Median der Burst-Streuung (Betrag über x/y) je Zyklus.</li></ol>")
    ad("<p class='klein'>Aufruf: <code>PYTHONPATH=src .venv/bin/python tools/wiederholgenauigkeit/bericht.py [--daten …] [--out …]</code>. Kennzahlen zusätzlich in <code>" + html.escape(jpath.name) + "</code>.</p>")
    ad("</div></body></html>")
    out.write_text("\n".join(H), encoding="utf-8")
    print(f"HTML: {out}\nJSON: {jpath}")


if __name__ == "__main__":
    main()

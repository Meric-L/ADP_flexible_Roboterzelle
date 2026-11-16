"""Kinematik-Experiment (Hilfsmodul): nominelle Franka-Kinematik und Laden der Sitzungen.

T_base_flange im robot_state.json ist exakt fk(jointAnglesRad) mal Rz(-45 Grad), z -0,2 mm
(geprueft ueber alle Posen der vier Sitzungen vom 09.11.2026, Abweichung 0,000 mm).
"""
import json, sys
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from tagloc import geometry, lens
from tagloc.handeye import intrinsics_from_dict

ROOT = Path(__file__).resolve().parents[3]
C = ROOT / "data/rohbilder/calibration"
S = {"S1": "20261109T125050Z_a5c964f0", "S2": "20261109T125825Z_a580eaba",
     "S3": "20261109T142717Z_a7d466a1", "S4": "20261109T143834Z_6852cbc7"}
# Franka Panda, modifizierte DH (Craig): a, d, alpha je Gelenk, Flansch d=0.107
A = [0, 0, 0, 0.0825, -0.0825, 0, 0.088]
D = [0.333, 0, 0.316, 0, 0.384, 0, 0]
AL = [0, -np.pi/2, np.pi/2, np.pi/2, -np.pi/2, np.pi/2, np.pi/2]

def tf(a, d, al, th):
    ca, sa, ct, st = np.cos(al), np.sin(al), np.cos(th), np.sin(th)
    return np.array([[ct, -st, 0, a], [st*ca, ct*ca, -sa, -d*sa], [st*sa, ct*sa, ca, d*ca], [0, 0, 0, 1]])

def fk(q, dq=None, dd=None):
    T = np.eye(4)
    for i in range(7):
        T = T @ tf(A[i], D[i] + (dd[i] if dd is not None else 0), AL[i], q[i] + (dq[i] if dq is not None else 0))
    return T @ tf(0, 0.107, 0, 0)

def load(key):
    f = C / S[key]
    cfg = json.loads((f / "config.json").read_text())
    cal = intrinsics_from_dict(cfg["intrinsics"])
    det = json.loads((f / "results/detections_iterative.json").read_text())
    obs = []
    for v in det["valid"]:
        st = json.loads((f / "poses" / v["poseId"] / "robot_state.json").read_text())
        obs.append(dict(q=np.array(st["jointAnglesRad"]), T=np.array(st["T_base_flange"]),
                        uv=np.array(v["cornersPx"]), P=np.array(v["objectPointsM"]),
                        Tcb=np.array(v["T_camera_board"]), t=st["capturedAt"]))
    return obs, cal

def resid(obs, cal, X, B, dq=None, use_fk=False):
    out = []
    for o in obs:
        F = fk(o["q"], dq) if (use_fk or dq is not None) else o["T"]
        cb = geometry.invert(F @ X) @ B
        pr = lens.project_points(o["P"], geometry.rvec_from_rotation(cb[:3, :3]), cb[:3, 3], cal)
        out.append((pr - o["uv"]).reshape(-1))
    return np.concatenate(out)

def rms(r): return float(np.sqrt(np.mean(r.reshape(-1, 2) ** 2 * 2)))  # wie bisher: RMS je Ecke (Betrag)

if __name__ == "__main__":
    obs, cal = load("S4")
    o = obs[0]
    print("FK vs T_base_flange (pose_000):")
    print(np.round(fk(o["q"]), 4)); print(np.round(o["T"], 4))
    errs = [np.linalg.norm(fk(o["q"])[:3, 3] - o["T"][:3, 3]) * 1000 for o in obs]
    print("max |t_FK - t_rs| mm: %.4f" % max(errs))
    he = json.loads((ROOT / f"doc/auswertung/handeye_cv4/{S['S4']}.json").read_text())["finalSolution"]
    print(list(he.keys()))
    for key in S:
        obs, cal = load(key)
        E = [geometry.invert(fk(o["q"])) @ o["T"] for o in obs]
        Em = geometry.average_poses(E)
        dt = [np.linalg.norm((geometry.invert(Em) @ e)[:3, 3]) * 1000 for e in E]
        dr = [np.degrees(geometry.rotation_distance_rad(e, Em)) for e in E]
        print(key, "E t mm", np.round(Em[:3, 3] * 1000, 3), "rotz deg %.2f" % np.degrees(np.arctan2(Em[1, 0], Em[0, 0])),
              "Streuung t max %.3f mm, rot max %.4f deg" % (max(dt), max(dr)))

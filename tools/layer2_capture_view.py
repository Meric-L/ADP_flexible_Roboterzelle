"""Drei Rohbilder einer stillstehenden Roboteransicht in eine Versuchs-Session schreiben."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path

from asyncua import Client

SCHEMA = "adp.layer2.information-gain/1"


async def capture(args) -> None:
    session_path = args.session.resolve()
    session = json.loads(session_path.read_text(encoding="utf-8"))
    if session.get("schema") != SCHEMA:
        raise ValueError(f"Erwartet schema {SCHEMA}")
    flange = json.loads(args.pose_file.read_text(encoding="utf-8"))
    if set(flange) != {"position", "orientation"} or len(flange["position"]) != 3 or len(flange["orientation"]) != 4:
        raise ValueError("Pose-Datei muss position[3] und orientation[4] enthalten")
    trials = session.setdefault("trials", [])
    trial = next((item for item in trials if item.get("id") == args.trial), None)
    if trial is None:
        trial = {"id": args.trial, "views": []}
        trials.append(trial)
    if len(trial["views"]) != args.view - 1:
        raise ValueError("Ansichten eines Trials muessen in Reihenfolge 1, 2, 3 aufgenommen werden")
    images_dir = session_path.parent / "images"
    images_dir.mkdir(exist_ok=True)
    images = []
    targets = [images_dir / f"trial-{args.trial:03d}-view-{args.view}-frame-{frame}.png" for frame in range(1, 4)]
    for target in targets:
        if target.exists():
            raise FileExistsError(target)
    copied = []
    try:
        async with Client(args.endpoint) as client:
            machine = client.get_node(args.machine_node)
            method = client.get_node(args.method_node)
            for frame, target in enumerate(targets, 1):
                if frame > 1:
                    await asyncio.sleep(args.interval)
                reply = await machine.call_method(method)
                name, code = reply if isinstance(reply, (tuple, list)) else (None, None)
                if code != 0 or not name:
                    raise RuntimeError(f"SaveRawImage fehlgeschlagen: Error={code}, FileName={name}")
                source = Path(name)
                if not source.is_absolute():
                    source = args.repo_root / source
                if not source.is_file():
                    raise FileNotFoundError(f"Bild liegt nicht auf diesem Rechner: {source}")
                shutil.copy2(source, target)
                copied.append(target)
                images.append(target.relative_to(session_path.parent).as_posix())
    except Exception:
        for target in copied:
            target.unlink(missing_ok=True)
        raise
    trial["views"].append({"flangeInBase": flange, "images": images})
    temporary = session_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(session, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(session_path)
    print(f"Trial {args.trial}, Ansicht {args.view}: {len(images)} Bilder in {images_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--trial", type=int, required=True)
    parser.add_argument("--view", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--pose-file", type=Path, required=True, help="Ist-Flanschpose aus Ist-Gelenken per FK")
    parser.add_argument("--endpoint", required=True, help="opc.tcp://... des Hand-Pi")
    parser.add_argument("--machine-node", required=True, help="NodeId von VisionMachine")
    parser.add_argument("--method-node", required=True, help="NodeId von SaveRawImage")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Repo-Wurzel auf dem Hand-Pi")
    parser.add_argument("--interval", type=float, default=0.5, help="Sekunden zwischen Bildern")
    args = parser.parse_args()
    if args.trial < 1 or args.interval <= 0:
        parser.error("trial muss >= 1 und interval > 0 sein")
    asyncio.run(capture(args))


if __name__ == "__main__":
    main()

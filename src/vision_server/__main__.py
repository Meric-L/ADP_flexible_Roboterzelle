"""Kommandozeilen-Einstiegspunkt: python -m vision_server."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .config import DEFAULT_NODESET_PATH, DEFAULT_RECIPE_PROFILES, VisionServerConfig
from .runner import run

DEFAULT_PORT = 4841


def build_config(argv: list[str] | None = None) -> VisionServerConfig:
    """Liest die Kommandozeile und baut die Serverkonfiguration."""
    parser = argparse.ArgumentParser(prog="vision_server", description="OPC 40100 Vision-Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--endpoint")
    parser.add_argument("--app-uri", default=VisionServerConfig.application_uri)
    parser.add_argument("--name", default=VisionServerConfig.server_name)
    parser.add_argument("--namespace", default=VisionServerConfig.namespace_uri)
    parser.add_argument("--vision-system-name", default=VisionServerConfig.vision_system_name)
    parser.add_argument("--vision-system-id", default=VisionServerConfig.vision_system_id)
    parser.add_argument("--profile", help="erzwingt ein Profil fuer alle Rezepte")
    parser.add_argument("--latency", type=float, default=VisionServerConfig.detection_latency)
    parser.add_argument("--nodeset", type=Path, default=DEFAULT_NODESET_PATH)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    endpoint = args.endpoint or f"opc.tcp://0.0.0.0:{args.port}/vision/machine/"
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    return VisionServerConfig(
        endpoint=endpoint,
        application_uri=args.app_uri,
        server_name=args.name,
        namespace_uri=args.namespace,
        vision_system_name=args.vision_system_name,
        vision_system_id=args.vision_system_id,
        detection_latency=args.latency,
        nodeset_path=args.nodeset,
        recipe_profiles=(
            tuple((recipe, args.profile) for recipe, _ in DEFAULT_RECIPE_PROFILES)
            if args.profile
            else DEFAULT_RECIPE_PROFILES
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """Startet den Server; 0 bei sauberem Abbruch."""
    config = build_config(argv)
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

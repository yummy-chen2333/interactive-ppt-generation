#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from console_encoding import configure_utf8_stdio
from stage8_contract import compile_spec_lock_typography, validate_stage8_contract
from visual_assets.asset_manifest import AssetManifest
from visual_assets.retrieval_cache import atomic_write_json


def _sync_manifest(project: Path) -> dict:
    manifest = AssetManifest(project)
    manifest._save()
    return manifest.validation_report()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile and validate the canonical Stage 8 production contract.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("sync-manifest", "compile-typography", "validate"):
        child = commands.add_parser(command)
        child.add_argument("project", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    project = args.project.resolve()
    if args.command == "sync-manifest":
        report = _sync_manifest(project)
    elif args.command == "compile-typography":
        report = compile_spec_lock_typography(project)
    else:
        report = validate_stage8_contract(project)
        atomic_write_json(project / "validation" / "stage8-contract-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.command == "validate":
        return 0 if report["stage8_ready"] else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

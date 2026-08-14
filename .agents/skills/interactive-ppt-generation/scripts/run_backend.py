#!/usr/bin/env python3
"""
Interactive PPT Generation - Backend Runner

Run the bundled SVG checker, finalizer, native PPTX converter, and immutable
version publisher without invoking another Skill or web workflow.

Usage:
    python scripts/run_backend.py <project_path> [options]

Examples:
    python scripts/run_backend.py projects/demo --title demo --changed-pages 1-3

Dependencies:
    Install ../requirements.txt.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from console_encoding import configure_utf8_stdio
from workflow_state import WorkflowStateController, WorkflowStateError


_SCRIPTS_DIR = Path(__file__).resolve().parent
configure_utf8_stdio()


def _run(command: list[str]) -> None:
    """Run one backend command and stop on a non-zero exit code."""
    print("+ " + " ".join(command), file=sys.stderr)
    subprocess.run(command, check=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate SVG, export native PPTX, and publish a unique version."
    )
    parser.add_argument("project_path", type=Path)
    parser.add_argument("--title", help="Published PPTX filename title")
    parser.add_argument(
        "--changed-pages",
        default="",
        help="Comma-separated pages or ranges, for example 11 or 3,7-9",
    )
    parser.add_argument(
        "--format",
        choices=["ppt169", "ppt43"],
        help="Require a registered SVG canvas format",
    )
    parser.add_argument(
        "--skip-finalize",
        action="store_true",
        help="Skip svg_final preview generation; native export still reads svg_output",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    project = args.project_path.expanduser().resolve()
    if not project.is_dir():
        print(f"ERROR: Project directory does not exist: {project}", file=sys.stderr)
        return 2
    if not (project / "svg_output").is_dir():
        print(f"ERROR: Missing project SVG directory: {project / 'svg_output'}", file=sys.stderr)
        return 2
    if not (project / "spec_lock.md").is_file():
        print(f"ERROR: Missing export contract: {project / 'spec_lock.md'}", file=sys.stderr)
        return 2
    controller = WorkflowStateController(project)
    try:
        controller.assert_ready_for_backend()
    except WorkflowStateError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    build_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    temporary_pptx = project / "exports" / f"_conversion_{build_stamp}.pptx"
    temporary_pptx.parent.mkdir(parents=True, exist_ok=True)
    quality_command = [
        sys.executable,
        str(_SCRIPTS_DIR / "svg_quality_checker.py"),
        str(project),
        "--stage",
        "final",
        "--json",
    ]
    if args.format:
        quality_command.extend(["--format", args.format])

    try:
        _run(quality_command)
        if not args.skip_finalize:
            _run([
                sys.executable,
                str(_SCRIPTS_DIR / "finalize_svg.py"),
                str(project),
            ])
        export_command = [
            sys.executable,
            str(_SCRIPTS_DIR / "svg_to_pptx.py"),
            str(project),
            "--output",
            str(temporary_pptx),
        ]
        if args.format:
            export_command.extend(["--format", args.format])
        _run(export_command)

        publish_command = [
            sys.executable,
            str(_SCRIPTS_DIR / "publish_version.py"),
            str(project),
            str(temporary_pptx),
            "--changed-pages",
            args.changed_pages,
        ]
        if args.title:
            publish_command.extend(["--title", args.title])
        _run(publish_command)
        controller.close_gate("export")
        _run([
            sys.executable,
            str(_SCRIPTS_DIR / "final_acceptance_validator.py"),
            str(project),
        ])
        controller.close_gate("final_validation")
    except subprocess.CalledProcessError as error:
        return error.returncode or 1

    latest_path = project / "exports" / "latest.json"
    receipt = json.loads(latest_path.read_text(encoding="utf-8"))
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

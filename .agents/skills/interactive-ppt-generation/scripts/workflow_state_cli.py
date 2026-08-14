#!/usr/bin/env python3
"""
Interactive PPT Generation - Workflow State CLI

Usage:
    python scripts/workflow_state_cli.py status <project>
    python scripts/workflow_state_cli.py close <project> <gate>
    python scripts/workflow_state_cli.py resume <project>
    python scripts/workflow_state_cli.py user-assets <project> <none|scan>
    python scripts/workflow_state_cli.py assert-ready <project> backend

Dependencies:
    PyYAML and bundled Skill modules.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from console_encoding import configure_utf8_stdio
from workflow_state import GATE_ORDER, WorkflowStateController, WorkflowStateError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and transition Interactive PPT workflow state.")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="Compare state declarations with artifacts")
    status.add_argument("project", type=Path)
    close = commands.add_parser("close", help="Validate and atomically close one gate")
    close.add_argument("project", type=Path)
    close.add_argument("gate", choices=GATE_ORDER)
    close.add_argument("--route", choices=["reference", "free-design"])
    resume = commands.add_parser("resume", help="Reconcile stale state and return the true resume point")
    resume.add_argument("project", type=Path)
    assets = commands.add_parser("user-assets", help="Record an explicit no-assets decision or scan supplied files")
    assets.add_argument("project", type=Path)
    assets.add_argument("mode", choices=["none", "scan"])
    ready = commands.add_parser("assert-ready", help="Enforce a deterministic stage entry gate")
    ready.add_argument("project", type=Path)
    ready.add_argument("target", choices=["backend"])
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    controller = WorkflowStateController(args.project)
    try:
        if args.command == "status":
            report = controller.inspect()
            exit_code = 2 if report["state_stale"] else 0
        elif args.command == "close":
            report = controller.close_gate(args.gate, template_route=args.route)
            exit_code = 0
        elif args.command == "resume":
            report = controller.resume()
            exit_code = 0
        elif args.command == "user-assets":
            report = controller.record_user_assets(args.mode)
            exit_code = 0
        else:
            report = controller.assert_ready_for_backend()
            exit_code = 0
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    except WorkflowStateError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

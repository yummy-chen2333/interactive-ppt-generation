#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from config import load_prefixed_env_file
from console_encoding import configure_utf8_stdio
from visual_assets.asset_manifest import AssetManifest
from visual_assets.config import RetrievalConfig
from visual_assets.models import SlotRequirement
from visual_assets.pipeline import VisualAssetPipeline
from visual_assets.retrieval_cache import atomic_write_json
from visual_assets.verification import resolve_verification_mode


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY_PATH = SKILL_ROOT / "references" / "source-policy-profiles.yaml"


def _progress(event: dict[str, Any]) -> None:
    print(json.dumps(event, ensure_ascii=False, sort_keys=True), file=sys.stderr, flush=True)


def _host_vision(args: argparse.Namespace) -> str:
    return str(getattr(args, "host_native_vision", None) or "unknown").casefold()


def _load_overrides(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if args.config_json:
        config_path = Path(args.config_json).resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("--config-json must contain a JSON object")
        overrides.update(payload)
    mapping = {
        "query_budget": args.query_budget,
        "candidate_budget": args.candidate_budget,
        "download_budget": args.download_budget,
        "slot_deadline_seconds": args.slot_deadline,
        "good_enough_threshold": args.good_enough_threshold,
    }
    overrides.update({key: value for key, value in mapping.items() if value is not None})
    return overrides


async def _retrieve(args: argparse.Namespace) -> int:
    project_path = Path(args.project).resolve()
    requirements_path = (
        Path(args.requirements).resolve()
        if args.requirements
        else project_path / "research" / "visual-assets" / "visual-requirements.json"
    )
    if not project_path.is_dir():
        raise FileNotFoundError(f"project directory does not exist: {project_path}")
    if not requirements_path.is_file():
        raise FileNotFoundError(f"visual requirements do not exist: {requirements_path}")
    config = RetrievalConfig.from_env(_load_overrides(args))
    timeout = httpx.Timeout(
        connect=config.connect_timeout_seconds,
        read=config.read_timeout_seconds,
        write=config.operation_timeout_seconds,
        pool=config.connect_timeout_seconds,
    )
    limits = httpx.Limits(
        max_connections=max(4, config.download_concurrency * 2),
        max_keepalive_connections=max(2, config.download_concurrency),
    )
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        headers={"User-Agent": config.user_agent},
    ) as client:
        pipeline = VisualAssetPipeline(
            project_path,
            client,
            config=config,
            provider_names=[item.strip() for item in args.providers.split(",") if item.strip()],
            policy_path=Path(args.policy_config).resolve(),
            progress_callback=_progress,
            host_native_vision=_host_vision(args),
        )
        if args.slot:
            payload = json.loads(requirements_path.read_text(encoding="utf-8-sig"))
            deck = payload.get("deck") or {}
            matched = [item for item in payload.get("slots", []) if str(item.get("slot_id")) == args.slot]
            if not matched:
                raise ValueError(f"slot not found in requirements: {args.slot}")
            slot = SlotRequirement.from_dict(matched[0], deck)
            _write_derived_modes(requirements_path, pipeline, payload)
            preflight = await pipeline.preflight([slot], probe_network=True)
            result = await pipeline.run_slot(slot, preflight_checked=preflight["stage7_ready"])
            result["preflight"] = preflight
            unresolved = result.get("status") not in {"selected"} or not result.get("quality_gate_passed")
        else:
            payload = json.loads(requirements_path.read_text(encoding="utf-8-sig"))
            _write_derived_modes(requirements_path, pipeline, payload)
            result = await pipeline.run_deck(requirements_path)
            unresolved = any(
                item.get("status") != "selected" or not item.get("quality_gate_passed")
                for item in result.get("slots", [])
            )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if unresolved else 0


def _write_derived_modes(requirements_path: Path, pipeline: VisualAssetPipeline, payload: dict[str, Any]) -> None:
    deck = payload.get("deck") or {}
    changed = False
    for raw in payload.get("slots", []):
        slot = SlotRequirement.from_dict(raw, deck)
        decision = resolve_verification_mode(slot, pipeline.policy_resolver.select(slot))
        if raw.get("verification_mode") != decision.allowed_mode:
            raw["verification_mode"] = decision.allowed_mode
            changed = True
        if raw.get("verification_risk") != decision.risk:
            raw["verification_risk"] = decision.risk
            changed = True
        if raw.get("verification_policy_reason") != decision.reason:
            raw["verification_policy_reason"] = decision.reason
            changed = True
    if changed:
        atomic_write_json(requirements_path, payload)


async def _preflight(args: argparse.Namespace) -> int:
    project_path = Path(args.project).resolve()
    requirements_path = (
        Path(args.requirements).resolve()
        if args.requirements
        else project_path / "research" / "visual-assets" / "visual-requirements.json"
    )
    if not requirements_path.is_file():
        raise FileNotFoundError(f"visual requirements do not exist: {requirements_path}")
    config = RetrievalConfig.from_env(_load_overrides(args))
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(config.operation_timeout_seconds),
        follow_redirects=True,
        headers={"User-Agent": config.user_agent},
    ) as client:
        pipeline = VisualAssetPipeline(
            project_path,
            client,
            config=config,
            provider_names=[item.strip() for item in args.providers.split(",") if item.strip()],
            policy_path=Path(args.policy_config).resolve(),
            progress_callback=_progress,
            host_native_vision=_host_vision(args),
        )
        payload = json.loads(requirements_path.read_text(encoding="utf-8-sig"))
        _write_derived_modes(requirements_path, pipeline, payload)
        deck = payload.get("deck") or {}
        slots = [SlotRequirement.from_dict(item, deck) for item in payload.get("slots", [])]
        if args.slot:
            slots = [slot for slot in slots if slot.slot_id == args.slot]
            if not slots:
                raise ValueError(f"slot not found in requirements: {args.slot}")
        result = await pipeline.preflight(slots, probe_network=True)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["stage7_ready"] else 2


def _validate_manifest(args: argparse.Namespace) -> int:
    manifest = AssetManifest(Path(args.project).resolve())
    report = manifest.validation_report()
    result = {
        **report,
        "manifest": str(manifest.path),
        "items": len(manifest.payload.get("items", [])),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["stage7_ready"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Built-in bounded Visual Asset Retrieval Pipeline for Interactive PPT Generation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight", help="Audit environment and deck verification capabilities")
    preflight.add_argument("project", help="PPT project directory")
    preflight.add_argument("--requirements", help="Path to visual-requirements.json")
    preflight.add_argument("--slot", help="Audit only one slot_id")
    preflight.add_argument(
        "--providers",
        default="official-page,serper,bing,neurips-proceedings,internet-archive,met-museum,wikimedia",
        help="Ordered provider names",
    )
    preflight.add_argument("--policy-config", default=str(DEFAULT_POLICY_PATH), help="Source policy YAML")
    preflight.add_argument("--config-json", help="JSON file with RetrievalConfig overrides")
    preflight.add_argument("--query-budget", type=int)
    preflight.add_argument("--candidate-budget", type=int)
    preflight.add_argument("--download-budget", type=int)
    preflight.add_argument("--slot-deadline", type=float)
    preflight.add_argument("--good-enough-threshold", type=float)
    preflight.add_argument(
        "--host-native-vision",
        choices=["available", "unavailable", "unknown"],
        default="unknown",
        help="Truthful capability declaration by the current host Agent",
    )
    preflight.set_defaults(handler=lambda parsed: asyncio.run(_preflight(parsed)))

    retrieve = subparsers.add_parser("retrieve", help="Retrieve assets for a deck or one slot")
    retrieve.add_argument("project", help="PPT project directory")
    retrieve.add_argument("--requirements", help="Path to visual-requirements.json")
    retrieve.add_argument("--slot", help="Run only one slot_id")
    retrieve.add_argument(
        "--providers",
        default="official-page,serper,bing,neurips-proceedings,internet-archive,met-museum,wikimedia",
        help="Ordered provider names",
    )
    retrieve.add_argument("--policy-config", default=str(DEFAULT_POLICY_PATH), help="Source policy YAML")
    retrieve.add_argument("--config-json", help="JSON file with RetrievalConfig overrides")
    retrieve.add_argument("--query-budget", type=int)
    retrieve.add_argument("--candidate-budget", type=int)
    retrieve.add_argument("--download-budget", type=int)
    retrieve.add_argument("--slot-deadline", type=float)
    retrieve.add_argument("--good-enough-threshold", type=float)
    retrieve.add_argument(
        "--host-native-vision",
        choices=["available", "unavailable", "unknown"],
        default="unknown",
        help="Truthful capability declaration by the current host Agent",
    )
    retrieve.set_defaults(handler=lambda parsed: asyncio.run(_retrieve(parsed)))

    validate = subparsers.add_parser("validate-manifest", help="Validate asset-manifest.json")
    validate.add_argument("project", help="PPT project directory")
    validate.set_defaults(handler=_validate_manifest)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    load_prefixed_env_file(("SERPER_", "VISUAL_ASSET_"))
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "fatal", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

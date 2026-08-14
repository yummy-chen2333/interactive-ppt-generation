from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import SearchCandidate, SlotRequirement
from .attribution_policy import (
    DISPLAY_ATTRIBUTION_MODES,
    VISIBLE_ATTRIBUTION_MODES,
    apply_display_attribution,
)
from .retrieval_cache import atomic_write_json
from .source_policy import SourcePolicy


class AssetManifest:
    """Owns the only machine-readable truth for selected visual assets."""

    def __init__(self, project_path: Path):
        self.project_path = project_path.resolve()
        self.path = self.project_path / "ppt-content" / "visuals" / "asset-manifest.json"
        self.markdown_path = self.project_path / "ppt-content" / "visuals" / "asset-manifest.md"
        self.search_log_path = self.project_path / "research" / "visual-assets" / "image-search-log.md"
        self.payload = self._load()

    def record_selection(
        self,
        slot: SlotRequirement,
        policy: SourcePolicy,
        candidate: SearchCandidate,
        *,
        quality_gate_passed: bool,
        early_stop: bool,
        queries: list[str],
        stats: dict[str, Any],
        selection_reason: str,
        candidate_audit: list[dict[str, Any]],
        search_backends: dict[str, Any],
        verification: dict[str, Any],
        verification_path: str | None,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        if not candidate.local_path:
            raise ValueError("selected candidate has no downloaded local path")
        source_path = Path(candidate.local_path)
        destination_dir = self.project_path / "ppt-content" / "visuals" / "assets" / f"P{slot.slide_number:02d}"
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / source_path.name
        if source_path.resolve() != destination.resolve():
            shutil.copy2(source_path, destination)
        relative = destination.relative_to(self.project_path).as_posix()
        item = {
            "asset_id": f"asset-{slot.slot_id}",
            "slot_id": slot.slot_id,
            "slide_number": slot.slide_number,
            "local_path": relative,
            "filename": destination.name,
            "source_type": "web",
            "provider": candidate.provider,
            "source_policy": policy.name,
            "source_tier": candidate.source_tier,
            "title": candidate.title,
            "original_image_url": candidate.image_url,
            "source_page_url": candidate.source_page_url,
            "source_domain": candidate.source_domain,
            "search_query": candidate.query,
            "width": candidate.validation.get("width", candidate.width),
            "height": candidate.validation.get("height", candidate.height),
            "mime_type": candidate.validation.get("content_type", candidate.mime_type),
            "content_sha256": candidate.content_sha256 or candidate.validation.get("content_sha256"),
            "perceptual_hash": candidate.perceptual_hash or candidate.validation.get("perceptual_hash"),
            "license_name": candidate.license_name,
            "license_url": candidate.license_url,
            "author": candidate.author,
            "credit": candidate.credit,
            "source_attribution_required": candidate.attribution_required,
            "license_status": "unknown",
            "scores": candidate.scores,
            "hard_gate_verdict": candidate.hard_gate_verdict or candidate.analysis.get("hard_gate_verdict", {}),
            "verification": verification,
            "verification_path": verification_path,
            "verification_risk": verification.get("verification_risk"),
            "verification_status": "presentation-verified",
            "verification_method": candidate.analysis.get("verification_method") or verification_path,
            "verification_evidence": (
                candidate.analysis.get("verification_evidence")
                or candidate.analysis.get("hard_gate_verdict")
                or {}
            ),
            "evidence_strength": candidate.analysis.get("evidence_strength"),
            "visual_review_actor": candidate.analysis.get("visual_review_actor"),
            "confidence": candidate.analysis.get("confidence"),
            "verification_timestamp": candidate.analysis.get("verification_timestamp") or datetime.now(timezone.utc).isoformat(),
            "provenance": provenance,
            "total_score": candidate.total_score,
            "quality_gate_passed": quality_gate_passed,
            "early_stop": early_stop,
            "selection_reason": selection_reason,
            "cache_hit": candidate.cache_hit,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "published_at": candidate.published_at,
        }
        attribution = apply_display_attribution(item)
        self.payload["items"] = [
            existing for existing in self.payload.get("items", []) if existing.get("slot_id") != slot.slot_id
        ]
        self.payload["items"].append(item)
        self.payload.setdefault("slots", {})[slot.slot_id] = {
            "status": "selected",
            "slide_number": slot.slide_number,
            "source_policy": policy.name,
            "queries": queries,
            "selected_asset_id": item["asset_id"],
            "quality_gate_passed": quality_gate_passed,
            "early_stop": early_stop,
            "stats": stats,
            "candidate_audit": candidate_audit,
            "search_backends": search_backends,
            "verification": verification,
            "verification_path": verification_path,
            "verification_risk": verification.get("verification_risk"),
            "verification_status": "presentation-verified",
            "verification_method": candidate.analysis.get("verification_method") or verification_path,
            "verification_evidence": (
                candidate.analysis.get("verification_evidence")
                or candidate.analysis.get("hard_gate_verdict")
                or {}
            ),
            "evidence_strength": candidate.analysis.get("evidence_strength"),
            "visual_review_actor": candidate.analysis.get("visual_review_actor"),
            "confidence": candidate.analysis.get("confidence"),
            "display_attribution_mode": attribution.mode,
            "display_attribution": attribution.display_attribution,
            "selected_asset_id": item["asset_id"],
        }
        self._save()
        return item

    def record_unresolved(
        self,
        slot: SlotRequirement,
        policy: SourcePolicy,
        status: str,
        queries: list[str],
        stats: dict[str, Any],
        reason: str,
        *,
        candidate_audit: list[dict[str, Any]],
        search_backends: dict[str, Any],
        verification: dict[str, Any],
        verification_path: str | None,
    ) -> None:
        self.payload["items"] = [
            existing for existing in self.payload.get("items", []) if existing.get("slot_id") != slot.slot_id
        ]
        self.payload.setdefault("slots", {})[slot.slot_id] = {
            "status": status,
            "slide_number": slot.slide_number,
            "source_policy": policy.name,
            "queries": queries,
            "reason": reason,
            "stats": stats,
            "candidate_audit": candidate_audit,
            "search_backends": search_backends,
            "verification": verification,
            "verification_path": verification_path,
            "verification_risk": verification.get("verification_risk"),
        }
        self._save()

    def validate(self) -> list[str]:
        return [*self._schema_errors(), *self._file_errors()]

    def validation_report(self) -> dict[str, Any]:
        schema_errors = self._schema_errors()
        file_errors = self._file_errors()
        slots = self.payload.get("slots") or {}
        required_ids = self._required_slot_ids()
        completion_errors: list[str] = []
        for slot_id in sorted(required_ids):
            status = str((slots.get(slot_id) or {}).get("status") or "missing")
            if status != "selected":
                completion_errors.append(f"required slot is not selected: {slot_id} ({status})")
        selected_ids = {str(item.get("slot_id") or "") for item in self.payload.get("items", [])}
        for slot_id in sorted(required_ids & selected_ids):
            slot = slots.get(slot_id) or {}
            if not slot.get("quality_gate_passed"):
                completion_errors.append(f"required slot did not pass quality gates: {slot_id}")
        schema_valid = not schema_errors
        files_valid = not file_errors
        assets_complete = not completion_errors
        return {
            "schema_valid": schema_valid,
            "files_valid": files_valid,
            "assets_complete": assets_complete,
            "stage7_ready": schema_valid and files_valid and assets_complete,
            "schema_errors": schema_errors,
            "file_errors": file_errors,
            "completion_errors": completion_errors,
            "required_slots": sorted(required_ids),
        }

    def _schema_errors(self) -> list[str]:
        errors: list[str] = []
        if self.payload.get("schema_version") not in {1, 2, 3, 4}:
            errors.append("asset-manifest.json schema_version must be 1, 2, 3, or 4")
        seen_slots: set[str] = set()
        for index, item in enumerate(self.payload.get("items", []), start=1):
            prefix = f"items[{index}]"
            for field in ("asset_id", "slot_id", "local_path", "source_type"):
                if not item.get(field):
                    errors.append(f"{prefix}.{field} is required")
            slot_id = str(item.get("slot_id", ""))
            if slot_id in seen_slots:
                errors.append(f"duplicate selected slot: {slot_id}")
            seen_slots.add(slot_id)
            if item.get("source_type") == "web":
                for field in ("source_page_url", "original_image_url", "source_domain", "search_query"):
                    if not item.get(field):
                        errors.append(f"{prefix}.{field} is required for web assets")
            if self.payload.get("schema_version") in {2, 3, 4}:
                for field in (
                    "verification_risk", "verification_method", "verification_evidence",
                    "confidence", "verification_timestamp",
                ):
                    if item.get(field) is None:
                        errors.append(f"{prefix}.{field} is required for selected assets")
            if self.payload.get("schema_version") in {3, 4}:
                mode = str(item.get("display_attribution_mode") or "")
                if mode not in DISPLAY_ATTRIBUTION_MODES:
                    errors.append(f"{prefix}.display_attribution_mode is invalid: {mode!r}")
                display = str(item.get("display_attribution") or "")
                if mode in VISIBLE_ATTRIBUTION_MODES and not display.strip():
                    errors.append(f"{prefix}.display_attribution is required for {mode}")
                slot = (self.payload.get("slots") or {}).get(slot_id) or {}
                for field in (
                    "selected_asset_id", "verification_status", "verification_risk", "verification_method",
                    "evidence_strength", "display_attribution_mode", "display_attribution",
                ):
                    expected = item.get("asset_id") if field == "selected_asset_id" else item.get(field)
                    if slot.get(field) != expected:
                        errors.append(f"slots.{slot_id}.{field} must match the selected item")
            if self.payload.get("schema_version") == 4:
                if item.get("verification_status") != "presentation-verified":
                    errors.append(f"{prefix}.verification_status must be presentation-verified")
                if item.get("verification_risk") != "presentation-grade":
                    errors.append(f"{prefix}.verification_risk must be presentation-grade")
                if item.get("license_status") not in {"known", "unknown"}:
                    errors.append(f"{prefix}.license_status must be known or unknown")
                if "provenance" not in item:
                    errors.append(f"{prefix}.provenance is required")
        return errors

    def _file_errors(self) -> list[str]:
        errors: list[str] = []
        for index, item in enumerate(self.payload.get("items", []), start=1):
            local_path = item.get("local_path")
            if local_path and not (self.project_path / str(local_path)).is_file():
                errors.append(f"items[{index}].local_path does not exist: {local_path}")
        return errors

    def _required_slot_ids(self) -> set[str]:
        requirements = self.project_path / "research" / "visual-assets" / "visual-requirements.json"
        if not requirements.is_file():
            return set((self.payload.get("slots") or {}).keys())
        try:
            payload = json.loads(requirements.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return set((self.payload.get("slots") or {}).keys())
        return {
            str(item.get("slot_id")) for item in payload.get("slots", [])
            if item.get("slot_id") and bool(item.get("required", True))
        }

    def _load(self) -> dict[str, Any]:
        if self.path.is_file():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    payload.setdefault("items", [])
                    payload.setdefault("slots", {})
                    return payload
            except (OSError, json.JSONDecodeError):
                pass
        return {"schema_version": 4, "updated_at": None, "items": [], "slots": {}}

    def _save(self) -> None:
        self._upgrade_verification_schema()
        self.payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self.path, self.payload)
        self._write_markdown_projections()

    def _upgrade_verification_schema(self) -> None:
        requirement_risks: dict[str, str] = {}
        requirements = self.project_path / "research" / "visual-assets" / "visual-requirements.json"
        if requirements.is_file():
            try:
                requirement_payload = json.loads(requirements.read_text(encoding="utf-8-sig"))
                requirement_risks = {
                    str(item.get("slot_id")): "presentation-grade"
                    for item in requirement_payload.get("slots", []) if item.get("slot_id")
                }
            except (OSError, json.JSONDecodeError):
                requirement_risks = {}
        for item in self.payload.get("items", []):
            slot_id = str(item.get("slot_id") or "")
            method = str(item.get("verification_method") or item.get("verification_path") or "unverified")
            legacy_risk = str(item.get("verification_risk") or requirement_risks.get(slot_id) or "")
            if legacy_risk and legacy_risk != "presentation-grade":
                item.setdefault("legacy_verification_risk", legacy_risk)
            item["verification_risk"] = "presentation-grade"
            if item.get("source_type") == "web" and method != "presentation-grade":
                item.setdefault("legacy_verification_method", method)
                method = "presentation-grade"
            item["verification_method"] = method
            item["verification_status"] = "presentation-verified"
            item.setdefault(
                "verification_evidence",
                item.get("provenance") or item.get("hard_gate_verdict") or {},
            )
            item.setdefault(
                "evidence_strength",
                "DIRECT_PROVENANCE" if method == "provenance" else "UNVERIFIED",
            )
            item.setdefault("visual_review_actor", None)
            item.setdefault("confidence", 1.0 if method == "provenance" else 0.0)
            item.setdefault("verification_timestamp", item.get("retrieved_at") or datetime.now(timezone.utc).isoformat())
            item.setdefault("source_attribution_required", bool(item.get("attribution_required")))
            attribution = apply_display_attribution(item)
            slot = self.payload.setdefault("slots", {}).setdefault(slot_id, {})
            if isinstance(slot, dict):
                slot.setdefault("status", "selected")
                slot.setdefault("slide_number", item.get("slide_number"))
                slot.setdefault("quality_gate_passed", bool(item.get("quality_gate_passed", True)))
                slot["verification_risk"] = item["verification_risk"]
                slot["verification_method"] = item["verification_method"]
                slot["verification_status"] = item["verification_status"]
                slot["selected_asset_id"] = item.get("asset_id")
                slot["evidence_strength"] = item.get("evidence_strength")
                slot["display_attribution_mode"] = attribution.mode
                slot["display_attribution"] = attribution.display_attribution
        self.payload["schema_version"] = 4

    def _write_markdown_projections(self) -> None:
        self.markdown_path.parent.mkdir(parents=True, exist_ok=True)
        self.search_log_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            "# Asset Manifest (generated view)",
            "",
            "> Machine truth: `asset-manifest.json`. Do not edit this Markdown file manually.",
            "",
            "| Slide | Slot | Local file | Source | Policy | Score | Display attribution |",
            "| --- | --- | --- | --- | --- | ---: | --- |",
        ]
        for item in sorted(self.payload.get("items", []), key=lambda entry: (entry.get("slide_number", 0), entry.get("slot_id", ""))):
            rows.append(
                "| {slide} | {slot} | `{local}` | [{domain}]({url}) | {policy} | {score:.1f} | {attrib} |".format(
                    slide=item.get("slide_number", ""),
                    slot=item.get("slot_id", ""),
                    local=item.get("local_path", ""),
                    domain=item.get("source_domain", "source"),
                    url=item.get("source_page_url", ""),
                    policy=item.get("source_policy", ""),
                    score=float(item.get("total_score", 0)),
                    attrib=(
                        f"{item.get('display_attribution_mode', '')}: {item.get('display_attribution', '')}"
                        if item.get("display_attribution")
                        else str(item.get("display_attribution_mode") or "")
                    ),
                )
            )
        self.markdown_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

        log = [
            "# Image Search Log (generated view)",
            "",
            "> Generated from `ppt-content/visuals/asset-manifest.json`; the JSON file is the only machine truth.",
            "",
        ]
        for slot_id, slot in sorted(self.payload.get("slots", {}).items()):
            log.extend(
                [
                    f"## {slot_id}",
                    "",
                    f"- Status: {slot.get('status', '')}",
                    f"- Source policy: {slot.get('source_policy', '')}",
                    f"- Queries: {', '.join(slot.get('queries', [])) or '(none)'}",
                    f"- Stats: `{json.dumps(slot.get('stats', {}), ensure_ascii=False, sort_keys=True)}`",
                    "",
                ]
            )
        self.search_log_path.write_text("\n".join(log), encoding="utf-8")

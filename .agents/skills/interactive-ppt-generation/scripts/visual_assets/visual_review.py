from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import SearchCandidate, SlotRequirement
from .retrieval_cache import atomic_write_json


REQUIRED_GATES = (
    "subject",
    "asset_type",
    "action",
    "context",
    "authenticity",
    "forbidden_conditions",
)


class VisualReviewExchange:
    """Compatibility exchange for one optional host-native visual sanity check."""

    def __init__(self, project_path: Path):
        self.root = project_path.resolve() / "research" / "visual-assets" / "visual-review"

    def request_path(self, slot_id: str, candidate_id: str) -> Path:
        return self.root / "requests" / slot_id / f"{candidate_id}.json"

    def result_path(self, slot_id: str, candidate_id: str) -> Path:
        return self.root / "results" / slot_id / f"{candidate_id}.json"

    def write_request(
        self,
        slot: SlotRequirement,
        candidate: SearchCandidate,
        verification: dict[str, Any],
    ) -> Path:
        if not candidate.local_path:
            raise ValueError("visual-review candidate has no local image path")
        path = self.request_path(slot.slot_id, candidate.candidate_id)
        payload = {
            "schema_version": 1,
            "status": "visual-review-optional",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "slot_id": slot.slot_id,
            "candidate_id": candidate.candidate_id,
            "image_path": str(Path(candidate.local_path).resolve()),
            "required_subject": slot.required_subject or slot.subject,
            "required_asset_type": slot.required_asset_type or slot.visual_type,
            "required_action": self._required_action(slot),
            "required_context": slot.required_relationship or slot.purpose,
            "forbidden_conditions": list(slot.forbidden_asset_types),
            "authenticity_requirement": slot.authenticity_requirement,
            "source_context": {
                "title": candidate.title,
                "description": candidate.description,
                "source_page_url": candidate.source_page_url,
                "source_domain": candidate.source_domain,
                "provider": candidate.provider,
                "credit": candidate.credit,
            },
            "verification_risk": verification.get("verification_risk"),
            "verification_mode": verification.get("verification_mode"),
        }
        atomic_write_json(path, payload)
        return path

    def load_result(self, slot: SlotRequirement, candidate: SearchCandidate) -> dict[str, Any] | None:
        path = self.result_path(slot.slot_id, candidate.candidate_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid visual-review result: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("visual-review result must be a JSON object")
        if payload.get("slot_id") != slot.slot_id or payload.get("candidate_id") != candidate.candidate_id:
            raise ValueError("visual-review result does not match the requested slot/candidate")
        image_path = Path(candidate.local_path or "").resolve()
        declared_path = Path(str(payload.get("image_path") or "")).resolve()
        if image_path != declared_path:
            raise ValueError("visual-review result image_path does not match the candidate")
        verdict = payload.get("verdict") if isinstance(payload.get("verdict"), dict) else payload
        missing = [name for name in REQUIRED_GATES if name not in verdict]
        if missing:
            raise ValueError("visual-review result omitted: " + ", ".join(missing))
        if not isinstance(verdict.get("reason"), str) or not verdict.get("reason", "").strip():
            raise ValueError("visual-review result requires a non-empty reason")
        confidence = float(verdict.get("confidence", 0.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("visual-review confidence must be between 0 and 1")
        return {
            "visual_verified": all(bool(verdict[name]) for name in REQUIRED_GATES[:-1])
            and not bool(verdict["forbidden_conditions"]),
            "subject_match": bool(verdict["subject"]),
            "asset_type_match": bool(verdict["asset_type"]),
            "action_match": bool(verdict["action"]),
            "context_match": bool(verdict["context"]),
            "authenticity_match": bool(verdict["authenticity"]),
            "forbidden_match": bool(verdict["forbidden_conditions"]),
            "confidence": confidence,
            "reason": verdict["reason"].strip(),
            "visual_review_actor": str(payload.get("visual_review_actor") or "current-agent"),
            "verification_timestamp": str(
                payload.get("verification_timestamp") or datetime.now(timezone.utc).isoformat()
            ),
            "result_path": str(path.resolve()),
        }

    @staticmethod
    def analysis(result: dict[str, Any], candidate: SearchCandidate) -> dict[str, Any]:
        gate_values = {
            "subject": result["subject_match"],
            "asset_type": result["asset_type_match"],
            "action": result["action_match"],
            "context": result["context_match"],
            "authenticity": result["authenticity_match"],
            "forbidden_conditions": not result["forbidden_match"],
        }
        confidence = float(result["confidence"])
        gates = {
            name: {
                "required": True,
                "passed": bool(passed),
                "confidence": confidence,
                "evidence": result["reason"],
            }
            for name, passed in gate_values.items()
        }
        failed = [name for name, passed in gate_values.items() if not passed]
        return {
            "provider": "host-native-vision",
            "semantic_match": round(confidence * 100, 3),
            "authenticity": round((confidence if result["authenticity_match"] else 0.0) * 100, 3),
            "composition": 75.0,
            "clarity": float(candidate.validation.get("clarity_score", 70.0)),
            "summary": result["reason"],
            "hard_gate_verdict": {
                "passed": not failed,
                "provider": "host-native-vision",
                "gates": gates,
                "reasons": failed,
                "visual_semantic_validation": {
                    "required": False,
                    "passed": not failed,
                    "provider": "host-native-vision",
                },
            },
            "verification_path": "host-native-vision",
            "verification_evidence": result,
            "visual_review_actor": result["visual_review_actor"],
            "confidence": confidence,
            "verification_method": "host-native-vision",
            "evidence_strength": "HOST_VISUAL_VERIFIED",
            "verification_timestamp": result["verification_timestamp"],
            "capability_degraded": False,
        }

    @staticmethod
    def _required_action(slot: SlotRequirement) -> str:
        relationship = slot.required_relationship or slot.purpose
        lower = relationship.casefold()
        if any(token in lower for token in ("action", "perform", "operat", "task", "动作", "操作", "任务")):
            return relationship
        return "none"

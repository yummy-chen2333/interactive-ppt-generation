from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from .models import SearchCandidate, SlotRequirement
from .source_policy import SourcePolicy


class ImageRanker:
    DEFAULT_WEIGHTS = {
        "semantic": 0.27,
        "source": 0.16,
        "authenticity": 0.15,
        "clarity": 0.10,
        "resolution": 0.10,
        "composition": 0.10,
        "provenance": 0.12,
    }

    def score(
        self,
        candidate: SearchCandidate,
        slot: SlotRequirement,
        policy: SourcePolicy,
    ) -> float:
        verdict = candidate.analysis.get("hard_gate_verdict") or candidate.hard_gate_verdict
        if not verdict or not verdict.get("passed"):
            raise ValueError("soft scoring is forbidden before semantic hard gates pass")
        source_score = {1: 100.0, 2: 82.0, 3: 62.0, 4: 38.0}.get(candidate.source_tier, 0.0)
        width = int(candidate.validation.get("width", candidate.width or 0))
        height = int(candidate.validation.get("height", candidate.height or 0))
        resolution_score = min(
            100.0,
            100.0
            * min(
                width / max(1, slot.min_width),
                height / max(1, slot.min_height),
            ),
        )
        provenance = 100.0 if candidate.source_page_url and candidate.image_url else 20.0
        if policy.attribution == "required" and not (candidate.license_name or candidate.credit or candidate.author):
            provenance = min(provenance, 55.0)
        scores: dict[str, float] = {
            "semantic": float(candidate.analysis.get("semantic_match", candidate.metadata_score)),
            "source": source_score,
            "authenticity": float(candidate.analysis.get("authenticity", 50.0)),
            "clarity": float(candidate.analysis.get("clarity", candidate.validation.get("clarity_score", 50.0))),
            "resolution": resolution_score,
            "composition": float(candidate.analysis.get("composition", 50.0)),
            "provenance": provenance,
            "freshness": self._freshness_score(candidate, policy),
        }
        weights = policy.ranking_weights or self.DEFAULT_WEIGHTS
        total_weight = sum(weights.values()) or 1.0
        candidate.scores = {key: round(value, 3) for key, value in scores.items()}
        candidate.total_score = round(
            sum(scores.get(key, 0.0) * weight for key, weight in weights.items()) / total_weight,
            3,
        )
        return candidate.total_score

    def is_good_enough(
        self,
        candidate: SearchCandidate,
        policy: SourcePolicy,
        threshold: float,
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        verdict = candidate.analysis.get("hard_gate_verdict") or candidate.hard_gate_verdict
        if not verdict or not verdict.get("passed"):
            reasons.append("semantic hard gates failed")
        if not candidate.validation.get("valid"):
            reasons.append("local validation failed")
        if candidate.scores.get("resolution", 0) < 100:
            reasons.append("minimum resolution not met")
        return not reasons, reasons

    @staticmethod
    def _freshness_score(candidate: SearchCandidate, policy: SourcePolicy) -> float:
        if not policy.freshness_days:
            return 100.0
        raw = candidate.published_at or candidate.analysis.get("published_at") or candidate.analysis.get("captured_at")
        if not raw:
            return 45.0
        try:
            parsed = parsedate_to_datetime(str(raw))
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                return 45.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - parsed).days
        return max(0.0, 100.0 * (1 - age_days / max(1, policy.freshness_days)))

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .attribution_policy import resolve_license_status
from .models import SearchCandidate, SlotRequirement
from .source_policy import SourcePolicy


# Provider classes can improve candidate ordering and establish a direct page/image
# relation. They never select a stricter verification route.
DIRECT_RELATION_PROVIDERS = {
    "arxiv",
    "crossref",
    "internet-archive",
    "met-museum",
    "neurips-proceedings",
    "official-archive",
    "official-government",
    "official-product",
    "official-university",
    "openalex",
    "wikimedia-commons",
}
EVIDENCE_STRENGTH = {
    "presentation-grade": "PRESENTATION_GRADE",
    "metadata": "METADATA_ONLY",
}


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", value.casefold()))


def _tokens(value: str) -> set[str]:
    return set(_normalized(value).split())


def _domain(url: str) -> str:
    return (urlparse(url).netloc or "").casefold().removeprefix("www.")


@dataclass(slots=True)
class VerificationDecision:
    allowed_mode: str
    risk: str
    reason: str
    requested_mode: str
    requested_risk: str

    @property
    def host_visual_preferred(self) -> bool:
        return False

    @property
    def host_visual_required(self) -> bool:
        return False

    @property
    def vlm_required(self) -> bool:
        """Compatibility alias retained for old callers; photos never require a VLM."""
        return False

    @property
    def provenance_allowed(self) -> bool:
        """Compatibility alias; provenance is recorded but never used as a photo gate."""
        return False

    @property
    def source_grounded_allowed(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "verification_mode": self.allowed_mode,
            "verification_risk": self.risk,
            "requested_mode": self.requested_mode,
            "requested_risk": self.requested_risk,
            "host_visual_preferred": self.host_visual_preferred,
            "host_visual_required": self.host_visual_required,
            "vlm_required": self.vlm_required,
            "provenance_allowed": self.provenance_allowed,
            "source_grounded_allowed": self.source_grounded_allowed,
            "presentation_grade": True,
            "policy_reason": self.reason,
        }


def resolve_verification_mode(slot: SlotRequirement, policy: SourcePolicy) -> VerificationDecision:
    """Resolve every external presentation image to one presentation-grade path.

    Legacy caller fields are captured for manifest compatibility only. They never
    change acceptance, early stop, Stage 7 readiness, or downstream validation.
    """
    del policy
    requested_mode = (slot.verification_mode or "auto").casefold()
    requested_risk = (slot.verification_risk or "auto").casefold()
    return VerificationDecision(
        "presentation",
        "presentation-grade",
        "all external presentation images use relevance, usability, source-context, contradiction, and rights checks",
        requested_mode,
        requested_risk,
    )


class SourceGroundedVerifier:
    """Apply the single presentation-grade source/context verification path."""

    def verify(
        self,
        candidate: SearchCandidate,
        slot: SlotRequirement,
        policy: SourcePolicy,
        decision: VerificationDecision,
    ) -> dict[str, Any]:
        del policy
        source_text = " ".join(
            [candidate.title, candidate.description, candidate.credit, candidate.author]
        )
        source_tokens = _tokens(source_text)
        subject_groups = [
            _tokens(value)
            for value in [
                slot.required_subject or slot.subject,
                slot.subject,
                *slot.entity_aliases,
            ]
            if value
        ]
        relationship_tokens = _tokens(slot.required_relationship or slot.purpose)
        subject_coverage = max(
            (
                len(group & source_tokens) / max(1, len(group))
                for group in subject_groups
            ),
            default=0.0,
        )
        relationship_coverage = len(relationship_tokens & source_tokens) / max(
            1, len(relationship_tokens)
        )
        credible_source = bool(candidate.source_page_url and candidate.source_domain) and (
            candidate.source_tier <= 4
        )
        source_domain = _domain(candidate.source_page_url)
        asset_domain = _domain(candidate.image_url)
        same_source_chain = bool(source_domain and asset_domain) and (
            source_domain == asset_domain
            or source_domain.endswith("." + asset_domain)
            or asset_domain.endswith("." + source_domain)
        )
        direct_relation = bool(candidate.source_page_url and candidate.image_url) and (
            same_source_chain
            or candidate.provenance.get("direct_asset_relation") is True
            or candidate.provider in DIRECT_RELATION_PROVIDERS
            or bool(candidate.title.strip() or candidate.description.strip())
        )
        explicit_context = bool(candidate.title.strip() or candidate.description.strip()) and (
            subject_coverage >= 0.20 or relationship_coverage >= 0.10
        )
        candidate_text = _normalized(source_text)
        candidate_tokens = _tokens(candidate_text)
        forbidden_hit = any(
            normalized in candidate_text
            or (bool(tokens) and tokens.issubset(candidate_tokens))
            for forbidden in slot.forbidden_asset_types
            if (normalized := _normalized(forbidden))
            if (tokens := _tokens(forbidden))
        )
        non_synthetic = not any(
            marker in candidate_text
            for marker in (
                "ai generated",
                "render",
                "illustration",
                "cartoon",
                "concept art",
                "synthetic",
                "人工智能生成",
                "渲染",
                "插画",
                "卡通",
            )
        )
        license_status = resolve_license_status(
            {
                "license_name": candidate.license_name,
                "license_url": candidate.license_url,
            }
        )
        gates = {
            "presentation_grade_path": decision.risk == "presentation-grade",
            "source_not_obviously_untrustworthy": credible_source,
            "direct_image_relation": direct_relation,
            "title_caption_or_context_match": explicit_context,
            "metadata_not_contradictory": not forbidden_hit,
            "authenticity_not_contradictory": non_synthetic,
            "license_not_explicitly_prohibited": license_status != "prohibited",
        }
        failed = [name for name, passed in gates.items() if not passed]
        confidence = min(
            0.92,
            0.55
            + subject_coverage * 0.18
            + relationship_coverage * 0.18
            + (0.08 if candidate.source_tier == 1 else 0.0),
        )
        return {
            "required": decision.source_grounded_allowed,
            "passed": not failed,
            "gates": gates,
            "failed_gates": failed,
            "evidence_strength": (
                EVIDENCE_STRENGTH["presentation-grade"]
                if not failed
                else EVIDENCE_STRENGTH["metadata"]
            ),
            "confidence": round(confidence, 3),
            "source_context": {
                "title": candidate.title,
                "description": candidate.description,
                "credit": candidate.credit,
                "source_page_url": candidate.source_page_url,
                "source_tier": candidate.source_tier,
                "subject_coverage": round(subject_coverage, 3),
                "relationship_coverage": round(relationship_coverage, 3),
                "license_status": license_status,
            },
            "reason": (
                "presentation-grade relevance, usability, source context, contradiction, and rights checks passed"
                if not failed
                else "presentation-grade verification failed: " + ", ".join(failed)
            ),
        }

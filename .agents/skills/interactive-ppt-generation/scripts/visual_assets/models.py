from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SlotRequirement:
    slot_id: str
    slide_number: int
    deck_theme: str
    slide_topic: str
    purpose: str
    subject: str
    required_subject: str = ""
    required_asset_type: str = ""
    required_relationship: str = ""
    forbidden_asset_types: list[str] = field(default_factory=list)
    authenticity_requirement: str = ""
    required: bool = True
    entity_aliases: list[str] = field(default_factory=list)
    visual_type: str = "generic-real-world"
    source_policy: str | None = None
    queries: list[str] = field(default_factory=list)
    required_terms: list[str] = field(default_factory=list)
    negative_terms: list[str] = field(default_factory=list)
    preferred_domains: list[str] = field(default_factory=list)
    excluded_domains: list[str] = field(default_factory=list)
    min_width: int = 1200
    min_height: int = 675
    desired_aspect_ratio: float | None = None
    notes: str = ""
    require_visual_semantic_validation: bool | None = None
    verification_mode: str = "auto"
    verification_risk: str = "auto"

    @classmethod
    def from_dict(cls, data: dict[str, Any], deck: dict[str, Any] | None = None) -> "SlotRequirement":
        deck = deck or {}
        return cls(
            slot_id=str(data["slot_id"]),
            slide_number=int(data.get("slide_number", 0)),
            deck_theme=str(data.get("deck_theme") or deck.get("theme") or ""),
            slide_topic=str(data.get("slide_topic") or ""),
            purpose=str(data.get("purpose") or ""),
            subject=str(data.get("subject") or data.get("purpose") or ""),
            required_subject=str(data.get("required_subject") or data.get("subject") or data.get("purpose") or ""),
            required_asset_type=str(data.get("required_asset_type") or data.get("visual_type") or "generic-real-world"),
            required_relationship=str(data.get("required_relationship") or data.get("purpose") or ""),
            forbidden_asset_types=[str(item) for item in data.get("forbidden_asset_types", [])],
            authenticity_requirement=str(data.get("authenticity_requirement") or ""),
            required=bool(data.get("required", True)),
            entity_aliases=[str(item) for item in data.get("entity_aliases", [])],
            visual_type=str(data.get("visual_type") or "generic-real-world"),
            source_policy=data.get("source_policy"),
            queries=[str(item) for item in data.get("queries", [])],
            required_terms=[str(item) for item in data.get("required_terms", [])],
            negative_terms=[str(item) for item in data.get("negative_terms", [])],
            preferred_domains=[str(item).lower() for item in data.get("preferred_domains", [])],
            excluded_domains=[str(item).lower() for item in data.get("excluded_domains", [])],
            min_width=int(data.get("min_width", 1200)),
            min_height=int(data.get("min_height", 675)),
            desired_aspect_ratio=(
                float(data["desired_aspect_ratio"])
                if data.get("desired_aspect_ratio") is not None
                else None
            ),
            notes=str(data.get("notes") or ""),
            require_visual_semantic_validation=(
                bool(data["require_visual_semantic_validation"])
                if data.get("require_visual_semantic_validation") is not None
                else None
            ),
            verification_mode=str(data.get("verification_mode") or "auto").lower(),
            verification_risk=str(data.get("verification_risk") or "auto").lower(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SearchCandidate:
    candidate_id: str
    provider: str
    query: str
    title: str
    image_url: str
    source_page_url: str
    source_domain: str
    thumbnail_url: str | None = None
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None
    description: str = ""
    author: str = ""
    credit: str = ""
    license_name: str = ""
    license_url: str = ""
    attribution_required: bool = False
    published_at: str = ""
    source_tier: int = 4
    metadata_score: float = 0.0
    local_path: str | None = None
    cache_hit: bool = False
    content_sha256: str | None = None
    perceptual_hash: str | None = None
    validation: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    total_score: float = 0.0
    status: str = "discovered"
    rejection_reason: str | None = None
    hard_gate_verdict: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    document_asset: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchCandidate":
        field_names = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in data.items() if key in field_names})

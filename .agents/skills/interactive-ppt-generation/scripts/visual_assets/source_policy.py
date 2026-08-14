from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

import yaml

from .models import SearchCandidate, SlotRequirement


@dataclass(slots=True)
class SourcePolicy:
    name: str
    label: str
    keywords: list[str]
    preferred_domains: list[str]
    excluded_domains: list[str]
    source_hints: list[str]
    authenticity: str
    freshness_days: int | None
    attribution: str
    allow_ai_fallback: bool
    max_source_tier: int
    ranking_weights: dict[str, float]
    tier1_domain_patterns: list[str]
    tier1_metadata_markers: list[str]
    tier2_domain_patterns: list[str]
    tier3_domain_patterns: list[str]

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "SourcePolicy":
        return cls(
            name=name,
            label=str(data.get("label", name)),
            keywords=[
                str(item).lower()
                for item in [*data.get("keywords", []), *data.get("additional_keywords", [])]
            ],
            preferred_domains=[str(item).lower() for item in data.get("preferred_domains", [])],
            excluded_domains=[str(item).lower() for item in data.get("excluded_domains", [])],
            source_hints=[str(item) for item in data.get("source_hints", [])],
            authenticity=str(data.get("authenticity", "presentation-grade")),
            freshness_days=(int(data["freshness_days"]) if data.get("freshness_days") else None),
            attribution=str(data.get("attribution", "when-required")),
            allow_ai_fallback=bool(data.get("allow_ai_fallback", False)),
            max_source_tier=int(data.get("max_source_tier", 3)),
            ranking_weights={str(key): float(value) for key, value in data.get("ranking_weights", {}).items()},
            tier1_domain_patterns=[str(item).lower() for item in data.get("tier1_domain_patterns", [])],
            tier1_metadata_markers=[str(item).lower() for item in data.get("tier1_metadata_markers", [])],
            tier2_domain_patterns=[str(item).lower() for item in data.get("tier2_domain_patterns", [])],
            tier3_domain_patterns=[str(item).lower() for item in data.get("tier3_domain_patterns", [])],
        )


class SourcePolicyResolver:
    def __init__(self, policy_path: Path):
        self.policy_path = policy_path
        payload = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
        raw_profiles = payload.get("profiles") or {}
        tier_defaults = payload.get("default_source_tier_rules") or {}
        if "generic-real-world" not in raw_profiles:
            raise ValueError("source policy config must define generic-real-world")
        self.profiles = {}
        for name, data in raw_profiles.items():
            merged = dict(data)
            merged["additional_keywords"] = list(data.get("additional_keywords", []))
            for key in (
                "tier1_domain_patterns",
                "tier1_metadata_markers",
                "tier2_domain_patterns",
                "tier3_domain_patterns",
            ):
                merged[key] = list(dict.fromkeys([*tier_defaults.get(key, []), *data.get(key, [])]))
            self.profiles[name] = SourcePolicy.from_dict(name, merged)
        self.aliases = {
            str(key).lower(): str(value)
            for key, value in (payload.get("aliases") or {}).items()
        }

    def select(self, slot: SlotRequirement) -> SourcePolicy:
        if slot.source_policy:
            requested = self.aliases.get(slot.source_policy.lower(), slot.source_policy)
            if requested not in self.profiles:
                raise ValueError(f"Unknown source policy profile: {slot.source_policy}")
            return self._merge_slot_overrides(self.profiles[requested], slot)

        declared_visual_type = self.aliases.get(slot.visual_type.casefold(), slot.visual_type)
        if slot.visual_type.casefold() != "generic-real-world" and declared_visual_type in self.profiles:
            return self._merge_slot_overrides(self.profiles[declared_visual_type], slot)

        context_parts = [
            slot.deck_theme, slot.slide_topic, slot.purpose, slot.subject,
            slot.required_asset_type, slot.required_relationship, slot.authenticity_requirement,
        ]
        context = " ".join(context_parts).casefold()
        visual_context = slot.visual_type.casefold()
        if slot.visual_type.casefold() == "generic-real-world":
            generic = self.profiles["generic-real-world"]
            if any(self._keyword_matches(context, keyword) for keyword in generic.keywords):
                return self._merge_slot_overrides(generic, slot)
        best_name = "generic-real-world"
        best_score = 0
        for name, profile in self.profiles.items():
            if name == "generic-real-world":
                continue
            score = sum(
                2 if self._keyword_matches(visual_context, keyword) else 1
                for keyword in profile.keywords
                if self._keyword_matches(context, keyword)
                or self._keyword_matches(visual_context, keyword)
            )
            if score > best_score:
                best_name, best_score = name, score
        return self._merge_slot_overrides(self.profiles[best_name], slot)

    @staticmethod
    def _keyword_matches(text: str, keyword: str) -> bool:
        """Match semantic tokens/phrases without collisions inside other words."""
        normalized_text = re.sub(r"[_-]+", " ", text.casefold())
        normalized_keyword = re.sub(r"[_-]+", " ", keyword.casefold()).strip()
        if not normalized_keyword:
            return False
        if re.search(r"[\u4e00-\u9fff]", normalized_keyword):
            return normalized_keyword in normalized_text
        pattern = r"(?<![a-z0-9])" + re.escape(normalized_keyword).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
        return re.search(pattern, normalized_text) is not None

    @staticmethod
    def _merge_slot_overrides(policy: SourcePolicy, slot: SlotRequirement) -> SourcePolicy:
        data = {
            "label": policy.label,
            "keywords": policy.keywords,
            "preferred_domains": list(dict.fromkeys(slot.preferred_domains + policy.preferred_domains)),
            "excluded_domains": list(dict.fromkeys(slot.excluded_domains + policy.excluded_domains)),
            "source_hints": policy.source_hints,
            "authenticity": policy.authenticity,
            "freshness_days": policy.freshness_days,
            "attribution": policy.attribution,
            "allow_ai_fallback": policy.allow_ai_fallback,
            "max_source_tier": policy.max_source_tier,
            "ranking_weights": policy.ranking_weights,
            "tier1_domain_patterns": policy.tier1_domain_patterns,
            "tier1_metadata_markers": policy.tier1_metadata_markers,
            "tier2_domain_patterns": policy.tier2_domain_patterns,
            "tier3_domain_patterns": policy.tier3_domain_patterns,
        }
        return SourcePolicy.from_dict(policy.name, data)

    def classify_source(self, candidate: SearchCandidate, policy: SourcePolicy) -> int:
        domain = candidate.source_domain.lower()
        metadata = " ".join(
            [candidate.title, candidate.description, candidate.author, candidate.credit, candidate.source_page_url]
        ).lower()
        if self.is_excluded(domain, policy):
            return 5
        if any(self._domain_matches(domain, preferred) for preferred in policy.preferred_domains):
            return 1
        if any(self._domain_matches(domain, pattern) for pattern in policy.tier1_domain_patterns):
            return 1
        if (
            candidate.provider in {
                "official-page", "met-museum", "neurips-proceedings",
                "internet-archive", "wikimedia-commons",
            }
            and any(marker in metadata for marker in policy.tier1_metadata_markers)
        ):
            return 1
        if policy.name == "company-product-technology" and self._looks_like_official_company_page(
            candidate, metadata
        ):
            return 2
        if any(self._domain_matches(domain, pattern) for pattern in policy.tier2_domain_patterns):
            return 2
        if any(self._domain_matches(domain, pattern) for pattern in policy.tier3_domain_patterns):
            return 3
        return 4

    @staticmethod
    def _looks_like_official_company_page(candidate: SearchCandidate, metadata: str) -> bool:
        domain = candidate.source_domain.lower().removeprefix("www.")
        labels = [label for label in domain.split(".") if label]
        if len(labels) < 2:
            return False
        brand = re.sub(r"[^a-z0-9]", "", labels[-2])
        collapsed = re.sub(r"[^a-z0-9]", "", metadata)
        path = (urlparse(candidate.source_page_url).path or "").casefold()
        path_segments = {segment for segment in path.split("/") if segment}
        product_path = bool(path_segments & {
            "product", "products", "solution", "solutions", "research",
            "technology", "technologies", "robot", "robots", "newsroom",
            "press", "rd", "r-d",
        })
        subject_marker = any(
            marker in metadata
            for marker in ("robot", "autonomous", "device", "product", "technology", "system")
        )
        excluded = any(
            marker in domain
            for marker in ("pinterest", "freepik", "vecteezy", "pixabay", "wallpaper", "blogspot")
        )
        return bool(brand and len(brand) >= 4 and brand in collapsed and product_path and subject_marker and not excluded)

    def is_excluded(self, domain: str, policy: SourcePolicy) -> bool:
        return any(self._domain_matches(domain, blocked) for blocked in policy.excluded_domains)

    @staticmethod
    def _domain_matches(domain: str, configured: str) -> bool:
        configured_domain = urlparse(configured).netloc or configured
        configured_domain = configured_domain.lower().removeprefix("www.")
        domain = domain.lower().removeprefix("www.")
        return domain == configured_domain.lstrip(".") or domain.endswith(configured_domain)

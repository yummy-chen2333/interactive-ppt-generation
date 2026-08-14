from __future__ import annotations

import math
import re
from urllib.parse import urlsplit, urlunsplit

from .attribution_policy import resolve_license_status
from .models import SearchCandidate, SlotRequirement
from .source_policy import SourcePolicy, SourcePolicyResolver


def _tokens(text: str) -> set[str]:
    output: set[str] = set()
    for token in re.findall(r"[\w\u4e00-\u9fff]+", text.casefold()):
        if len(token) <= 1:
            continue
        output.add(token)
        if re.fullmatch(r"[a-z]+s", token) and not token.endswith("ss"):
            output.add(token[:-1])
    return output


class CandidateFilter:
    def __init__(self, resolver: SourcePolicyResolver):
        self.resolver = resolver

    def filter_and_rank_metadata(
        self,
        candidates: list[SearchCandidate],
        slot: SlotRequirement,
        policy: SourcePolicy,
    ) -> list[SearchCandidate]:
        seen_urls: set[str] = set()
        output: list[SearchCandidate] = []
        context_tokens = _tokens(
            " ".join([slot.subject, slot.slide_topic, slot.purpose, *slot.required_terms])
        )
        subject_groups = [
            _tokens(value) - {
                "the", "of", "by", "during", "historical", "real", "official", "image",
                "artwork", "painting", "portrait", "museum", "object", "collection", "record",
                "institutional", "dynasty", "ancient", "modern", "作品", "绘画", "画作", "肖像",
                "博物馆", "馆藏", "历史", "人物", "图片", "图像", "文物", "朝代",
            }
            for value in [slot.required_subject or slot.subject, slot.subject, *slot.entity_aliases]
            if value
        ]
        negative_tokens = _tokens(" ".join(slot.negative_terms))
        for candidate in candidates:
            canonical = self._canonical_url(candidate.image_url)
            if canonical in seen_urls:
                candidate.status = "rejected"
                candidate.rejection_reason = "duplicate URL"
                continue
            seen_urls.add(canonical)
            lower_title = candidate.title.casefold()
            if not candidate.document_asset and (lower_title.endswith((".pdf", ".djvu")) or (
                candidate.mime_type and not candidate.mime_type.startswith("image/")
            )):
                candidate.status = "rejected"
                candidate.rejection_reason = "candidate is not a supported raster image"
                continue
            if lower_title.endswith(".svg") and any(
                token in (slot.purpose + " " + slot.visual_type).casefold()
                for token in ("photo", "photograph", "real-evidence", "real-scene")
            ):
                candidate.status = "rejected"
                candidate.rejection_reason = "vector artwork cannot satisfy a real-photo slot"
                continue
            if candidate.width and candidate.height and (
                candidate.width < slot.min_width or candidate.height < slot.min_height
            ):
                candidate.status = "rejected"
                candidate.rejection_reason = (
                    f"known resolution below slot minimum: {candidate.width}x{candidate.height} "
                    f"< {slot.min_width}x{slot.min_height}"
                )
                continue
            if self.resolver.is_excluded(candidate.source_domain, policy):
                candidate.status = "rejected"
                candidate.rejection_reason = "source domain excluded by selected policy"
                continue
            if resolve_license_status({
                "license_name": candidate.license_name,
                "license_url": candidate.license_url,
            }) == "prohibited":
                candidate.status = "rejected"
                candidate.rejection_reason = "source explicitly prohibits reuse"
                continue
            text = " ".join(
                [candidate.title, candidate.description, candidate.author, candidate.credit, candidate.published_at]
            ).casefold()
            years = [int(value) for value in re.findall(r"(?<!\d)(\d{3,4})(?!\d)", text)]
            if any(960 <= year <= 1279 for year in years):
                text += " song dynasty 宋代"
            if negative_tokens and _tokens(text) & negative_tokens:
                candidate.status = "rejected"
                candidate.rejection_reason = "negative term matched"
                continue
            candidate.source_tier = self.resolver.classify_source(candidate, policy)
            if candidate.provenance.get("provider_verified") is True:
                candidate.source_tier = 1
            if candidate.source_tier > 4:
                candidate.status = "rejected"
                candidate.rejection_reason = "source policy rejected candidate"
                continue
            metadata_tokens = _tokens(text)
            if candidate.provider == "met-museum":
                candidate.source_tier = 1
            subject_coverage = max(
                (len(group & metadata_tokens) / max(1, len(group)) for group in subject_groups),
                default=0.0,
            )
            exact_subject = any(
                value and value.casefold() in text
                for value in [slot.required_subject, slot.subject, *slot.entity_aliases]
            )
            if (
                "real-evidence" in slot.visual_type.casefold()
                and not exact_subject
                and subject_coverage < 0.40
                and candidate.provenance.get("provider_verified") is not True
            ):
                candidate.status = "rejected"
                candidate.rejection_reason = "required subject/entity absent from candidate metadata"
                continue
            if slot.visual_type.casefold() == "real-scene":
                distinctive_scene_tokens = set().union(*subject_groups) - {
                    "real", "world", "scene", "authentic", "photo", "photograph", "system",
                    "enabled", "autonomous", "ai", "human", "oversight", "environment",
                    "真实", "场景", "系统", "照片",
                }
                if distinctive_scene_tokens and not (distinctive_scene_tokens & metadata_tokens):
                    candidate.status = "rejected"
                    candidate.rejection_reason = "required scene subject absent from candidate metadata"
                    continue
            overlap = len(context_tokens & metadata_tokens) / max(1, len(context_tokens))
            phrase_bonus = 0.25 if exact_subject else subject_coverage * 0.20
            type_text = (slot.required_asset_type or slot.visual_type).casefold()
            type_bonus = 0.0
            if any(marker in type_text for marker in ("painting", "artwork", "绘画", "画作")):
                type_bonus = 0.18 if any(
                    marker in text for marker in ("painting", "handscroll", "scroll", "ink", "silk", "绘画", "手卷", "绢本")
                ) else 0.0
            if any(marker in type_text for marker in ("portrait", "肖像")):
                type_bonus = 0.18 if any(marker in text for marker in ("portrait", "depicted person", "肖像", "帝像")) else 0.0
            forbidden_penalty = 0.0
            if any(marker.casefold() in text for marker in slot.forbidden_asset_types):
                forbidden_penalty = 0.40
            dimension_bonus = self._dimension_score(candidate, slot) * 0.15
            source_bonus = max(0.0, (5 - candidate.source_tier) / 4) * 0.25
            institutional_relationship = any(
                marker in (slot.required_relationship or slot.purpose).casefold()
                for marker in ("museum", "collection", "institution", "博物馆", "馆藏", "机构")
            )
            institutional_bonus = 0.20 if institutional_relationship and candidate.provider == "met-museum" else 0.0
            candidate.metadata_score = round(
                100 * max(0.0, min(1.0, overlap * 0.4 + phrase_bonus + type_bonus + dimension_bonus + source_bonus + institutional_bonus - forbidden_penalty)),
                3,
            )
            candidate.status = "filtered"
            output.append(candidate)
        return sorted(output, key=lambda item: item.metadata_score, reverse=True)

    @staticmethod
    def overlap_ratio(before: set[str], after: list[SearchCandidate]) -> float:
        current = {CandidateFilter._canonical_url(candidate.image_url) for candidate in after}
        return len(before & current) / max(1, len(current))

    @staticmethod
    def _canonical_url(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))

    @staticmethod
    def _dimension_score(candidate: SearchCandidate, slot: SlotRequirement) -> float:
        if not candidate.width or not candidate.height:
            return 0.70 if candidate.provider == "official-page" else 0.35
        width_score = min(1.0, candidate.width / max(1, slot.min_width))
        height_score = min(1.0, candidate.height / max(1, slot.min_height))
        score = math.sqrt(width_score * height_score)
        if slot.desired_aspect_ratio:
            ratio = candidate.width / candidate.height
            score *= max(0.3, 1.0 - abs(ratio - slot.desired_aspect_ratio) / slot.desired_aspect_ratio)
        return score

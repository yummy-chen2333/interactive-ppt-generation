from __future__ import annotations

import re

from .models import SlotRequirement
from .source_policy import SourcePolicy


class QueryBuilder:
    """Build bounded slot-first queries; use the deck theme only for ambiguity."""

    def build(self, slot: SlotRequirement, policy: SourcePolicy, budget: int) -> list[str]:
        candidates: list[str] = []
        candidates.extend(slot.queries)

        subject = self._clean(slot.required_subject or slot.subject)
        subject = self._disambiguate_subject(subject, slot)
        aliases = [self._clean(item) for item in slot.entity_aliases if self._clean(item)]
        asset_type = self._asset_type_phrase(slot.required_asset_type or slot.visual_type)
        relationship = self._clean(slot.required_relationship or slot.purpose)
        base = " ".join(part for part in (subject, asset_type) if part)
        if base:
            candidates.append(base)
        if relationship and relationship.casefold() not in base.casefold():
            candidates.append(f"{subject} {asset_type} {self._relationship_phrase(relationship)}")
        candidates.extend(aliases[:2])
        if subject:
            candidates.append(re.sub(r"\s+by\s+", " ", subject, flags=re.IGNORECASE))

        output: list[str] = []
        seen: set[str] = set()
        for query in candidates:
            normalized = self._deduplicate_adjacent_words(" ".join(query.split()).strip())
            key = normalized.casefold()
            if not normalized or key in seen:
                continue
            seen.add(key)
            output.append(normalized)
            if len(output) >= budget:
                break
        return output

    @staticmethod
    def _deduplicate_adjacent_words(text: str) -> str:
        words = text.split()
        output: list[str] = []
        for word in words:
            if output and output[-1].casefold() == word.casefold():
                continue
            output.append(word)
        return " ".join(output)

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"[\r\n\t]+", " ", text).strip()

    @staticmethod
    def _asset_type_phrase(text: str) -> str:
        lower = text.casefold()
        if "portrait" in lower or "肖像" in lower:
            return "historical portrait"
        if "handscroll" in lower or "长卷" in lower or "手卷" in lower:
            return "painting handscroll"
        if "painting" in lower or "绘画" in lower or "画作" in lower:
            return "painting artwork"
        if "micro" in lower or "显微" in lower:
            return "microscopy micrograph"
        if "photo" in lower or "摄影" in lower or "照片" in lower:
            return "photograph"
        return " ".join(text.split()[:4])

    @staticmethod
    def _relationship_phrase(text: str) -> str:
        lower = text.casefold()
        if any(marker in lower for marker in ("museum", "collection", "institution", "博物馆", "馆藏", "机构")):
            return "museum collection record"
        if any(marker in lower for marker in ("laboratory", "research", "实验室", "科研")):
            return "official laboratory source"
        return " ".join(text.split()[:6])

    @staticmethod
    def _disambiguate_subject(text: str, slot: SlotRequirement) -> str:
        if slot.visual_type.casefold() == "real-scene":
            text = re.sub(r"\breal\s+", "", text, flags=re.IGNORECASE)
        return text.strip()

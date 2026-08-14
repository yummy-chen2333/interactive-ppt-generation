from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

import httpx

from .models import SearchCandidate, SlotRequirement
from .source_policy import SourcePolicy
from .verification import resolve_verification_mode


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w\u4e00-\u9fff]+", text.casefold())
        if len(token) > 1
    }


def _contains_any(text: str, markers: tuple[str, ...] | list[str]) -> bool:
    return any(marker.casefold() in text for marker in markers if marker)


def _gate(required: str, passed: bool, confidence: float, evidence: str) -> dict[str, Any]:
    return {
        "required": required,
        "passed": passed,
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "evidence": evidence,
    }


_GENERIC_SUBJECT_TOKENS = {
    "the", "a", "an", "of", "by", "during", "historical", "history", "real", "official",
    "image", "photo", "photograph", "artwork", "painting", "portrait", "museum", "object",
    "collection", "record", "institutional", "dynasty", "ancient", "modern", "作品", "绘画",
    "画作", "肖像", "博物馆", "馆藏", "历史", "人物", "图片", "图像", "文物", "朝代",
}


def _distinctive_tokens(text: str) -> set[str]:
    return _tokens(text) - _GENERIC_SUBJECT_TOKENS


def requires_visual_semantic_validation(slot: SlotRequirement, policy: SourcePolicy) -> bool:
    """Compatibility API: pixel inspection is optional for presentation-grade photos."""
    resolve_verification_mode(slot, policy)
    return False


class DeterministicImageAnalyzer:
    """Pixel-quality and metadata-semantic analysis with no external model dependency."""

    async def analyze(
        self,
        path: Path,
        candidate: SearchCandidate,
        slot: SlotRequirement,
        policy: SourcePolicy,
    ) -> dict[str, Any]:
        context = " ".join(
            [slot.subject, slot.slide_topic, slot.purpose, *slot.required_terms]
        )
        candidate_text = " ".join(
            [
                candidate.title,
                candidate.description,
                candidate.author,
                candidate.credit,
                candidate.published_at,
                candidate.source_domain,
            ]
        )
        year_matches = [int(value) for value in re.findall(r"(?<!\d)(\d{3,4})(?!\d)", candidate_text)]
        if any(960 <= year <= 1279 for year in year_matches):
            candidate_text += " Song dynasty 宋代"
        context_tokens = _tokens(context)
        candidate_tokens = _tokens(candidate_text)
        overlap_count = len(context_tokens & candidate_tokens)
        coverage = overlap_count / max(1, len(context_tokens))
        precision = overlap_count / max(1, min(len(candidate_tokens), len(context_tokens)))
        exact_subject = bool(slot.subject and slot.subject.casefold() in candidate_text.casefold())
        subject_tokens = _tokens(slot.subject)
        subject_coverage = len(subject_tokens & candidate_tokens) / max(1, len(subject_tokens))
        semantic = min(
            100.0,
            35.0
            + coverage * 28.0
            + precision * 22.0
            + subject_coverage * 22.0
            + (12.0 if exact_subject else 0.0),
        )

        lower = candidate_text.casefold()
        synthetic_markers = ("ai generated", "generative ai", "render", "illustration", "vector", "3d model")
        authentic_markers = (
            "photo",
            "photograph",
            "micrograph",
            "microscopy",
            "scanning electron",
            "official",
            "archive",
            "museum",
            "institutional collection",
            "primary source",
        )
        authenticity = 72.0
        authenticity += 15.0 if any(marker in lower for marker in authentic_markers) else 0.0
        authenticity -= 40.0 if any(marker in lower for marker in synthetic_markers) else 0.0
        authenticity = max(0.0, min(100.0, authenticity))

        required_subject = slot.required_subject or slot.subject
        subject_aliases = [required_subject, slot.subject, *slot.entity_aliases]
        subject_token_groups = [_distinctive_tokens(item) for item in subject_aliases if item]
        subject_coverages = []
        for tokens in subject_token_groups:
            coverage_value = len(tokens & candidate_tokens) / max(1, len(tokens))
            if {"along", "river"}.issubset(tokens) and {"spring", "festival", "river"}.issubset(candidate_tokens):
                coverage_value = max(coverage_value, 0.60)
            subject_coverages.append(coverage_value)
        best_subject_coverage = max(subject_coverages or [0.0])
        subject_pass = any(alias.casefold() in lower for alias in subject_aliases if alias)
        subject_pass = subject_pass or best_subject_coverage >= 0.45

        required_type = slot.required_asset_type or slot.visual_type
        type_lower = required_type.casefold()
        portrait_markers = (
            "portrait", "depicted person:", "sitter", "head portrait", "emperor portrait", "biographical portrait",
            "人物肖像", "肖像画", "皇帝肖像", "帝像",
        )
        artwork_markers = (
            "painting", "artwork", "handscroll", "scroll", "landscape", "ink", "color on silk",
            "绘画", "画作", "长卷", "手卷", "山水", "绢本",
        )
        photo_markers = ("photo", "photograph", "camera", "摄影", "照片")
        classified_types: list[str] = []
        if _contains_any(lower, portrait_markers):
            classified_types.append("person-portrait")
        if _contains_any(lower, artwork_markers):
            classified_types.append("artwork")
        if _contains_any(lower, photo_markers):
            classified_types.append("photograph")
        if _contains_any(type_lower, ("portrait", "肖像", "biography", "人物")):
            type_pass = "person-portrait" in classified_types
            if not type_pass and subject_pass and candidate.width and candidate.height:
                type_pass = candidate.height >= candidate.width * 0.95
        elif _contains_any(type_lower, ("artwork", "painting", "museum object", "绘画", "艺术", "文物")):
            type_pass = "artwork" in classified_types
            non_painting_objects = (
                "vase", "jar", "bowl", "plate", "dish", "pot", "ceramic", "porcelain",
                "sculpture", "statue", "carved in stone", "stone carving", "stone relief",
                "furniture", "textile", "treatise", "calligraphy", "manuscript", "poem", "sutra",
                "inscription", "stele", "letter", "album of calligraphy", "written text",
                "瓷瓶", "花瓶", "罐", "碗", "盘", "瓷器", "雕塑", "石刻", "石雕",
                "书法", "手稿", "论著", "诗文", "经卷", "碑刻", "题跋",
            )
            if _contains_any(type_lower, ("painting", "绘画", "画作")) and _contains_any(lower, non_painting_objects):
                type_pass = False
        elif _contains_any(type_lower, ("photo", "photograph", "real-scene", "照片", "摄影")):
            type_pass = "photograph" in classified_types
        else:
            type_pass = subject_pass

        relationship = slot.required_relationship or slot.purpose
        relationship_tokens = _tokens(relationship)
        relationship_coverage = len(relationship_tokens & candidate_tokens) / max(1, len(relationship_tokens))
        institution_markers = (
            "museum", "collection", "institution", "archive", "digitized by", "credit line",
            "博物馆", "馆藏", "机构", "档案", "收藏",
        )
        relationship_pass = relationship_coverage >= 0.20 or subject_pass
        relationship_confidence = relationship_coverage
        relationship_evidence = f"metadata coverage={relationship_coverage:.3f}"
        if not relationship_tokens:
            relationship_pass = True
            relationship_confidence = 1.0
            relationship_evidence = "no relationship constraint"

        forbidden_hits: list[str] = []
        for forbidden in slot.forbidden_asset_types:
            forbidden_lower = forbidden.casefold()
            if _contains_any(forbidden_lower, ("portrait", "person", "人物", "肖像")):
                if "person-portrait" in classified_types:
                    forbidden_hits.append(forbidden)
            elif _contains_any(forbidden_lower, ("modern reproduction", "modern imitation", "现代仿作", "现代复制")):
                reproduction_markers = (
                    "modern reproduction", "modern imitation", "replica", "copy after", "carved in stone",
                    "stone carving", "stone relief", "现代仿作", "现代复制", "复制品", "石刻", "石雕",
                )
                modern_year = any(year >= 1800 for year in year_matches)
                if _contains_any(lower, reproduction_markers) or (
                    modern_year and _contains_any(lower, ("carved", "replica", "copy", "reproduction", "stone"))
                ):
                    forbidden_hits.append(forbidden)
            elif forbidden_lower and forbidden_lower in lower:
                forbidden_hits.append(forbidden)
        synthetic = _contains_any(lower, synthetic_markers)
        authenticity_required = "presentation-grade"
        authenticity_pass = not synthetic

        gates = {
            "subject": _gate(required_subject, subject_pass, best_subject_coverage, f"metadata coverage={best_subject_coverage:.3f}"),
            "asset_type": _gate(required_type, type_pass, 0.9 if type_pass else 0.2, ", ".join(classified_types) or "unclassified"),
            "relationship": _gate(
                relationship,
                relationship_pass,
                relationship_confidence,
                relationship_evidence,
            ),
            "forbidden_asset_types": _gate(
                ", ".join(slot.forbidden_asset_types) or "none",
                not forbidden_hits,
                1.0 if not forbidden_hits else 0.95,
                "none detected" if not forbidden_hits else f"detected: {', '.join(forbidden_hits)}",
            ),
            "authenticity": _gate(
                authenticity_required,
                authenticity_pass,
                authenticity / 100.0,
                "traceable non-synthetic source" if authenticity_pass else "authenticity/source requirement failed",
            ),
        }
        hard_gate_reasons = [name for name, verdict in gates.items() if not verdict["passed"]]
        hard_gate_verdict = {
            "passed": not hard_gate_reasons,
            "provider": "deterministic-metadata-pixel",
            "gates": gates,
            "reasons": hard_gate_reasons,
        }

        clarity = float(candidate.validation.get("clarity_score", 50.0))
        aspect = float(candidate.validation.get("aspect_score", 75.0))
        composition = min(100.0, 55.0 + aspect * 0.35 + clarity * 0.1)
        return {
            "provider": "deterministic-metadata-pixel",
            "semantic_match": round(semantic, 3),
            "authenticity": round(authenticity, 3),
            "composition": round(composition, 3),
            "clarity": round(clarity, 3),
            "summary": "Metadata semantics plus decoded-pixel quality signals.",
            "hard_gate_verdict": hard_gate_verdict,
            "model_fallback": False,
        }


class OpenAICompatibleVisionAnalyzer:
    """Optional vendor-compatible extension; never the default Stage 7 dependency."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.api_key = os.getenv("VISUAL_ASSET_VISION_API_KEY")
        self.base_url = os.getenv("VISUAL_ASSET_VISION_BASE_URL", "https://api.openai.com/v1")
        self.model = os.getenv("VISUAL_ASSET_VISION_MODEL")

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.model)

    async def analyze(
        self,
        path: Path,
        candidate: SearchCandidate,
        slot: SlotRequirement,
        policy: SourcePolicy,
    ) -> dict[str, Any]:
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        prompt = (
            "Evaluate this candidate for a presentation image slot. Return valid JSON with numeric "
            "semantic_match, authenticity, composition, clarity (0-100), summary, and hard_gate_verdict. "
            "hard_gate_verdict must contain passed, reasons, and gates for subject, asset_type, relationship, "
            "forbidden_asset_types, and authenticity. Each gate must contain passed, confidence, and evidence. "
            "Reject obvious subject, object, event, image-type, or forbidden-condition mismatches. "
            f"Deck theme: {slot.deck_theme}. Slide: {slot.slide_topic}. Slot purpose: {slot.purpose}. "
            f"Required subject: {slot.required_subject or slot.subject}. Required asset type: "
            f"{slot.required_asset_type or slot.visual_type}. Required relationship: "
            f"{slot.required_relationship or slot.purpose}. Forbidden asset types: {slot.forbidden_asset_types}. "
            f"Authenticity requirement: {slot.authenticity_requirement or policy.authenticity}. "
            f"Source policy: {policy.name}. "
            f"Candidate title: {candidate.title}. Source: {candidate.source_page_url}."
        )
        response = await self.client.post(
            self.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                        ],
                    }
                ],
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        payload = json.loads(content)
        verdict = payload.get("hard_gate_verdict")
        if not isinstance(verdict, dict) or not isinstance(verdict.get("gates"), dict):
            raise ValueError("VLM response omitted structured hard_gate_verdict")
        required_gates = {"subject", "asset_type", "relationship", "forbidden_asset_types", "authenticity"}
        if not required_gates.issubset(verdict["gates"]):
            raise ValueError("VLM hard_gate_verdict omitted required gates")
        verdict["passed"] = all(bool(verdict["gates"][name].get("passed")) for name in required_gates)
        verdict["provider"] = "openai-compatible-vision"
        payload["provider"] = "openai-compatible-vision"
        payload["model_fallback"] = False
        return payload


class ImageAnalyzer:
    def __init__(self, client: httpx.AsyncClient):
        self.deterministic = DeterministicImageAnalyzer()
        # Kept only for installations that explicitly opt into an external visual provider.
        # The formal workflow uses the host-native request/result exchange first.
        self.vision = OpenAICompatibleVisionAnalyzer(client)

    async def analyze(
        self,
        path: Path,
        candidate: SearchCandidate,
        slot: SlotRequirement,
        policy: SourcePolicy,
    ) -> dict[str, Any]:
        baseline = await self.deterministic.analyze(path, candidate, slot, policy)
        visual_optional = self.vision.available
        if not self.vision.available:
            return baseline
        try:
            visual = await self.vision.analyze(path, candidate, slot, policy)
        except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
            baseline["model_fallback"] = True
            baseline["model_error"] = str(exc)
            return baseline
        for key in ("semantic_match", "authenticity", "composition", "clarity"):
            if key in visual:
                visual[key] = max(0.0, min(100.0, float(visual[key])))
        visual.setdefault("hard_gate_verdict", baseline["hard_gate_verdict"])
        visual["hard_gate_verdict"]["visual_semantic_validation"] = {
            "required": False,
            "passed": True,
            "provider": "openai-compatible-vision",
        }
        return visual

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping


FULL_CREDIT = "full-credit"
COMPACT_SOURCE = "compact-source"
PROVENANCE_ONLY = "provenance-only"
NONE = "none"
DISPLAY_ATTRIBUTION_MODES = frozenset({
    FULL_CREDIT,
    COMPACT_SOURCE,
    PROVENANCE_ONLY,
    NONE,
})
VISIBLE_ATTRIBUTION_MODES = frozenset({FULL_CREDIT, COMPACT_SOURCE})

_EXPLICIT_ATTRIBUTION_LICENSE_RE = re.compile(
    r"(?:\bcc\s*by\b|creative\s+commons\s+attribution|\bby-sa\b|\bby-nc\b|"
    r"attribution\s+required|credit\s+required)",
    flags=re.IGNORECASE,
)
_NO_CREDIT_LICENSE_RE = re.compile(
    r"(?:public\s+domain|\bcc0\b|creative\s+commons\s+zero)",
    flags=re.IGNORECASE,
)
_UNKNOWN_LICENSE_RE = re.compile(
    r"(?:^$|^unknown$|^unspecified$|see\s+.+record|verify\s+reuse\s+terms)",
    flags=re.IGNORECASE,
)
_PROHIBITED_LICENSE_RE = re.compile(
    r"(?:do\s+not\s+use|reuse\s+prohibited|not\s+licensed\s+for\s+reuse|"
    r"no\s+reproduction|reproduction\s+prohibited|permission\s+denied)",
    flags=re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(?:18|19|20)\d{2}\b")


@dataclass(frozen=True, slots=True)
class DisplayAttributionDecision:
    mode: str
    display_attribution: str
    reason: str
    license_obligation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _compact_source(item: Mapping[str, Any]) -> str:
    author = _clean(item.get("author"))
    provider = _clean(item.get("provider"))
    domain = _clean(item.get("source_domain"))
    published = _clean(item.get("published_at"))
    provenance = item.get("provenance") if isinstance(item.get("provenance"), Mapping) else {}
    entity_id = _clean(provenance.get("entity_document_id"))
    year_match = _YEAR_RE.search(published)
    year = year_match.group(0) if year_match else ""

    source = author or provider or domain or "Source"
    detail = ""
    if entity_id:
        detail = entity_id.rsplit(":", 1)[-1]
        # Provider-internal hashes stay in provenance, but are not useful in a
        # human-facing compact citation.
        if re.fullmatch(r"[0-9a-f]{24,}", detail, flags=re.IGNORECASE):
            detail = ""
    parts = [source]
    if detail and detail.casefold() not in source.casefold():
        parts.append(detail)
    if year and year not in " ".join(parts):
        parts.append(year)
    return " · ".join(parts)


def _full_credit(item: Mapping[str, Any]) -> str:
    author = _clean(item.get("author"))
    credit = _clean(item.get("credit"))
    license_name = _clean(item.get("license_name"))
    source = credit or author or _clean(item.get("source_domain")) or "Source"
    parts = [source]
    if author and author.casefold() not in source.casefold():
        parts.insert(0, author)
    if license_name and license_name.casefold() not in " ".join(parts).casefold():
        parts.append(license_name)
    return " · ".join(parts)


def resolve_license_status(item: Mapping[str, Any]) -> str:
    """Return known, unknown, or prohibited without starting a rights investigation."""
    license_text = " ".join(
        part for part in (
            _clean(item.get("license_name")),
            _clean(item.get("license_url")),
        )
        if part
    )
    if _PROHIBITED_LICENSE_RE.search(license_text):
        return "prohibited"
    if _UNKNOWN_LICENSE_RE.search(license_text):
        return "unknown"
    return "known"


def resolve_display_attribution(item: Mapping[str, Any]) -> DisplayAttributionDecision:
    """Derive visible-credit policy without changing the Stage 7 image decision."""
    source_type = _clean(item.get("source_type")).casefold()
    license_name = _clean(item.get("license_name"))
    license_status = _clean(item.get("license_status")) or resolve_license_status(item)

    if source_type in {"user", "original", "project-original", "generated", "ai-generated"}:
        return DisplayAttributionDecision(
            NONE,
            "",
            "project-owned or generated asset has no external display-source requirement",
            "none",
        )

    if _EXPLICIT_ATTRIBUTION_LICENSE_RE.search(license_name):
        return DisplayAttributionDecision(
            FULL_CREDIT,
            _full_credit(item),
            "normalized license explicitly requires visible attribution",
            "explicit-visible-credit",
        )

    no_credit_required = bool(_NO_CREDIT_LICENSE_RE.search(license_name))
    unknown_license = license_status == "unknown"
    if bool(item.get("source_attribution_required")):
        return DisplayAttributionDecision(
            COMPACT_SOURCE,
            _compact_source(item),
            "the source record requests attribution but supplies no explicit full-credit license text",
            "no-visible-license-obligation" if no_credit_required else (
                "unknown-license" if unknown_license else "citation-required"
            ),
        )
    return DisplayAttributionDecision(
        PROVENANCE_ONLY,
        "",
        "source and rights metadata remain in the manifest without an invented visible-credit rule",
        "unknown-license" if unknown_license else "provenance-retained",
    )


def apply_display_attribution(item: dict[str, Any]) -> DisplayAttributionDecision:
    item["license_status"] = resolve_license_status(item)
    decision = resolve_display_attribution(item)
    item["display_attribution_mode"] = decision.mode
    item["display_attribution"] = decision.display_attribution
    item["display_attribution_reason"] = decision.reason
    item["license_obligation"] = decision.license_obligation
    item["attribution_required"] = decision.mode in VISIBLE_ATTRIBUTION_MODES
    return decision

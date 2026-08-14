from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from visual_assets.asset_manifest import AssetManifest
from visual_assets.attribution_policy import (
    DISPLAY_ATTRIBUTION_MODES,
    VISIBLE_ATTRIBUTION_MODES,
)


_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SIZE_RE = re.compile(r"^(?P<size>\d+(?:\.\d+)?)\s*(?:px)?$", re.IGNORECASE)
_RANGE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*[\-–—]\s*\d+(?:\.\d+)?\s*(?:px|pt)\b", re.IGNORECASE)
_PAGE_RE = re.compile(r"(?m)^## P(?P<number>\d+)\s*$")


@dataclass(frozen=True, slots=True)
class TypographyToken:
    role: str
    font_family: str
    size: float
    weight: str
    line_height: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cell_key(value: str) -> str:
    return re.sub(r"[\s/_-]+", "", value).casefold()


def _tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    lines = text.splitlines()
    output: list[tuple[list[str], list[list[str]]]] = []
    index = 0
    while index + 1 < len(lines):
        header = lines[index].strip()
        separator = lines[index + 1].strip()
        if not header.startswith("|") or not separator.startswith("|") or "---" not in separator:
            index += 1
            continue
        headers = [cell.strip() for cell in header.strip("|").split("|")]
        rows: list[list[str]] = []
        index += 2
        while index < len(lines) and lines[index].strip().startswith("|"):
            cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
            if len(cells) < len(headers):
                cells.extend([""] * (len(headers) - len(cells)))
            rows.append(cells[: len(headers)])
            index += 1
        output.append((headers, rows))
    return output


def _page_sections(text: str) -> dict[int, str]:
    matches = list(_PAGE_RE.finditer(text))
    return {
        int(match.group("number")): text[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(text)]
        for index, match in enumerate(matches)
    }


def parse_design_typography(path: Path) -> dict[str, TypographyToken]:
    text = path.read_text(encoding="utf-8-sig")
    for headers, rows in _tables(text):
        keys = [_cell_key(header) for header in headers]
        role_index = next((i for i, key in enumerate(keys) if key in {"role", "角色", "typographyrole", "字号角色"}), None)
        size_index = next((i for i, key in enumerate(keys) if key in {"size", "sizepx", "字号", "字号px"}), None)
        font_index = next((i for i, key in enumerate(keys) if key in {"font", "fontfamily", "fontstack", "字体", "字体栈"}), None)
        weight_index = next((i for i, key in enumerate(keys) if key in {"weight", "fontweight", "字重"}), None)
        leading_index = next((i for i, key in enumerate(keys) if key in {"lineheight", "leading", "行距"}), None)
        if role_index is None or size_index is None:
            continue
        tokens: dict[str, TypographyToken] = {}
        for row in rows:
            role = row[role_index].strip()
            if not role:
                continue
            if not _ROLE_RE.fullmatch(role):
                raise ValueError(f"invalid typography role {role!r}; use lower_snake_case")
            match = _SIZE_RE.fullmatch(row[size_index].strip())
            if match is None or float(match.group("size")) <= 0:
                raise ValueError(f"typography role {role!r} has invalid unitless px size")
            if role in tokens:
                raise ValueError(f"duplicate typography role: {role}")
            tokens[role] = TypographyToken(
                role=role,
                font_family=row[font_index].strip() if font_index is not None else "",
                size=float(match.group("size")),
                weight=row[weight_index].strip() if weight_index is not None else "",
                line_height=row[leading_index].strip() if leading_index is not None else "",
            )
        if tokens:
            return tokens
    raise ValueError("design-system.md must contain a typography token table with role and exact size columns")


def _parse_lock_typography(path: Path) -> tuple[dict[str, str], str, tuple[int, int]]:
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(r"(?ms)^## typography\s*\n(?P<body>.*?)(?=^##\s|\Z)", text)
    if match is None:
        raise ValueError("spec_lock.md is missing ## typography")
    values: dict[str, str] = {}
    for row in match.group("body").splitlines():
        parsed = re.match(r"^\s*-\s*([^:]+):\s*(.*?)\s*$", row)
        if parsed:
            values[parsed.group(1).strip()] = parsed.group(2).strip()
    return values, text, match.span()


def compile_spec_lock_typography(project: Path) -> dict[str, Any]:
    project = project.resolve()
    design_path = project / "ppt-content" / "design" / "design-system.md"
    lock_path = project / "spec_lock.md"
    tokens = parse_design_typography(design_path)
    current, text, span = _parse_lock_typography(lock_path)
    family_keys = [key for key in ("font_family", "title_family", "body_family") if current.get(key)]
    default_family = next((token.font_family for token in tokens.values() if token.font_family), "Arial, sans-serif")
    rows = ["## typography"]
    for key in ("font_family", "title_family", "body_family"):
        rows.append(f"- {key}: {current.get(key) or default_family}")
    if "title" not in tokens:
        title_token = next(
            (token for role, token in tokens.items() if role in {"slide_title", "deck_title"} or role.endswith("_title")),
            next(iter(tokens.values())),
        )
        title_size = int(title_token.size) if title_token.size.is_integer() else title_token.size
        rows.append(f"- title: {title_size}")
    if "body" not in tokens:
        body_token = next(
            (token for role, token in tokens.items() if role.startswith("body")),
            next(iter(tokens.values())),
        )
        body_size = int(body_token.size) if body_token.size.is_integer() else body_token.size
        rows.append(f"- body: {body_size}")
    for role, token in tokens.items():
        size = int(token.size) if token.size.is_integer() else token.size
        rows.append(f"- {role}: {size}")
    replacement = "\n".join(rows) + "\n\n"
    updated = text[: span[0]] + replacement + text[span[1] :].lstrip("\n")
    lock_path.write_text(updated, encoding="utf-8", newline="\n")
    return {
        "spec_lock": str(lock_path),
        "roles": {role: token.to_dict() for role, token in tokens.items()},
        "preserved_family_keys": family_keys,
    }


def _normalize_empty(value: str) -> str:
    value = value.strip()
    return "" if value in {"", "-", "—", "(none)", "none"} else value


def validate_stage8_contract(project: Path) -> dict[str, Any]:
    project = project.resolve()
    errors: list[str] = []
    try:
        tokens = parse_design_typography(project / "ppt-content" / "design" / "design-system.md")
    except (OSError, ValueError) as error:
        tokens = {}
        errors.append(str(error))
    try:
        locked, _text, _span = _parse_lock_typography(project / "spec_lock.md")
    except (OSError, ValueError) as error:
        locked = {}
        errors.append(str(error))
    for role, token in tokens.items():
        raw = locked.get(role)
        if raw is None:
            errors.append(f"spec_lock typography is missing design token: {role}")
            continue
        try:
            if abs(float(raw) - token.size) > 0.001:
                errors.append(f"spec_lock typography {role}={raw} does not match design-system size {token.size:g}")
        except ValueError:
            errors.append(f"spec_lock typography {role} must be a unitless number")

    plan_path = project / "production" / "slide-production-plan.md"
    plan_text = plan_path.read_text(encoding="utf-8-sig") if plan_path.is_file() else ""
    if _RANGE_RE.search(plan_text):
        errors.append("production plan contains an ambiguous typography size range; use a named typography_role")
    sections = _page_sections(plan_text)
    sparse_counts: dict[str, int] = {}
    attribution_rows: dict[int, dict[str, dict[str, str]]] = {}
    for page, section in sections.items():
        found_text_table = False
        page_attributions: dict[str, dict[str, str]] = {}
        for headers, rows in _tables(section):
            keys = [_cell_key(header) for header in headers]
            role_index = next((i for i, key in enumerate(keys) if key in {"typographyrole", "字号角色"}), None)
            content_index = next((i for i, key in enumerate(keys) if key in {"contentkey", "内容键"}), None)
            if role_index is not None and content_index is not None:
                found_text_table = True
                for row in rows:
                    content_key = row[content_index].strip()
                    role = row[role_index].strip()
                    if not content_key:
                        continue
                    if not role:
                        errors.append(f"P{page:02d} text object {content_key} has no typography_role")
                    elif role.startswith("sparse:"):
                        sparse_counts[role] = sparse_counts.get(role, 0) + 1
                    elif role not in tokens:
                        errors.append(f"P{page:02d} text object {content_key} references unknown typography_role {role!r}")
            asset_index = next((i for i, key in enumerate(keys) if key in {"assetid", "素材id"}), None)
            mode_index = next((i for i, key in enumerate(keys) if key in {"displayattributionmode", "attributionmode", "署名模式"}), None)
            if asset_index is None or mode_index is None:
                continue
            lookup = {key: index for index, key in enumerate(keys)}
            for row in rows:
                asset_id = row[asset_index].strip()
                if not asset_id:
                    continue
                page_attributions[asset_id] = {
                    "verification_status": row[lookup.get("verificationstatus", lookup.get("验证状态", -1))].strip() if ("verificationstatus" in lookup or "验证状态" in lookup) else "",
                    "verification_risk": row[lookup.get("verificationrisk", lookup.get("验证风险", -1))].strip() if ("verificationrisk" in lookup or "验证风险" in lookup) else "",
                    "verification_method": row[lookup.get("verificationmethod", lookup.get("验证方法", -1))].strip() if ("verificationmethod" in lookup or "验证方法" in lookup) else "",
                    "evidence_strength": row[lookup.get("evidencestrength", lookup.get("证据强度", -1))].strip() if ("evidencestrength" in lookup or "证据强度" in lookup) else "",
                    "display_attribution_mode": row[mode_index].strip(),
                    "display_attribution": row[lookup.get("displayattribution", lookup.get("显示署名", -1))].strip() if ("displayattribution" in lookup or "显示署名" in lookup) else "",
                    "typography_role": row[lookup.get("typographyrole", lookup.get("字号角色", -1))].strip() if ("typographyrole" in lookup or "字号角色" in lookup) else "",
                    "placement": row[lookup.get("placement", lookup.get("位置", -1))].strip() if ("placement" in lookup or "位置" in lookup) else "",
                }
        if not found_text_table:
            errors.append(f"P{page:02d} production plan has no text-object table with typography_role")
        attribution_rows[page] = page_attributions
    for sparse, count in sparse_counts.items():
        if count > 2:
            errors.append(f"sparse typography exception {sparse!r} occurs {count} times; repeated treatment needs a named role")

    manifest = AssetManifest(project)
    manifest_errors = manifest.validation_report()["schema_errors"]
    errors.extend(f"manifest: {error}" for error in manifest_errors)
    for item in manifest.payload.get("items", []):
        page = int(item.get("slide_number") or 0)
        asset_id = str(item.get("asset_id") or "")
        row = attribution_rows.get(page, {}).get(asset_id)
        if row is None:
            errors.append(f"P{page:02d} production plan is missing Stage 7 asset decision row for {asset_id}")
            continue
        for field in ("verification_status", "verification_risk", "verification_method", "evidence_strength", "display_attribution_mode"):
            expected = str(item.get(field) or "")
            if row[field] != expected:
                errors.append(f"P{page:02d} {asset_id} changes Stage 7 {field}: expected {expected!r}, got {row[field]!r}")
        expected_display = str(item.get("display_attribution") or "")
        if _normalize_empty(row["display_attribution"]) != expected_display:
            errors.append(f"P{page:02d} {asset_id} display_attribution does not match asset-manifest.json")
        mode = str(item.get("display_attribution_mode") or "")
        if mode in VISIBLE_ATTRIBUTION_MODES:
            if not row["placement"]:
                errors.append(f"P{page:02d} {asset_id} visible attribution has no placement")
            if row["typography_role"] != "attribution":
                errors.append(f"P{page:02d} {asset_id} visible attribution must use typography_role attribution")
        elif mode not in DISPLAY_ATTRIBUTION_MODES:
            errors.append(f"P{page:02d} {asset_id} has invalid display attribution mode {mode!r}")

    return {
        "status": "passed" if not errors else "failed",
        "stage8_ready": not errors,
        "errors": errors,
        "typography_roles": {role: token.to_dict() for role, token in tokens.items()},
        "manifest_items": len(manifest.payload.get("items", [])),
    }

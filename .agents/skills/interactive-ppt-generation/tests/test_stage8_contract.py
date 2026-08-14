from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

from stage8_contract import compile_spec_lock_typography, validate_stage8_contract
from svg_quality_checker import SVGQualityChecker
from visual_assets.asset_manifest import AssetManifest
from visual_assets.attribution_policy import (
    COMPACT_SOURCE,
    FULL_CREDIT,
    PROVENANCE_ONLY,
    resolve_display_attribution,
)


class DisplayAttributionPolicyTests(unittest.TestCase):
    def test_source_attribution_request_uses_compact_source_with_unknown_license(self) -> None:
        decision = resolve_display_attribution({
            "source_type": "web",
            "verification_risk": "evidence-critical",
            "author": "NeurIPS Proceedings",
            "published_at": "2017",
            "license_name": "See official proceedings record",
            "source_attribution_required": True,
        })
        self.assertEqual(decision.mode, COMPACT_SOURCE)
        self.assertEqual(decision.display_attribution, "NeurIPS Proceedings · 2017")

    def test_factual_public_domain_uses_provenance_only(self) -> None:
        decision = resolve_display_attribution({
            "source_type": "web",
            "verification_risk": "factual-illustrative",
            "author": "NASA",
            "license_name": "Public domain",
        })
        self.assertEqual(decision.mode, PROVENANCE_ONLY)
        self.assertEqual(decision.display_attribution, "")

    def test_compact_source_omits_provider_internal_hash(self) -> None:
        decision = resolve_display_attribution({
            "source_type": "web",
            "verification_risk": "evidence-critical",
            "author": "NeurIPS Proceedings",
            "published_at": "2012",
            "license_name": "See official proceedings record",
            "source_attribution_required": True,
            "provenance": {
                "entity_document_id": "c399862d3b9d6b76c8436e924a68c45b",
            },
        })
        self.assertEqual(decision.display_attribution, "NeurIPS Proceedings · 2012")

    def test_license_can_raise_attribution_without_changing_semantic_risk(self) -> None:
        item = {
            "source_type": "web",
            "verification_risk": "factual-illustrative",
            "author": "Example Photographer",
            "license_name": "CC BY 4.0",
        }
        decision = resolve_display_attribution(item)
        self.assertEqual(decision.mode, FULL_CREDIT)
        self.assertEqual(item["verification_risk"], "factual-illustrative")


class Stage8ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "ppt-content" / "design").mkdir(parents=True)
        (self.project / "ppt-content" / "visuals").mkdir(parents=True)
        (self.project / "production").mkdir(parents=True)
        (self.project / "research" / "visual-assets").mkdir(parents=True)
        (self.project / "validation").mkdir(parents=True)
        (self.project / "ppt-content" / "design" / "design-system.md").write_text(
            """# Design System

## 字体与字号

| 角色 | 字体栈 | 字号 | 字重 |
|---|---|---:|---:|
| slide_title | Arial, sans-serif | 38 | 700 |
| body | Arial, sans-serif | 20 | 400 |
| caption | Arial, sans-serif | 14 | 400 |
| attribution | Arial, sans-serif | 11 | 400 |
""",
            encoding="utf-8",
        )
        (self.project / "spec_lock.md").write_text(
            """# Execution Lock

## canvas
- viewBox: 0 0 1280 720

## typography
- font_family: Arial, sans-serif
- title_family: Arial, sans-serif
- body_family: Arial, sans-serif
- title: 42
- body: 24

## pptx_structure
- mode: flat
""",
            encoding="utf-8",
        )
        (self.project / "research" / "visual-assets" / "visual-requirements.json").write_text(
            json.dumps({"slots": []}), encoding="utf-8"
        )
        (self.project / "ppt-content" / "visuals" / "asset-manifest.json").write_text(
            json.dumps({"schema_version": 4, "items": [], "slots": {}}), encoding="utf-8"
        )
        self._write_plan()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_plan(self, attribution_row: str = "") -> None:
        self.project.joinpath("production", "slide-production-plan.md").write_text(
            """# Production Plan

## P01

### 文字对象

| 内容键 | x | y | w | h | 字号角色 |
|---|---:|---:|---:|---:|---|
| P01-title | 72 | 60 | 900 | 60 | slide_title |
| P01-body | 72 | 150 | 900 | 120 | body |

### Stage 7 素材决策

| 素材 ID | 验证状态 | 验证风险 | 验证方法 | 证据强度 | 署名模式 | 显示署名 | 位置 | 字号角色 |
|---|---|---|---|---|---|---|---|---|
""" + attribution_row + "\n",
            encoding="utf-8",
        )

    def test_design_roles_compile_into_spec_lock_and_validate(self) -> None:
        result = compile_spec_lock_typography(self.project)
        self.assertEqual(set(result["roles"]), {"slide_title", "body", "caption", "attribution"})
        report = validate_stage8_contract(self.project)
        self.assertTrue(report["stage8_ready"], report["errors"])

    def test_repeated_structure_without_named_role_fails_before_svg(self) -> None:
        compile_spec_lock_typography(self.project)
        plan = self.project / "production" / "slide-production-plan.md"
        plan.write_text(
            plan.read_text(encoding="utf-8")
            .replace("| P01-body | 72 | 150 | 900 | 120 | body |", "| P01-body | 72 | 150 | 900 | 120 | 19–42 px |"),
            encoding="utf-8",
        )
        report = validate_stage8_contract(self.project)
        self.assertFalse(report["stage8_ready"])
        self.assertTrue(any("ambiguous typography size range" in error for error in report["errors"]))

    def test_cross_stage_asset_decision_cannot_be_changed(self) -> None:
        compile_spec_lock_typography(self.project)
        asset = self.project / "ppt-content" / "visuals" / "asset.png"
        asset.write_bytes(b"asset")
        manifest = AssetManifest(self.project)
        manifest.payload = {
            "schema_version": 2,
            "items": [{
                "asset_id": "asset-P01-evidence",
                "slot_id": "P01-evidence",
                "slide_number": 1,
                "local_path": "ppt-content/visuals/asset.png",
                "filename": "asset.png",
                "source_type": "web",
                "source_page_url": "https://example.org/paper",
                "original_image_url": "https://example.org/paper.pdf",
                "source_domain": "example.org",
                "search_query": "original paper",
                "author": "NeurIPS Proceedings",
                "license_name": "See official proceedings record",
                "published_at": "2017",
                "verification_risk": "evidence-critical",
                "verification_method": "direct-provenance",
                "verification_evidence": {"record": "paper"},
                "evidence_strength": "DIRECT_PROVENANCE",
                "confidence": 1.0,
                "verification_timestamp": "2026-08-14T00:00:00+00:00",
                "quality_gate_passed": True,
                "provenance": {},
            }],
            "slots": {"P01-evidence": {"status": "selected", "quality_gate_passed": True}},
        }
        manifest._save()
        item = AssetManifest(self.project).payload["items"][0]
        row = (
            f"| {item['asset_id']} | {item['verification_status']} | {item['verification_risk']} | {item['verification_method']} | "
            f"{item['evidence_strength']} | {item['display_attribution_mode']} | "
            f"{item['display_attribution']} | footer | attribution |"
        )
        self._write_plan(row)
        self.assertTrue(validate_stage8_contract(self.project)["stage8_ready"])
        self._write_plan(row.replace("presentation-grade", "evidence-critical", 1))
        report = validate_stage8_contract(self.project)
        self.assertFalse(report["stage8_ready"])
        self.assertTrue(any("changes Stage 7 verification_risk" in error for error in report["errors"]))

    def test_provenance_only_asset_needs_no_visible_credit(self) -> None:
        checker = SVGQualityChecker()
        manifest = self.project / "ppt-content" / "visuals" / "asset-manifest.json"
        photo = self.project / "ppt-content" / "visuals" / "photo.jpg"
        photo.write_bytes(b"photo")
        manifest.write_text(json.dumps({
            "schema_version": 4,
            "items": [{
                "asset_id": "asset-P01-photo",
                "slot_id": "P01-photo",
                "slide_number": 1,
                "filename": "photo.jpg",
                "local_path": "ppt-content/visuals/photo.jpg",
                "source_type": "web",
                "source_page_url": "https://example.org/page",
                "original_image_url": "https://example.org/photo.jpg",
                "source_domain": "example.org",
                "search_query": "scene",
                "verification_status": "presentation-verified",
                "display_attribution_mode": "provenance-only",
                "display_attribution": "",
            }],
            "slots": {},
        }), encoding="utf-8")
        svg_path = self.project / "svg_output" / "P01.svg"
        svg_path.parent.mkdir()
        result = {"errors": [], "warnings": [], "info": {}}
        checker._check_sourced_image_attribution('<svg><image href="photo.jpg"/></svg>', svg_path, result)
        self.assertEqual(result["errors"], [])

    def test_checker_rejects_arbitrary_domain_token_instead_of_canonical_text(self) -> None:
        checker = SVGQualityChecker()
        manifest = self.project / "ppt-content" / "visuals" / "asset-manifest.json"
        paper = self.project / "ppt-content" / "visuals" / "paper.png"
        paper.write_bytes(b"paper")
        manifest.write_text(json.dumps({
            "schema_version": 4,
            "items": [{
                "asset_id": "asset-P01-paper",
                "slot_id": "P01-paper",
                "slide_number": 1,
                "filename": "paper.png",
                "local_path": "ppt-content/visuals/paper.png",
                "source_type": "web",
                "source_page_url": "https://example.org/page",
                "original_image_url": "https://example.org/paper.png",
                "source_domain": "example.org",
                "search_query": "paper",
                "verification_status": "presentation-verified",
                "display_attribution_mode": "compact-source",
                "display_attribution": "NeurIPS Proceedings · 2017",
            }],
            "slots": {},
        }), encoding="utf-8")
        svg_path = self.project / "svg_output" / "P01.svg"
        svg_path.parent.mkdir()
        result = {"errors": [], "warnings": [], "info": {}}
        checker._check_sourced_image_attribution(
            '<svg><image href="paper.png"/><text>example.org</text></svg>', svg_path, result
        )
        self.assertTrue(any("canonical Stage 7 display_attribution" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()

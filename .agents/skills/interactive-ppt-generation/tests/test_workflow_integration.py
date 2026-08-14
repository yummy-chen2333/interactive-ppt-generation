from __future__ import annotations

import json
import hashlib
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml
from PIL import Image


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

from final_acceptance_validator import (
    AcceptanceError,
    _audit_manifest,
    _audit_package,
    validate_project,
)
from init_project import initialize_project
from run_backend import main as run_backend_main
from svg_to_pptx.pptx_package.discovery import find_notes_files
from visual_assets.asset_manifest import AssetManifest
from visual_assets.image_analyzer import ImageAnalyzer
from visual_assets.models import SearchCandidate, SlotRequirement
from visual_assets.source_policy import SourcePolicyResolver
from workflow_state import GATE_ORDER, WorkflowStateController, WorkflowStateError


POLICY_PATH = SKILL / "references" / "source-policy-profiles.yaml"


def _replace_status(path: Path, value: str) -> None:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^- 状态：.*$", f"- 状态：{value}", text)
    path.write_text(text, encoding="utf-8")


def _fill_pages(path: Path, page_count: int, marker: str) -> None:
    text = path.read_text(encoding="utf-8")
    for page in range(1, page_count + 1):
        heading = f"## P{page:02d}"
        start = text.index(heading)
        next_start = text.find("\n## P", start + len(heading))
        end = len(text) if next_start < 0 else next_start
        section = text[start:end]
        section += f"\n\n{marker} page {page} substantive content for deterministic validation.\n"
        text = text[:start] + section + text[end:]
    path.write_text(text, encoding="utf-8")


def prepare_project(
    project: Path, page_count: int = 1, *, with_required_image: bool = False
) -> WorkflowStateController:
    initialize_project(project, page_count)
    WorkflowStateController(project).record_user_assets("none")
    _replace_status(project / "narrative" / "presentation-brief.md", "已确认")
    _replace_status(project / "narrative" / "presentation-structure.md", "已确认")
    for relative, marker in (
        ("narrative/slide-intent.md", "Intent"),
        ("narrative/speaker-notes.md", "Canonical speaker notes"),
        ("ppt-content/text/slide-copy.md", "Visible slide copy"),
        ("production/slide-production-plan.md", "Production coordinates"),
        ("production/layout-review.md", "Layout reviewed"),
    ):
        _fill_pages(project / relative, page_count, marker)
    plan = project / "production" / "slide-production-plan.md"
    plan.write_text(plan.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    plan_text = plan.read_text(encoding="utf-8")
    for page in range(1, page_count + 1):
        heading = f"## P{page:02d}"
        start = plan_text.index(heading)
        next_start = plan_text.find("\n## P", start + len(heading))
        end = len(plan_text) if next_start < 0 else next_start
        section = plan_text[start:end].replace(
            "\n### 视觉素材",
            f"\n| P{page:02d}-title | 150 | 180 | 900 | 80 | slide_title | 1/2 | left | safe |\n\n### 视觉素材",
            1,
        )
        plan_text = plan_text[:start] + section + plan_text[end:]
    plan.write_text(plan_text, encoding="utf-8")
    review = project / "production" / "layout-review.md"
    review.write_text(
        review.read_text(encoding="utf-8")
        .replace("| pending | pending | pending |", "| passed | passed | pending |")
        .replace("| pending | not-applicable |", "| passed | not-applicable |")
        .replace("status: pending", "status: passed"),
        encoding="utf-8",
    )
    (project / "research" / "research-notes.md").write_text(
        "# Research Notes\n\nVerified fact and source record.\n", encoding="utf-8"
    )
    (project / "ppt-content" / "design" / "design-system.md").write_text(
        """# Design System

Canvas 16:9, blue palette, Arial typography, consistent margins.

## 字体与字号

| 角色 | 字体栈 | 字号 | 字重 |
|---|---|---:|---:|
| slide_title | Arial, sans-serif | 52 | 700 |
| body | Arial, sans-serif | 26 | 400 |
| attribution | Arial, sans-serif | 16 | 400 |
""",
        encoding="utf-8",
    )
    (project / "ppt-content" / "design" / "page-layouts.md").write_text(
        "# Page Layouts\n\nThe cover uses a locked title-and-subtitle composition.\n\n"
        "| key | page | structure |\n|---|---|---|\n| cover | P01 | title and subtitle |\n",
        encoding="utf-8",
    )
    (project / "spec_lock.md").write_text(
        """# Execution Lock

## canvas
- viewBox: 0 0 1280 720
- format: PPT 16:9

## communication
- audience: test audience
- objective: verify workflow integration
- core_message: deterministic workflow passes
- consumption_mode: presentation

## mode
- mode: explanatory

## visual_style
- visual_style: minimal

## colors
- background: #F4F7FB
- primary: #175CD3
- body_text: #172B4D

## typography
- font_family: Arial, sans-serif
- title_family: Arial, sans-serif
- body_family: Arial, sans-serif
- title: 52
- slide_title: 52
- body: 26
- attribution: 16

## pptx_structure
- mode: flat
""",
        encoding="utf-8",
    )
    requirements = project / "research" / "visual-assets" / "visual-requirements.json"
    slots = []
    manifest_items = []
    manifest_slots = {}
    image_markup = ""
    if with_required_image:
        asset = project / "ppt-content" / "visuals" / "assets" / "P01" / "test-image.png"
        asset.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (1600, 900))
        pixels = image.load()
        for y in range(900):
            for x in range(1600):
                pixels[x, y] = ((x * 3) % 256, (y * 5) % 256, (x + y) % 256)
        image.save(asset)
        asset_hash = hashlib.sha256(asset.read_bytes()).hexdigest()
        slots = [{"slot_id": "P01-main", "slide_number": 1, "required": True}]
        manifest_items = [{
            "asset_id": "asset-P01-main",
            "slot_id": "P01-main",
            "slide_number": 1,
            "local_path": "ppt-content/visuals/assets/P01/test-image.png",
            "filename": "test-image.png",
            "source_type": "web",
            "source_page_url": "https://example.org/source",
            "original_image_url": "https://example.org/test-image.png",
            "source_domain": "example.org",
            "search_query": "integration test image",
            "content_sha256": asset_hash,
            "source_attribution_required": True,
            "author": "Example Author",
            "license_name": "CC BY 4.0",
            "verification_risk": "factual-illustrative",
            "verification_method": "source-grounded",
            "verification_evidence": {"source_page": "https://example.org/source"},
            "evidence_strength": "SOURCE_GROUNDED",
            "confidence": 0.9,
            "verification_timestamp": "2026-08-14T00:00:00+00:00",
            "quality_gate_passed": True,
            "provenance": {},
        }]
        manifest_slots = {"P01-main": {
            "status": "selected",
            "quality_gate_passed": True,
            "verification_risk": "factual-illustrative",
            "verification_method": "source-grounded",
            "evidence_strength": "SOURCE_GROUNDED",
        }}
        image_markup = (
            '<image id="hero" href="../ppt-content/visuals/assets/P01/test-image.png" '
            'x="760" y="170" width="360" height="202.5"/>'
            '<text id="attribution" x="760" y="400" font-family="Arial, sans-serif" '
            'font-size="16" fill="#172B4D">Example Author · CC BY 4.0</text>'
        )
    requirements.write_text(
        json.dumps({"schema_version": 1, "deck": {"theme": "test"}, "slots": slots}),
        encoding="utf-8",
    )
    manifest = project / "ppt-content" / "visuals" / "asset-manifest.json"
    manifest.write_text(
        json.dumps({
            "schema_version": 2,
            "updated_at": None,
            "items": manifest_items,
            "slots": manifest_slots,
        }),
        encoding="utf-8",
    )
    AssetManifest(project)._save()
    if with_required_image:
        canonical = AssetManifest(project).payload["items"][0]
        plan_text = plan.read_text(encoding="utf-8")
        decision_row = (
            "| asset-P01-main | {status} | {risk} | {method} | {strength} | {mode} | {display} | x=760 y=400 | attribution |"
        ).format(
            status=canonical["verification_status"],
            risk=canonical["verification_risk"],
            method=canonical["verification_method"],
            strength=canonical["evidence_strength"],
            mode=canonical["display_attribution_mode"],
            display=canonical["display_attribution"],
        )
        marker = "|---|---|---|---|---|---|---|---|---|"
        plan.write_text(plan_text.replace(marker, marker + "\n" + decision_row, 1), encoding="utf-8")
    svg = project / "svg_output" / "P01.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" data-pptx-page-role="cover">'
        '<rect id="background" data-pptx-role="background" x="0" y="0" width="1280" height="720" fill="#F4F7FB"/>'
        '<g id="content" data-pptx-bounds="100 100 1080 520">'
        '<text id="title" x="150" y="260" font-family="Arial, sans-serif" font-size="52" fill="#172B4D">Workflow Integration</text>'
        '<text id="subtitle" x="150" y="330" font-family="Arial, sans-serif" font-size="26" fill="#175CD3">Canonical notes and final validation</text>'
        + image_markup +
        '</g></svg>',
        encoding="utf-8",
    )
    controller = WorkflowStateController(project)
    for gate in GATE_ORDER[:10]:
        if gate == "template_route":
            controller.close_gate(gate, template_route="free-design")
        else:
            controller.close_gate(gate)
    return controller


class WorkflowStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        initialize_project(self.project, 1)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_stage_cannot_skip(self) -> None:
        controller = WorkflowStateController(self.project)
        with self.assertRaises(WorkflowStateError):
            controller.close_gate("text_plan")

    def test_claimed_completed_with_missing_artifact_fails(self) -> None:
        state = yaml.safe_load((self.project / "project-state.yaml").read_text(encoding="utf-8"))
        state["gates"]["presentation_brief"] = "confirmed"
        state["current_stage"] = "text_plan"
        (self.project / "project-state.yaml").write_text(
            yaml.safe_dump(state, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        report = WorkflowStateController(self.project).inspect()
        self.assertTrue(report["state_stale"])
        self.assertFalse(report["gates"]["presentation_brief"]["artifact_current"])

    def test_state_stale_is_detected_after_artifact_change(self) -> None:
        controller = WorkflowStateController(self.project)
        controller.close_gate("template_route", template_route="free-design")
        _replace_status(self.project / "narrative" / "presentation-brief.md", "已确认")
        controller.close_gate("presentation_brief")
        with (self.project / "narrative" / "presentation-brief.md").open("a", encoding="utf-8") as stream:
            stream.write("\nChanged after gate close.\n")
        report = controller.inspect()
        self.assertTrue(report["state_stale"])
        self.assertEqual(report["last_completed_gate"], "template_route")

    def test_resume_returns_to_last_artifact_current_gate(self) -> None:
        controller = WorkflowStateController(self.project)
        controller.close_gate("template_route", template_route="free-design")
        _replace_status(self.project / "narrative" / "presentation-brief.md", "已确认")
        controller.close_gate("presentation_brief")
        with (self.project / "narrative" / "presentation-brief.md").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write("\nStale edit after confirmation.\n")
        report = controller.resume()
        self.assertEqual(report["last_completed_gate"], "template_route")
        self.assertEqual(report["next_stage"], "presentation_brief")
        state = yaml.safe_load((self.project / "project-state.yaml").read_text(encoding="utf-8"))
        self.assertEqual(state["current_stage"], "presentation_brief")
        self.assertEqual(state["gates"]["presentation_brief"], "stale")

    def test_stage7_incomplete_cannot_enter_backend(self) -> None:
        controller = WorkflowStateController(self.project)
        with self.assertRaises(WorkflowStateError):
            controller.assert_ready_for_backend()
        (self.project / "spec_lock.md").write_text(
            "# Execution Lock\n\nIncomplete project must still be rejected by the gate controller.\n",
            encoding="utf-8",
        )
        self.assertEqual(run_backend_main([str(self.project)]), 2)
        self.assertFalse((self.project / "exports" / "latest.json").exists())

    def test_missing_production_plan_cannot_enter_backend(self) -> None:
        controller = prepare_project(self.project)
        (self.project / "production" / "slide-production-plan.md").unlink()
        with self.assertRaises(WorkflowStateError):
            controller.assert_ready_for_backend()


class NotesAndPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_canonical_notes_are_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "narrative").mkdir()
            (project / "narrative" / "speaker-notes.md").write_text(
                "# Speaker Notes\n\n## P01\n\nFirst canonical note.\n\n## P02\n\nSecond canonical note.\n",
                encoding="utf-8",
            )
            svg_files = [project / "P01.svg", project / "P02.svg"]
            notes = find_notes_files(project, svg_files)
            self.assertIn("First canonical note", notes["P01"])
            self.assertIn("Second canonical note", notes["P02"])

    def test_source_policy_word_boundaries(self) -> None:
        resolver = SourcePolicyResolver(POLICY_PATH)
        ai = SlotRequirement("a", 1, "Artificial intelligence", "AI", "technology", "AI")
        agriculture = SlotRequirement("b", 1, "Agriculture technology", "smart farm", "device", "sensor")
        self.assertEqual(resolver.select(ai).name, "company-product-technology")
        self.assertEqual(resolver.select(agriculture).name, "company-product-technology")
        self.assertNotEqual(resolver.select(ai).name, "artwork-museum-object")
        self.assertNotEqual(resolver.select(agriculture).name, "humanities-culture")

    async def test_legacy_strict_historical_portrait_passes_without_vlm(self) -> None:
        import httpx

        slot = SlotRequirement(
            "strict", 1, "History", "Historical portrait", "Identify the real person", "Ada Lovelace",
            required_subject="Ada Lovelace",
            required_asset_type="historical person portrait",
            required_relationship="authenticated historical portrait",
            authenticity_requirement="strict",
            visual_type="public figure portrait",
        )
        candidate = SearchCandidate(
            "c", "fake", "query", "Ada Lovelace official portrait", "https://example.test/a.jpg",
            "https://example.test/source", "example.test", description="historical portrait",
            source_tier=1, validation={"clarity_score": 90}, width=1200, height=1600,
        )
        async with httpx.AsyncClient() as client:
            analyzer = ImageAnalyzer(client)
            with patch.dict("os.environ", {}, clear=True):
                analyzer.vision.api_key = None
                analyzer.vision.model = None
                result = await analyzer.analyze(Path("unused.jpg"), candidate, slot, SourcePolicyResolver(POLICY_PATH).profiles["public-figure-biography"])
        self.assertFalse(result.get("capability_degraded", False))
        self.assertTrue(result["hard_gate_verdict"]["passed"])


class FinalAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_full_minimal_workflow_to_final_pptx(self) -> None:
        controller = prepare_project(self.project, with_required_image=True)
        exit_code = run_backend_main([str(self.project), "--title", "integration"])
        self.assertEqual(exit_code, 0)
        report = controller.inspect()
        self.assertEqual(report["last_completed_gate"], "final_validation")
        self.assertEqual(report["next_stage"], "completed")
        latest = json.loads((self.project / "exports" / "latest.json").read_text(encoding="utf-8"))
        package = _audit_package(Path(latest["path"]), 1)
        self.assertEqual(package["notes_count"], 1)
        self.assertGreaterEqual(package["image_part_count"], 1)
        self.assertIn("Canonical speaker notes", package["notes_text"][1])

    def test_final_validator_failure_missing_notes(self) -> None:
        self.project.mkdir(parents=True)
        exports = self.project / "exports"
        exports.mkdir()
        pptx = exports / "bad.pptx"
        with zipfile.ZipFile(pptx, "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>')
        latest = {
            "path": str(pptx),
            "sha256": __import__("hashlib").sha256(pptx.read_bytes()).hexdigest(),
            "slide_count": 1,
        }
        (exports / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
        with self.assertRaises(AcceptanceError):
            validate_project(self.project)

    def test_final_validator_rejects_notes_that_differ_from_canonical(self) -> None:
        from svg_to_pptx.pptx_package.builder import create_pptx_with_native_svg

        prepare_project(self.project)
        output = self.project / "exports" / "mismatched-notes.pptx"
        create_pptx_with_native_svg(
            [self.project / "svg_output" / "P01.svg"],
            output,
            notes={"P01": "Different embedded speaker note."},
            verbose=False,
            pptx_structure="flat",
        )
        (self.project / "exports" / "latest.json").write_text(
            json.dumps({
                "path": str(output),
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "slide_count": 1,
            }),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(AcceptanceError, "do not match canonical"):
            validate_project(self.project)

    def test_manifest_asset_must_be_embedded_on_declared_slide(self) -> None:
        asset = self.project / "ppt-content" / "visuals" / "assets" / "P01" / "asset.png"
        asset.parent.mkdir(parents=True)
        Image.new("RGB", (64, 64), "red").save(asset)
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        manifest = self.project / "ppt-content" / "visuals" / "asset-manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps({
                "schema_version": 1,
                "items": [{
                    "asset_id": "asset-P01-main",
                    "slot_id": "P01-main",
                    "slide_number": 1,
                    "local_path": "ppt-content/visuals/assets/P01/asset.png",
                    "content_sha256": digest,
                    "source_type": "web",
                    "source_page_url": "https://example.org/source",
                    "original_image_url": "https://example.org/asset.png",
                    "source_domain": "example.org",
                    "search_query": "red test asset",
                }],
                "slots": {
                    "P01-main": {
                        "status": "selected",
                        "quality_gate_passed": True,
                    }
                },
            }),
            encoding="utf-8",
        )
        pptx = self.project / "exports" / "wrong-slide.pptx"
        pptx.parent.mkdir(parents=True)
        with zipfile.ZipFile(pptx, "w") as archive:
            archive.writestr("ppt/media/image1.png", asset.read_bytes())
        package = {
            "pptx_path": str(pptx),
            "image_part_count": 1,
            "slide_image_parts": {"1": [], "2": ["ppt/media/image1.png"]},
        }
        with self.assertRaisesRegex(AcceptanceError, "P01-main@P01"):
            _audit_manifest(self.project, package)


if __name__ == "__main__":
    unittest.main()

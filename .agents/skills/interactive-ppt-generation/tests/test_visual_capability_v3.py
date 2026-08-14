from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from PIL import Image


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from visual_assets.config import RetrievalConfig
from visual_assets.image_analyzer import DeterministicImageAnalyzer
from visual_assets.models import SearchCandidate, SlotRequirement
from visual_assets.pipeline import VisualAssetPipeline
from visual_assets.search_adapter import ImageSearchAdapter
from visual_assets.search_adapter import NeurIPSPaperSearchAdapter
from visual_assets.source_policy import SourcePolicy, SourcePolicyResolver
from visual_assets.verification import resolve_verification_mode
from visual_assets.verification import SourceGroundedVerifier
from visual_assets.query_builder import QueryBuilder
from visual_assets.download_manager import DownloadManager
from visual_assets.retrieval_cache import RetrievalCache
from visual_assets.retrieval_state import DomainCircuitBreaker


POLICY_PATH = Path(__file__).resolve().parents[1] / "references" / "source-policy-profiles.yaml"


def image_bytes() -> bytes:
    image = Image.new("RGB", (1400, 900), (72, 96, 128))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


IMAGE = image_bytes()


class StaticAdapter(ImageSearchAdapter):
    name = "static"
    capability_kind = "general-web"

    def __init__(self, candidates: list[SearchCandidate]):
        self.candidates = candidates
        self.calls = 0

    async def search(self, query: str, limit: int, policy: SourcePolicy) -> list[SearchCandidate]:
        self.calls += 1
        return self.candidates[:limit]


def config() -> RetrievalConfig:
    return RetrievalConfig.from_env({
        "query_budget": 1,
        "results_per_query": 4,
        "candidate_budget": 4,
        "metadata_shortlist": 4,
        "download_budget": 2,
        "visual_analysis_budget": 1,
        "operation_timeout_seconds": 1.0,
        "slot_deadline_seconds": 4.0,
        "no_progress_seconds": 1.0,
        "good_enough_threshold": 65.0,
    })


def paper_slot() -> SlotRequirement:
    return SlotRequirement(
        slot_id="P01-paper",
        slide_number=1,
        deck_theme="History of artificial intelligence",
        slide_topic="The original Transformer paper",
        purpose="Anchor the claim in the original publication",
        subject="Attention Is All You Need",
        required_subject="Attention Is All You Need paper",
        required_asset_type="original research paper page",
        required_relationship="original 2017 publication",
        forbidden_asset_types=["blog infographic"],
        authenticity_requirement="strict",
        visual_type="real-evidence",
        entity_aliases=["Vaswani et al. 2017"],
        queries=["Attention Is All You Need 2017"],
    )


def visual_slot() -> SlotRequirement:
    return SlotRequirement(
        slot_id="P02-robot",
        slide_number=2,
        deck_theme="AI agents",
        slide_topic="Robots acting under human oversight",
        purpose="Show a real action scene",
        subject="AI-enabled robot with human oversight",
        required_subject="real AI-enabled robot",
        required_asset_type="authentic real-world photograph",
        required_relationship="robot performing a task in a real environment with human oversight visible",
        forbidden_asset_types=["render", "illustration"],
        authenticity_requirement="strict",
        visual_type="real-scene",
        queries=["robot human oversight real scene"],
    )


def candidate(provider: str, title: str, record_type: str, image_url: str) -> SearchCandidate:
    record_url = f"https://records.test/{provider}/record/1"
    return SearchCandidate(
        candidate_id=f"{provider}-1",
        provider=provider,
        query=title,
        title=title,
        description=title,
        image_url=image_url,
        source_page_url=record_url,
        source_domain="records.test",
        width=1400,
        height=900,
        credit="Authoritative provider",
        provenance={
            "provider_record_url": record_url,
            "entity_document_id": f"{provider}:1",
            "record_title": title,
            "record_subject": title,
            "record_type": record_type,
            "relation_type": "structured-record-primary-asset",
            "direct_asset_relation": True,
            "asset_url": image_url,
            "provider_verified": True,
        },
    )


class VisualCapabilityV3Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.resolver = SourcePolicyResolver(POLICY_PATH)

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def _pipeline_result(self, slot: SlotRequirement, item: SearchCandidate) -> tuple[dict, StaticAdapter]:
        adapter = StaticAdapter([item])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=IMAGE, headers={"content-type": "image/png"}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            pipeline = VisualAssetPipeline(
                self.project,
                client,
                config=config(),
                adapters=[adapter],
                policy_path=POLICY_PATH,
            )
            pipeline.analyzer.vision.api_key = None
            pipeline.analyzer.vision.model = None
            result = await pipeline.run_slot(slot)
        return result, adapter

    async def test_official_paper_uses_presentation_grade_without_vlm(self) -> None:
        item = candidate(
            "neurips-proceedings",
            "Attention Is All You Need 2017 Transformer paper",
            "research-paper",
            "https://assets.test/transformer.png",
        )
        result, _ = await self._pipeline_result(paper_slot(), item)
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["verification"]["chosen_path"], "presentation-source-context")
        self.assertEqual(result["selected_asset"]["verification_risk"], "presentation-grade")
        self.assertEqual(result["selected_asset"]["verification_status"], "presentation-verified")

    async def test_nasa_source_page_and_caption_pass_without_vlm(self) -> None:
        nasa_slot = SlotRequirement(
            slot_id="P02-nasa",
            slide_number=2,
            deck_theme="Earth observation",
            slide_topic="Earth from orbit",
            purpose="Show a real NASA Earth observation photograph",
            subject="Earth from orbit",
            required_subject="Earth from orbit",
            required_asset_type="real satellite photograph",
            required_relationship="NASA Earth observation from orbit",
            forbidden_asset_types=["illustration", "render"],
            visual_type="real-evidence",
            queries=["NASA Earth from orbit photograph"],
        )
        item = SearchCandidate(
            candidate_id="nasa-earth",
            provider="nasa",
            query="NASA Earth from orbit photograph",
            title="Earth observed from orbit",
            description="NASA photograph of Earth captured during orbital observation.",
            image_url="https://images.nasa.gov/earth-orbit.png",
            source_page_url="https://www.nasa.gov/image-article/earth-from-orbit/",
            source_domain="nasa.gov",
            width=1400,
            height=900,
            credit="NASA",
            license_name="Public domain",
        )
        result, adapter = await self._pipeline_result(nasa_slot, item)
        self.assertEqual(result["status"], "selected")
        self.assertTrue(result["early_stop"])
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(result["selected_asset"]["verification_method"], "presentation-grade")
        self.assertFalse(result["verification"]["vlm_required"])

    async def test_neurips_adapter_builds_stable_document_provenance(self) -> None:
        index = (
            '<a title="paper title" href="/paper_files/paper/2017/hash/abc-Abstract.html">'
            'Attention Is All You Need</a>'
        )
        record = '<a href="/paper_files/paper/2017/file/abc-Paper.pdf">Paper</a>'

        def handler(request: httpx.Request) -> httpx.Response:
            body = record if "Abstract.html" in request.url.path else index
            return httpx.Response(200, text=body, headers={"content-type": "text/html"}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            from visual_assets.retrieval_cache import RetrievalCache
            adapter = NeurIPSPaperSearchAdapter(client, RetrievalCache(self.project / ".cache-neurips", 3600))
            policy = self.resolver.profiles["scientific-academic"]
            results = await adapter.search("Attention Is All You Need 2017 paper", 2, policy)
        self.assertGreaterEqual(len(results), 1)
        self.assertTrue(all(result.title == "Attention Is All You Need" for result in results))
        self.assertEqual(results[0].provenance["entity_document_id"], "neurips:2017:abc")
        self.assertEqual(results[0].provenance["asset_url"], results[0].image_url)

    async def test_neurips_adapter_accepts_formal_paper_title_without_word_paper(self) -> None:
        index = (
            '<a title="paper title" href="/paper_files/paper/2012/hash/alex-Abstract.html">'
            'ImageNet Classification with Deep Convolutional Neural Networks</a>'
        )
        record = '<a href="/paper_files/paper/2012/file/alex-Paper.pdf">Paper</a>'

        def handler(request: httpx.Request) -> httpx.Response:
            body = record if "Abstract.html" in request.url.path else index
            return httpx.Response(200, text=body, headers={"content-type": "text/html"}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            from visual_assets.retrieval_cache import RetrievalCache
            adapter = NeurIPSPaperSearchAdapter(client, RetrievalCache(self.project / ".cache-alexnet", 3600))
            policy = self.resolver.profiles["generic-real-world"]
            results = await adapter.search("ImageNet Classification with Deep Convolutional Neural Networks", 2, policy)
        self.assertGreaterEqual(len(results), 1)
        self.assertTrue(all(result.title == "ImageNet Classification with Deep Convolutional Neural Networks" for result in results))

    async def test_museum_photo_does_not_require_archive_grade_provenance(self) -> None:
        slot = SlotRequirement(
            slot_id="P03-museum",
            slide_number=3,
            deck_theme="Song dynasty art",
            slide_topic="Museum collection object",
            purpose="Show the exact museum collection object",
            subject="Travelers among Mountains and Streams",
            required_subject="Travelers among Mountains and Streams",
            required_asset_type="museum collection artwork object",
            required_relationship="museum collection record",
            authenticity_requirement="strict",
            visual_type="real-evidence",
            queries=["Travelers among Mountains and Streams museum collection"],
        )
        item = candidate(
            "met-museum",
            "Travelers among Mountains and Streams museum collection",
            "museum-object",
            "https://assets.test/museum.png",
        )
        result, _ = await self._pipeline_result(slot, item)
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["verification"]["chosen_path"], "presentation-source-context")
        self.assertFalse(result["verification"]["vlm_required"])

    async def test_apollo_history_photo_ignores_legacy_strict_fields_and_missing_archive_metadata(self) -> None:
        apollo_slot = SlotRequirement(
            slot_id="P04-apollo",
            slide_number=4,
            deck_theme="Apollo program history",
            slide_topic="Apollo 11 lunar mission",
            purpose="Show a historical Apollo 11 mission photograph",
            subject="Apollo 11",
            required_subject="Apollo 11",
            required_asset_type="historical photograph",
            required_relationship="Apollo 11 crew during the lunar mission",
            forbidden_asset_types=["illustration", "Apollo 12"],
            authenticity_requirement="strict",
            verification_mode="strict-provenance",
            verification_risk="evidence-critical",
            visual_type="real-evidence",
            queries=["Apollo 11 lunar mission photograph"],
        )
        item = SearchCandidate(
            candidate_id="apollo-context-photo",
            provider="independent-history-site",
            query="Apollo 11 lunar mission photograph",
            title="Apollo 11 crew during the lunar mission",
            description="Historical photograph illustrating the Apollo 11 lunar mission and crew.",
            image_url="https://images.history-example.test/apollo11.png",
            source_page_url="https://history-example.test/apollo-11-photo-essay",
            source_domain="history-example.test",
            width=1400,
            height=900,
            license_name="",
            published_at="",
            provenance={},
        )
        result, adapter = await self._pipeline_result(apollo_slot, item)
        self.assertEqual(result["status"], "selected")
        self.assertTrue(result["early_stop"])
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(result["verification"]["verification_risk"], "presentation-grade")
        self.assertEqual(result["verification"]["requested_risk"], "evidence-critical")
        self.assertFalse(result["verification"]["vlm_required"])
        self.assertFalse(result["verification"]["provenance_allowed"])
        self.assertEqual(result["selected_asset"]["license_status"], "unknown")
        self.assertEqual(result["selected_asset"]["display_attribution_mode"], "provenance-only")
        self.assertEqual(result["selected_asset"]["provenance"], {})

    async def test_nonofficial_but_relevant_source_can_pass(self) -> None:
        generic = SearchCandidate(
            candidate_id="generic",
            provider="static",
            query="robot",
            title="robot in laboratory",
            image_url="https://assets.test/robot.png",
            source_page_url="https://example.test/robot",
            source_domain="example.test",
            width=1400,
            height=900,
        )
        result, adapter = await self._pipeline_result(visual_slot(), generic)
        self.assertEqual(result["status"], "selected")
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(result["stats"]["downloads"], 1)

    def test_robot_action_scene_uses_single_presentation_grade_mode(self) -> None:
        slot = visual_slot()
        decision = resolve_verification_mode(slot, self.resolver.select(slot))
        self.assertEqual(decision.allowed_mode, "presentation")
        self.assertEqual(decision.risk, "presentation-grade")
        self.assertFalse(decision.vlm_required)
        self.assertTrue(decision.source_grounded_allowed)

    def test_real_scene_queries_are_bounded_and_slot_first(self) -> None:
        slot = visual_slot()
        slot.queries = []
        slot.entity_aliases = [
            "robotics lab human oversight",
            "autonomous robot field test",
            "human robot collaboration",
        ]
        queries = QueryBuilder().build(slot, self.resolver.select(slot), 2)
        self.assertEqual(len(queries), 2)
        self.assertIn("robot", queries[0].casefold())

    def test_paper_slot_does_not_expand_beyond_query_budget(self) -> None:
        slot = paper_slot()
        slot.required_subject = "Transformer paper"
        slot.entity_aliases = ["Attention Is All You Need"]
        slot.queries = []
        queries = QueryBuilder().build(slot, self.resolver.profiles["scientific-academic"], 2)
        self.assertLessEqual(len(queries), 2)
        self.assertFalse(any("archive" in query.casefold() for query in queries))

    async def test_metadata_heuristic_cannot_satisfy_visual_verification(self) -> None:
        slot = visual_slot()
        item = SearchCandidate(
            candidate_id="generic",
            provider="generic-web",
            query="robot",
            title="real robot action human supervision",
            description="authentic photograph of a robot performing a task",
            image_url="https://assets.test/robot.png",
            source_page_url="https://example.test/robot",
            source_domain="example.test",
            validation={"clarity_score": 100.0},
        )
        analysis = await DeterministicImageAnalyzer().analyze(
            Path("unused.png"), item, slot, self.resolver.select(slot)
        )
        self.assertEqual(analysis["hard_gate_verdict"]["provider"], "deterministic-metadata-pixel")
        self.assertNotEqual(analysis["hard_gate_verdict"]["provider"], "openai-compatible-vision")

    async def test_preflight_allows_factual_slot_without_host_vision(self) -> None:
        adapter = StaticAdapter([])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as client:
            pipeline = VisualAssetPipeline(self.project, client, config=config(), adapters=[adapter], policy_path=POLICY_PATH)
            pipeline.analyzer.vision.api_key = None
            pipeline.analyzer.vision.model = None
            report = await pipeline.preflight([visual_slot()], probe_network=False)
        self.assertTrue(report["stage7_ready"])
        self.assertEqual(report["blocked_slots"], [])
        self.assertEqual(adapter.calls, 0)

    async def test_preflight_never_creates_vlm_required_slots(self) -> None:
        adapter = StaticAdapter([])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as client:
            pipeline = VisualAssetPipeline(self.project, client, config=config(), adapters=[adapter], policy_path=POLICY_PATH)
            pipeline.analyzer.vision.api_key = None
            pipeline.analyzer.vision.model = None
            pipeline.host_native_vision = "unavailable"
            provenance_only = await pipeline.preflight([paper_slot()], probe_network=False)
            evidence_visual = visual_slot()
            evidence_visual.visual_type = "real-evidence"
            evidence_visual.require_visual_semantic_validation = True
            evidence_visual.required_relationship = "specific robot model identity in a historical event"
            includes_visual = await pipeline.preflight([paper_slot(), evidence_visual], probe_network=False)
        self.assertTrue(provenance_only["stage7_ready"])
        self.assertTrue(includes_visual["stage7_ready"])
        self.assertEqual(includes_visual["blocked_slots"], [])
        self.assertEqual(includes_visual["vlm_required_slots"], [])
        self.assertTrue((self.project / "validation" / "capability-preflight-P01-paper.json").is_file())
        self.assertTrue((self.project / "validation" / "capability-preflight.json").is_file())

    async def test_pdf_render_binds_selected_page_to_required_entity(self) -> None:
        import fitz
        document = fitz.open()
        first = document.new_page()
        first.insert_text((72, 72), "Generic expert systems overview")
        second = document.new_page()
        second.insert_text((72, 72), "MYCIN consultation interface historical documentation")
        pdf = document.tobytes()
        document.close()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"}, request=request)

        item = candidate(
            "internet-archive",
            "MYCIN historical archive",
            "archive-document",
            "https://assets.test/mycin.pdf",
        )
        item.mime_type = "application/pdf"
        item.document_asset = {"format": "pdf", "page_index": 0, "page_search_terms": ["mycin"]}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            manager = DownloadManager(
                client,
                RetrievalCache(self.project / ".cache-pdf", 3600),
                config(),
                DomainCircuitBreaker(2, 60),
            )
            outcome = await manager.download(item, self.project / "rendered")
        self.assertTrue(outcome.path.is_file())
        self.assertEqual(item.provenance["rendered_page_index"], 1)
        self.assertEqual(item.provenance["rendered_page_matched_terms"], ["mycin"])

    def test_archive_number_and_institutional_binding_do_not_control_acceptance(self) -> None:
        slot = SlotRequirement(
            slot_id="archive",
            slide_number=1,
            deck_theme="History",
            slide_topic="MYCIN",
            purpose="Historical evidence",
            subject="MYCIN",
            required_subject="MYCIN",
            required_asset_type="archival historical documentation",
            required_relationship="official archive record",
            authenticity_requirement="strict",
            visual_type="real-evidence",
        )
        item = candidate("internet-archive", "MYCIN archive document", "archive-document", "https://assets.test/mycin.pdf")
        decision = resolve_verification_mode(slot, self.resolver.profiles["historical-evidence"])
        self.assertEqual(decision.risk, "presentation-grade")
        self.assertEqual(decision.allowed_mode, "presentation")
        self.assertFalse(decision.provenance_allowed)

    async def test_host_vision_available_does_not_create_required_review(self) -> None:
        slot = visual_slot()
        item = SearchCandidate(
            candidate_id="host-review",
            provider="official-page",
            query="robot demonstration official",
            title="University robot demonstration",
            description="A robot performs a task under human supervision in a university laboratory.",
            image_url="https://assets.test/robot.png",
            source_page_url="https://robotics.example.edu/demonstration",
            source_domain="robotics.example.edu",
            width=1400,
            height=900,
            credit="University robotics laboratory",
        )
        adapter = StaticAdapter([item])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=IMAGE, headers={"content-type": "image/png"}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            pipeline = VisualAssetPipeline(
                self.project,
                client,
                config=config(),
                adapters=[adapter],
                policy_path=POLICY_PATH,
                host_native_vision="available",
            )
            result = await pipeline.run_slot(slot)
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["selected_asset"]["verification_method"], "presentation-grade")
        self.assertEqual(result["stats"]["visual_analyses"], 0)

    async def test_factual_illustrative_passes_source_grounded_without_host_vision(self) -> None:
        slot = visual_slot()
        item = SearchCandidate(
            candidate_id="source-grounded",
            provider="official-page",
            query="robot demonstration official",
            title="University robot demonstration with human oversight",
            description="A real robot performs a task in a laboratory while a human operator supervises it.",
            image_url="https://assets.test/robot.png",
            source_page_url="https://robotics.example.edu/demonstration",
            source_domain="robotics.example.edu",
            width=1400,
            height=900,
            credit="University robotics laboratory",
            source_tier=1,
        )
        verdict = SourceGroundedVerifier().verify(
            item, slot, self.resolver.select(slot), resolve_verification_mode(slot, self.resolver.select(slot))
        )
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["evidence_strength"], "PRESENTATION_GRADE")

    def test_metadata_caption_and_source_context_can_pass_without_same_domain(self) -> None:
        slot = visual_slot()
        item = SearchCandidate(
            candidate_id="hotlink",
            provider="bing-images",
            query="robot demonstration",
            title="Robot demonstration with human oversight",
            description="A robot performs a task while an operator watches.",
            image_url="https://wallpaper.example/robot.jpg",
            source_page_url="https://blog.example/robot-demonstration",
            source_domain="blog.example",
            width=1400,
            height=900,
            source_tier=2,
        )
        verdict = SourceGroundedVerifier().verify(
            item, slot, self.resolver.select(slot), resolve_verification_mode(slot, self.resolver.select(slot))
        )
        self.assertTrue(verdict["passed"])

    def test_structured_media_repository_can_bind_asset_to_record(self) -> None:
        slot = visual_slot()
        item = SearchCandidate(
            candidate_id="commons",
            provider="wikimedia-commons",
            query="human robot collaboration",
            title="Human-Robot Collaboration Sawing",
            description="A human and robot collaborate on a physical task.",
            image_url="https://upload.wikimedia.org/example/robot.jpg",
            source_page_url="https://commons.wikimedia.org/wiki/File:Human-Robot-Collaboration.jpg",
            source_domain="commons.wikimedia.org",
            width=1600,
            height=1000,
            source_tier=2,
        )
        verdict = SourceGroundedVerifier().verify(
            item, slot, self.resolver.select(slot), resolve_verification_mode(slot, self.resolver.select(slot))
        )
        self.assertTrue(verdict["passed"])

    async def test_historical_identity_does_not_fail_without_host_vision(self) -> None:
        slot = visual_slot()
        slot.visual_type = "real-evidence"
        slot.required_subject = "specific Atlas robot model"
        slot.required_relationship = "specific robot model identity in a historical event"
        slot.require_visual_semantic_validation = True
        adapter = StaticAdapter([])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as client:
            pipeline = VisualAssetPipeline(
                self.project,
                client,
                config=config(),
                adapters=[adapter],
                policy_path=POLICY_PATH,
                host_native_vision="unavailable",
            )
            report = await pipeline.preflight([slot], probe_network=False)
        self.assertTrue(report["stage7_ready"])
        self.assertEqual(report["blocked_slots"], [])
        self.assertEqual(report["vlm_required_slots"], [])

    def test_decorative_photo_also_uses_presentation_grade(self) -> None:
        slot = visual_slot()
        slot.visual_type = "decorative-background"
        slot.required_asset_type = "abstract technology background"
        slot.required_relationship = "visual atmosphere only"
        decision = resolve_verification_mode(slot, self.resolver.profiles["decorative-background"])
        self.assertEqual(decision.risk, "presentation-grade")
        self.assertFalse(decision.host_visual_required)

    async def test_no_vendor_keys_required_for_preflight(self) -> None:
        adapter = StaticAdapter([])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as client:
            pipeline = VisualAssetPipeline(
                self.project,
                client,
                config=config(),
                adapters=[adapter],
                policy_path=POLICY_PATH,
                host_native_vision="unavailable",
            )
            pipeline.analyzer.vision.api_key = None
            pipeline.analyzer.vision.model = None
            report = await pipeline.preflight([visual_slot()], probe_network=False)
        self.assertTrue(report["stage7_ready"])
        self.assertEqual(report["capabilities"]["external_vlm"], "optional")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

import httpx
from PIL import Image


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from visual_assets.asset_manifest import AssetManifest
from visual_assets.attribution_policy import apply_display_attribution
from visual_assets.candidate_filter import CandidateFilter
from visual_assets.config import RetrievalConfig
from visual_assets.download_manager import DomainCircuitOpen, DownloadManager, RateLimitError
from visual_assets.image_analyzer import DeterministicImageAnalyzer
from visual_assets.image_ranker import ImageRanker
from visual_assets.image_validator import ImageValidator
from visual_assets.models import SearchCandidate, SlotRequirement
from visual_assets.pipeline import VisualAssetPipeline
from visual_assets.retrieval_cache import RetrievalCache
from visual_assets.retrieval_state import DomainCircuitBreaker
from visual_assets.search_adapter import ImageSearchAdapter
from visual_assets.query_builder import QueryBuilder
from visual_assets.source_policy import SourcePolicy, SourcePolicyResolver
from svg_quality_checker import SVGQualityChecker


POLICY_PATH = Path(__file__).resolve().parents[1] / "references" / "source-policy-profiles.yaml"


def image_bytes(size: tuple[int, int] = (1600, 900), offset: int = 0) -> bytes:
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            pixels[x, y] = (
                (x * 7 + y * 3 + offset) % 256,
                (x * 2 + y * 11 + offset) % 256,
                (x * 13 + y * 5 + offset) % 256,
            )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


HIGH_IMAGE = image_bytes()
ALT_IMAGE = image_bytes(offset=37)
LOW_IMAGE = image_bytes((320, 180))


def candidate(
    candidate_id: str,
    url: str,
    *,
    title: str = "NIAID macrophage microscopy official micrograph",
    domain: str = "niaid.nih.gov",
) -> SearchCandidate:
    item = SearchCandidate(
        candidate_id=candidate_id,
        provider="fake",
        query="macrophage NIAID microscopy",
        title=title,
        image_url=url,
        source_page_url=f"https://{domain}/source/{candidate_id}",
        source_domain=domain,
        description="Official photograph and authentic microscopy image",
        author="NIAID",
        credit="National Institute of Allergy and Infectious Diseases",
        license_name="Public domain",
        width=1600,
        height=900,
    )
    item.provenance["direct_asset_relation"] = True
    return item


def slot(**overrides: object) -> SlotRequirement:
    values: dict[str, object] = {
        "slot_id": "P01-main",
        "slide_number": 1,
        "deck_theme": "Biomedical science and immune cells",
        "slide_topic": "Macrophage microscopy",
        "purpose": "Show a real macrophage micrograph from an authoritative source",
        "subject": "macrophage",
        "required_subject": "macrophage",
        "required_asset_type": "microscopy micrograph",
        "required_relationship": "real macrophage microscopy image from an authoritative source",
        "forbidden_asset_types": ["illustration", "person portrait"],
        "authenticity_requirement": "standard",
        "require_visual_semantic_validation": False,
        "visual_type": "real-evidence",
        "required_terms": ["macrophage", "microscopy"],
        "min_width": 1200,
        "min_height": 675,
    }
    values.update(overrides)
    return SlotRequirement(**values)


class StaticAdapter(ImageSearchAdapter):
    name = "fake"

    def __init__(self, results: list[SearchCandidate], delay: float = 0):
        self.results = results
        self.delay = delay
        self.calls = 0

    async def search(self, query: str, limit: int, policy: SourcePolicy) -> list[SearchCandidate]:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.results[:limit]


class UnavailableGeneralAdapter(StaticAdapter):
    name = "unavailable-general"
    capability_kind = "general-web"

    @property
    def available(self) -> bool:
        return False

    @property
    def unavailable_reason(self) -> str | None:
        return "API key is not configured"


class VisualAssetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def resolver(self) -> SourcePolicyResolver:
        return SourcePolicyResolver(POLICY_PATH)

    def config(self, **overrides: object) -> RetrievalConfig:
        values = {
            "retry_limit": 1,
            "operation_timeout_seconds": 0.4,
            "slot_deadline_seconds": 3.0,
            "no_progress_seconds": 0.5,
            "download_budget": 2,
            "good_enough_threshold": 70,
            "domain_failure_threshold": 2,
        }
        values.update(overrides)
        return RetrievalConfig.from_env(values)

    def test_presentation_grade_default_slot_budgets(self) -> None:
        config = RetrievalConfig()
        self.assertEqual(config.query_budget, 2)
        self.assertEqual(config.candidate_budget, 5)
        self.assertEqual(config.metadata_shortlist, 5)
        self.assertEqual(config.download_budget, 2)
        self.assertEqual(config.host_visual_review_budget, 1)
        self.assertEqual(config.visual_analysis_budget, 1)
        self.assertIn(config.retry_limit, {0, 1})
        self.assertEqual(config.slot_deadline_seconds, 45.0)

    def test_presentation_grade_slot_budget_hard_caps_cannot_be_raised(self) -> None:
        for name, value in {
            "query_budget": 3,
            "candidate_budget": 6,
            "metadata_shortlist": 6,
            "download_budget": 3,
            "visual_analysis_budget": 2,
            "host_visual_review_budget": 2,
            "retry_limit": 2,
            "slot_deadline_seconds": 46.0,
        }.items():
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "hard cap"):
                RetrievalConfig.from_env({name: value})

    def test_unknown_license_is_recorded_without_rights_investigation(self) -> None:
        item = {
            "source_type": "web",
            "source_domain": "credible.example",
            "license_name": "",
            "license_url": "",
        }
        decision = apply_display_attribution(item)
        self.assertEqual(item["license_status"], "unknown")
        self.assertEqual(decision.mode, "provenance-only")
        self.assertEqual(decision.license_obligation, "unknown-license")

    def test_explicit_reuse_prohibition_is_rejected(self) -> None:
        prohibited = candidate("prohibited", "https://credible.example/photo.jpg")
        prohibited.license_name = "Reproduction prohibited"
        accepted = CandidateFilter(self.resolver()).filter_and_rank_metadata(
            [prohibited], slot(), self.resolver().profiles["scientific-academic"]
        )
        self.assertEqual(accepted, [])
        self.assertEqual(prohibited.rejection_reason, "source explicitly prohibits reuse")

    async def test_normal_download_retry_and_cache_hit(self) -> None:
        calls: dict[str, int] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            key = str(request.url)
            calls[key] = calls.get(key, 0) + 1
            if request.url.host == "flaky.test" and calls[key] == 1:
                return httpx.Response(500, request=request)
            return httpx.Response(200, content=HIGH_IMAGE, headers={"content-type": "image/jpeg"}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            cache = RetrievalCache(self.project / ".cache", 3600)
            manager = DownloadManager(client, cache, self.config(), DomainCircuitBreaker(3, 60))
            item = candidate("flaky", "https://flaky.test/image.jpg")
            first = await manager.download(item, self.project / "downloads-a")
            second = await manager.download(item, self.project / "downloads-b")
        self.assertEqual(first.retries, 1)
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(calls[item.image_url], 2)

    def test_cache_indexes_exact_content_hashes(self) -> None:
        cache = RetrievalCache(self.project / ".cache", 3600)
        source = self.project / "image.jpg"
        source.write_bytes(HIGH_IMAGE)
        cache.put_download(
            "https://one.test/image.jpg",
            source,
            {
                "content_type": "image/jpeg",
                "bytes_downloaded": len(HIGH_IMAGE),
                "content_sha256": "abc123",
            },
        )
        self.assertEqual(cache.content_hashes(), {"abc123"})

    async def test_429_opens_domain_circuit(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            breaker = DomainCircuitBreaker(1, 60)
            manager = DownloadManager(client, RetrievalCache(self.project / ".cache", 3600), self.config(), breaker)
            item = candidate("limited", "https://limited.test/image.jpg")
            with self.assertRaises(RateLimitError):
                await manager.download(item, self.project / "downloads")
            with self.assertRaises(DomainCircuitOpen):
                await manager.download(item, self.project / "downloads")

    async def test_bounded_concurrency(self) -> None:
        active = 0
        maximum = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.05)
            active -= 1
            return httpx.Response(200, content=HIGH_IMAGE, headers={"content-type": "image/jpeg"}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            config = self.config(download_concurrency=2)
            manager = DownloadManager(client, RetrievalCache(self.project / ".cache", 3600), config, DomainCircuitBreaker(5, 60))
            await asyncio.gather(
                *(manager.download(candidate(str(i), f"https://host{i}.test/image.jpg"), self.project / "downloads") for i in range(5))
            )
        self.assertLessEqual(maximum, 2)
        self.assertEqual(maximum, 2)

    def test_validator_rejects_html_low_resolution_and_duplicate(self) -> None:
        validator = ImageValidator()
        html_path = self.project / "fake.jpg"
        html_path.write_bytes(b"<!doctype html><html>not an image</html>")
        self.assertIn("HTML", validator.validate(html_path, "image/jpeg", slot()).reason)
        low_path = self.project / "low.jpg"
        low_path.write_bytes(LOW_IMAGE)
        self.assertIn("resolution too low", validator.validate(low_path, "image/jpeg", slot()).reason)
        high_path = self.project / "high.jpg"
        high_path.write_bytes(HIGH_IMAGE)
        result = validator.validate(high_path, "image/jpeg", slot())
        self.assertTrue(result.valid)
        self.assertTrue(validator.is_near_duplicate(result.details["perceptual_hash"], [result.details["perceptual_hash"]], 5))

    def test_candidate_filter_removes_duplicate_url_and_excluded_domain(self) -> None:
        resolver = self.resolver()
        policy = resolver.profiles["scientific-academic"]
        candidates = [
            candidate("a", "https://a.test/one.jpg"),
            candidate("b", "https://a.test/one.jpg"),
            candidate("c", "https://c.test/two.jpg", domain="pinterest.com"),
        ]
        filtered = CandidateFilter(resolver).filter_and_rank_metadata(candidates, slot(), policy)
        self.assertEqual([item.candidate_id for item in filtered], ["a"])

    def test_candidate_filter_rejects_known_low_resolution_before_download(self) -> None:
        item = candidate("low-meta", "https://example.org/low.jpg")
        item.width = 1920
        item.height = 58
        filtered = CandidateFilter(self.resolver()).filter_and_rank_metadata(
            [item], slot(min_width=1200, min_height=500), self.resolver().profiles["scientific-academic"]
        )
        self.assertEqual(filtered, [])

    def test_real_scene_rejects_real_madrid_false_positive(self) -> None:
        football = candidate(
            "football",
            "https://example.org/real-madrid.jpg",
            title="Real Madrid football club wallpaper",
            domain="example.org",
        )
        football.description = "Real Madrid team logo"
        scene = slot(
            visual_type="real-scene",
            subject="AI-enabled robot or autonomous system",
            required_subject="real AI-enabled robot or autonomous system",
            required_asset_type="authentic real-world photograph",
            required_relationship="robot performing a task with human oversight",
        )
        filtered = CandidateFilter(self.resolver()).filter_and_rank_metadata(
            [football], scene, self.resolver().profiles["company-product-technology"]
        )
        self.assertEqual(filtered, [])
        self.assertEqual(football.rejection_reason, "required scene subject absent from candidate metadata")

    def test_institutional_candidate_is_prioritized_for_museum_relationship(self) -> None:
        resolver = self.resolver()
        required = slot(
            subject="Song dynasty painting",
            required_subject="Song dynasty painting",
            required_asset_type="painting artwork museum object",
            required_relationship="museum collection record",
            visual_type="artwork-museum-object",
            source_policy="artwork-museum-object",
        )
        repository = candidate(
            "repository",
            "https://upload.wikimedia.org/repository.jpg",
            title="Song dynasty painting museum collection",
            domain="commons.wikimedia.org",
        )
        repository.provider = "wikimedia-commons"
        institutional = candidate(
            "institutional",
            "https://images.metmuseum.org/institutional.jpg",
            title="Song dynasty painting",
            domain="metmuseum.org",
        )
        institutional.provider = "met-museum"
        institutional.credit = "The Metropolitan Museum of Art collection"
        ranked = CandidateFilter(resolver).filter_and_rank_metadata(
            [repository, institutional], required, resolver.profiles["artwork-museum-object"]
        )
        self.assertEqual(ranked[0].candidate_id, "institutional")
        self.assertEqual(ranked[0].source_tier, 1)

    def test_theme_and_slot_select_different_profiles(self) -> None:
        resolver = self.resolver()
        cases = [
            (slot(), "scientific-academic"),
            (slot(deck_theme="Chinese literature and cultural memory", slide_topic="Tang poetry", purpose="explain cultural context", subject="Tang dynasty poetry manuscript", visual_type="humanities-culture"), "humanities-culture"),
            (slot(deck_theme="Apollo 11 history", slide_topic="Moon landing archive", purpose="historical evidence", subject="Apollo 11", visual_type="historical-evidence"), "historical-evidence"),
            (slot(deck_theme="Biography of Ada Lovelace", slide_topic="Portrait and life", purpose="authentic portrait", subject="Ada Lovelace", visual_type="public-figure-biography"), "public-figure-biography"),
            (slot(deck_theme="Spatial computing products", slide_topic="Apple Vision Pro", purpose="official product photograph", subject="Apple Vision Pro", visual_type="company-product-technology"), "company-product-technology"),
            (slot(deck_theme="Paris travel", slide_topic="Eiffel Tower", purpose="real landmark photo", subject="Eiffel Tower", visual_type="geography-travel-landmark"), "geography-travel-landmark"),
            (slot(deck_theme="Renaissance art", slide_topic="Mona Lisa", purpose="museum object record", subject="Mona Lisa", visual_type="artwork-museum-object"), "artwork-museum-object"),
            (slot(deck_theme="Latest election news", slide_topic="Current election event", purpose="current news evidence", subject="election press conference", visual_type="news-current-events"), "news-current-events"),
            (slot(deck_theme="University life", slide_topic="Student collaboration", purpose="real campus scene", subject="students collaborating", visual_type="generic-real-world"), "generic-real-world"),
            (slot(deck_theme="Minimal design", slide_topic="Opening mood", purpose="decorative abstract background", subject="blue gradient texture", visual_type="decorative-background"), "decorative-background"),
        ]
        self.assertEqual([resolver.select(value).name for value, _ in cases], [expected for _, expected in cases])

    def test_unknown_company_official_product_page_is_tier_two(self) -> None:
        resolver = self.resolver()
        requirement = slot(
            deck_theme="AI robotics",
            slide_topic="Robots in research",
            purpose="Show a robot in operation",
            subject="robot",
            visual_type="company-product-technology",
            source_policy="company-product-technology",
        )
        policy = resolver.select(requirement)
        item = candidate(
            "boston",
            "https://bostondynamics.com/media/spot.jpg",
            title="Robots for Research & Development | Boston Dynamics",
            domain="bostondynamics.com",
        )
        item.source_page_url = "https://bostondynamics.com/solutions/rd/"
        self.assertEqual(resolver.classify_source(item, policy), 2)

    def test_blog_slug_remains_lower_ranked_than_official_product_page(self) -> None:
        resolver = self.resolver()
        policy = resolver.profiles["company-product-technology"]
        item = candidate(
            "blog",
            "https://wallpaper.example/robot.jpg",
            title="Robotics The Future is Here – Track2Training",
            domain="track2training.com",
        )
        item.source_page_url = "https://track2training.com/2022/04/08/robotics-the-future-is-here/"
        self.assertEqual(resolver.classify_source(item, policy), 4)

    def test_query_builder_is_slot_first_and_theme_is_not_mechanically_appended(self) -> None:
        value = slot(
            deck_theme="A broad deck theme that should not appear in every query",
            entity_aliases=["巨噬细胞"],
            required_asset_type="microscopy micrograph",
            required_relationship="official laboratory image",
        )
        queries = QueryBuilder().build(value, self.resolver().profiles["scientific-academic"], 4)
        self.assertTrue(any("巨噬细胞" in query for query in queries))
        self.assertTrue(all(value.deck_theme not in query for query in queries))
        painting = slot(
            required_subject="Song dynasty painting",
            required_asset_type="painting artwork museum object",
            required_relationship="museum collection record",
            visual_type="artwork-museum-object",
        )
        painting_queries = QueryBuilder().build(
            painting, self.resolver().profiles["artwork-museum-object"], 1
        )
        self.assertNotIn("painting painting", painting_queries[0].casefold())

    async def test_artwork_slot_rejects_person_portrait(self) -> None:
        portrait = candidate(
            "portrait",
            "https://museum.test/portrait.jpg",
            title="Portrait of Song Taizu emperor depicted person",
            domain="metmuseum.org",
        )
        portrait.description = "Museum collection portrait depicting the first emperor of Song"
        artwork_slot = slot(
            subject="Song dynasty painting",
            required_subject="Song dynasty painting",
            required_asset_type="painting artwork museum object",
            required_relationship="museum collection record for a Song dynasty painting",
            forbidden_asset_types=["person portrait"],
            visual_type="artwork-museum-object",
            source_policy="artwork-museum-object",
        )
        async with httpx.AsyncClient() as client:
            analysis = await DeterministicImageAnalyzer().analyze(
                self.project / "unused.jpg", portrait, artwork_slot, self.resolver().profiles["artwork-museum-object"]
            )
        self.assertFalse(analysis["hard_gate_verdict"]["passed"])
        self.assertFalse(analysis["hard_gate_verdict"]["gates"]["forbidden_asset_types"]["passed"])

    async def test_museum_collection_rejects_merely_historically_related_image(self) -> None:
        related = candidate(
            "related",
            "https://museum.test/related.jpg",
            title="Song emperor historical portrait",
            domain="metmuseum.org",
        )
        related.description = "Historically related portrait, not a collection painting"
        required = slot(
            subject="Song dynasty painting",
            required_subject="Song dynasty painting",
            required_asset_type="painting artwork museum object",
            required_relationship="held and digitized by a museum with a collection record",
            forbidden_asset_types=["person portrait"],
            visual_type="artwork-museum-object",
            source_policy="artwork-museum-object",
        )
        async with httpx.AsyncClient() as client:
            analysis = await DeterministicImageAnalyzer().analyze(
                self.project / "unused.jpg", related, required, self.resolver().profiles["artwork-museum-object"]
            )
        self.assertFalse(analysis["hard_gate_verdict"]["passed"])

    async def test_repository_caption_can_support_presentation_grade_museum_image(self) -> None:
        repository_item = candidate(
            "repository-museum-claim",
            "https://upload.wikimedia.org/example.jpg",
            title="Song dynasty painting from Honolulu Museum of Art",
            domain="commons.wikimedia.org",
        )
        repository_item.provider = "wikimedia-commons"
        repository_item.description = "Song dynasty painting, Honolulu Museum of Art accession 4162"
        repository_item.source_tier = 2
        required = slot(
            subject="Song dynasty painting",
            required_subject="Song dynasty painting",
            required_asset_type="painting artwork museum object",
            required_relationship="held, published, or digitized by a museum with a traceable collection record",
            forbidden_asset_types=["person portrait"],
            visual_type="artwork-museum-object",
            source_policy="artwork-museum-object",
        )
        analysis = await DeterministicImageAnalyzer().analyze(
            self.project / "unused.jpg",
            repository_item,
            required,
            self.resolver().profiles["artwork-museum-object"],
        )
        verdict = analysis["hard_gate_verdict"]
        self.assertTrue(verdict["passed"])
        self.assertTrue(verdict["gates"]["relationship"]["passed"])

    async def test_original_painting_slot_rejects_modern_stone_reproduction(self) -> None:
        reproduction = candidate(
            "stone-copy",
            "https://commons.wikimedia.org/stone-copy.jpg",
            title="2008 Zhang Zeduan Painting Carved in Stone",
            domain="commons.wikimedia.org",
        )
        reproduction.description = "Modern stone carving based on Along the River During the Qingming Festival"
        reproduction.published_at = "2008"
        required = slot(
            subject="Along the River During the Qingming Festival Zhang Zeduan",
            required_subject="Along the River During the Qingming Festival Zhang Zeduan",
            entity_aliases=["Qingming scroll Zhang Zeduan"],
            required_asset_type="ancient Chinese painting handscroll artwork",
            required_relationship="original Northern Song artwork or museum digitization",
            forbidden_asset_types=["modern reproduction", "stone carving"],
            visual_type="real-evidence artwork museum object",
            source_policy="artwork-museum-object",
        )
        reproduction.source_tier = 2
        async with httpx.AsyncClient() as client:
            analysis = await DeterministicImageAnalyzer().analyze(
                self.project / "unused.jpg", reproduction, required, self.resolver().profiles["artwork-museum-object"]
            )
        self.assertFalse(analysis["hard_gate_verdict"]["passed"])
        self.assertFalse(analysis["hard_gate_verdict"]["gates"]["asset_type"]["passed"])
        self.assertFalse(analysis["hard_gate_verdict"]["gates"]["forbidden_asset_types"]["passed"])

    async def test_painting_slot_rejects_calligraphy_treatise(self) -> None:
        treatise = candidate(
            "treatise",
            "https://images.metmuseum.org/treatise.jpg",
            title="Treatise on Painting",
            domain="metmuseum.org",
        )
        treatise.provider = "met-museum"
        treatise.description = "Song dynasty handscroll treatise with calligraphy and written text"
        treatise.credit = "The Metropolitan Museum of Art collection"
        treatise.source_tier = 1
        required = slot(
            subject="Song dynasty painting",
            required_subject="Song dynasty painting",
            required_asset_type="painting artwork museum object",
            required_relationship="museum collection record",
            forbidden_asset_types=["person portrait"],
            visual_type="artwork-museum-object",
            source_policy="artwork-museum-object",
        )
        analysis = await DeterministicImageAnalyzer().analyze(
            self.project / "unused.jpg", treatise, required, self.resolver().profiles["artwork-museum-object"]
        )
        self.assertFalse(analysis["hard_gate_verdict"]["passed"])
        self.assertFalse(analysis["hard_gate_verdict"]["gates"]["asset_type"]["passed"])

    def test_soft_score_cannot_override_hard_semantic_failure(self) -> None:
        item = candidate("wrong", "https://metmuseum.org/wrong.jpg", domain="metmuseum.org")
        item.validation = {"valid": True, "width": 4000, "height": 3000, "clarity_score": 100}
        item.analysis = {
            "semantic_match": 100,
            "authenticity": 100,
            "clarity": 100,
            "composition": 100,
            "hard_gate_verdict": {"passed": False, "reasons": ["asset_type"]},
        }
        item.source_tier = 1
        with self.assertRaises(ValueError):
            ImageRanker().score(item, slot(), self.resolver().profiles["scientific-academic"])

    async def test_early_stop_cannot_trigger_before_hard_gates_pass(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=HIGH_IMAGE, headers={"content-type": "image/jpeg"}, request=request)

        wrong = candidate(
            "wrong-type",
            "https://metmuseum.org/wrong.jpg",
            title="Portrait of Song Taizu emperor depicted person museum collection",
            domain="metmuseum.org",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            pipeline = VisualAssetPipeline(
                self.project,
                client,
                config=self.config(download_budget=1),
                adapters=[StaticAdapter([wrong])],
                policy_path=POLICY_PATH,
            )
            result = await pipeline.run_slot(slot(
                subject="Song dynasty painting",
                required_subject="Song dynasty painting",
                required_asset_type="painting artwork museum object",
                required_relationship="museum collection record",
                forbidden_asset_types=["person portrait"],
                visual_type="artwork-museum-object",
                source_policy="artwork-museum-object",
                queries=["Song dynasty painting museum collection"],
            ))
        self.assertEqual(result["status"], "capability-degraded")
        self.assertFalse(result["early_stop"])

    async def test_policy_reports_degraded_only_when_all_search_is_unavailable(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as client:
            pipeline = VisualAssetPipeline(
                self.project,
                client,
                config=self.config(),
                adapters=[UnavailableGeneralAdapter([])],
                policy_path=POLICY_PATH,
            )
            result = await pipeline.run_slot(slot(queries=["macrophage official source"]))
        self.assertEqual(result["status"], "capability-degraded")
        self.assertFalse(result["search_backends"]["general_search_available"])

    async def test_all_downloaded_files_corrupt_is_capability_degraded(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"not-an-image",
                headers={"content-type": "image/jpeg"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            pipeline = VisualAssetPipeline(
                self.project,
                client,
                config=self.config(download_budget=1),
                adapters=[StaticAdapter([candidate("corrupt-only", "https://corrupt.test/image.jpg")])],
                policy_path=POLICY_PATH,
            )
            result = await pipeline.run_slot(slot(queries=["macrophage microscopy"]))
        self.assertEqual(result["status"], "capability-degraded")
        self.assertEqual(result["stats"]["downloads"], 1)
        self.assertTrue(any(
            item["status"] in {"download-failed", "validation-failed"}
            for item in result["candidate_audit"]
        ))

    def test_unresolved_required_slot_makes_stage7_not_ready(self) -> None:
        requirements = self.project / "research" / "visual-assets" / "visual-requirements.json"
        requirements.parent.mkdir(parents=True, exist_ok=True)
        requirements.write_text(json.dumps({"slots": [{"slot_id": "required-slot", "required": True}]}), encoding="utf-8")
        manifest = AssetManifest(self.project)
        manifest.payload["slots"] = {"required-slot": {"status": "unresolved"}}
        manifest._save()
        report = AssetManifest(self.project).validation_report()
        self.assertTrue(report["schema_valid"])
        self.assertTrue(report["files_valid"])
        self.assertFalse(report["assets_complete"])
        self.assertFalse(report["stage7_ready"])

    async def test_cache_miss_metrics_are_recorded(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=HIGH_IMAGE, headers={"content-type": "image/jpeg"}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            pipeline = VisualAssetPipeline(
                self.project,
                client,
                config=self.config(),
                adapters=[StaticAdapter([candidate("fresh", "https://fresh.test/image.jpg")])],
                policy_path=POLICY_PATH,
            )
            result = await pipeline.run_slot(slot(queries=["macrophage microscopy"]))
        self.assertEqual(result["stats"]["download_cache_misses"], 1)
        self.assertEqual(result["stats"]["download_cache_hits"], 0)

    async def test_pipeline_failure_isolation_and_good_enough_early_stop(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "bad.test":
                return httpx.Response(200, content=b"<html>blocked</html>", headers={"content-type": "text/html"}, request=request)
            if request.url.host == "unused.test":
                return httpx.Response(200, content=ALT_IMAGE, headers={"content-type": "image/jpeg"}, request=request)
            return httpx.Response(200, content=HIGH_IMAGE, headers={"content-type": "image/jpeg"}, request=request)

        results = [
            candidate("bad", "https://bad.test/image.jpg"),
            candidate("good", "https://good.test/image.jpg"),
            candidate("unused", "https://unused.test/image.jpg"),
        ]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            pipeline = VisualAssetPipeline(
                self.project,
                client,
                config=self.config(),
                adapters=[StaticAdapter(results)],
                policy_path=POLICY_PATH,
            )
            result = await pipeline.run_slot(slot(queries=["macrophage NIAID microscopy"]))
        self.assertEqual(result["status"], "selected")
        self.assertTrue(result["early_stop"])
        self.assertEqual(result["stats"]["downloads"], 2)
        self.assertEqual(result["stats"]["network_downloads"], 2)
        self.assertEqual(result["selected_asset"]["title"], results[1].title)
        self.assertEqual(AssetManifest(self.project).validate(), [])

    async def test_domain_failure_switches_to_next_candidate_domain(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "limited.test":
                return httpx.Response(429, request=request)
            return httpx.Response(200, content=HIGH_IMAGE, headers={"content-type": "image/jpeg"}, request=request)

        results = [
            candidate("limited", "https://limited.test/image.jpg"),
            candidate("backup", "https://backup.test/image.jpg"),
        ]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            pipeline = VisualAssetPipeline(
                self.project,
                client,
                config=self.config(domain_failure_threshold=1),
                adapters=[StaticAdapter(results)],
                policy_path=POLICY_PATH,
            )
            result = await pipeline.run_slot(slot(queries=["macrophage NIAID microscopy"]))
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["selected_asset"]["original_image_url"], "https://backup.test/image.jpg")
        self.assertTrue(result["stats"]["domain_circuits"]["limited.test"]["circuit_open"])

    async def test_no_progress_watchdog_exits_slow_search(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request))) as client:
            pipeline = VisualAssetPipeline(
                self.project,
                client,
                config=self.config(operation_timeout_seconds=0.05, no_progress_seconds=0.08, slot_deadline_seconds=0.4),
                adapters=[StaticAdapter([], delay=0.3)],
                policy_path=POLICY_PATH,
            )
            started = time.monotonic()
            result = await pipeline.run_slot(slot(queries=["slow search"]))
            elapsed = time.monotonic() - started
        self.assertEqual(result["status"], "capability-degraded")
        self.assertLess(elapsed, 0.5)

    async def test_total_score_threshold_cannot_block_presentation_grade_early_stop(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=HIGH_IMAGE, headers={"content-type": "image/jpeg"}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            pipeline = VisualAssetPipeline(
                self.project,
                client,
                config=self.config(good_enough_threshold=99.9, download_budget=1),
                adapters=[StaticAdapter([candidate("best", "https://best.test/image.jpg")])],
                policy_path=POLICY_PATH,
            )
            result = await pipeline.run_slot(slot(queries=["macrophage"]))
        self.assertEqual(result["status"], "selected")
        self.assertTrue(result["early_stop"])
        self.assertIsNotNone(result["stats"]["best_so_far"])

    async def test_slot_deadline_selects_usable_best_so_far(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=HIGH_IMAGE, headers={"content-type": "image/jpeg"}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            pipeline = VisualAssetPipeline(
                self.project,
                client,
                config=self.config(
                    query_budget=2,
                    download_budget=2,
                    operation_timeout_seconds=0.05,
                    slot_deadline_seconds=0.08,
                    no_progress_seconds=1.0,
                ),
                adapters=[StaticAdapter([candidate("deadline-best", "https://best.test/image.jpg")])],
                policy_path=POLICY_PATH,
            )
            original_verify = pipeline.source_grounded_verifier.verify

            def delayed_verify(*args: object, **kwargs: object) -> dict[str, object]:
                evidence = original_verify(*args, **kwargs)
                time.sleep(0.1)
                return evidence

            pipeline.source_grounded_verifier.verify = delayed_verify
            pipeline.ranker.is_good_enough = lambda *args, **kwargs: (False, ["continue to budget"])
            result = await pipeline.run_slot(slot(queries=["macrophage one", "macrophage two"]))
        self.assertEqual(result["status"], "selected")
        self.assertFalse(result["early_stop"])
        self.assertIn("slot budget ended", result["selected_asset"]["selection_reason"])
        self.assertEqual(result["selected_asset"]["verification_status"], "presentation-verified")

    def test_manifest_is_unique_machine_truth_and_windows_paths_resolve(self) -> None:
        manifest = AssetManifest(self.project)
        destination = self.project / "ppt-content" / "visuals" / "assets" / "P01" / "image.jpg"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(HIGH_IMAGE)
        manifest.payload["items"] = [
            {
                "asset_id": "asset-P01-main",
                "slot_id": "P01-main",
                "slide_number": 1,
                "local_path": "ppt-content/visuals/assets/P01/image.jpg",
                "source_type": "web",
                "source_page_url": "https://example.org/page",
                "original_image_url": "https://example.org/image.jpg",
                "source_domain": "example.org",
                "search_query": "example",
                "verification_risk": "factual-illustrative",
                "verification_method": "source-grounded",
                "verification_evidence": {"source_page": "https://example.org/page"},
                "confidence": 0.8,
                "verification_timestamp": "2026-08-14T00:00:00+00:00",
                "provenance": {},
            }
        ]
        manifest._save()
        loaded = AssetManifest(self.project)
        self.assertEqual(loaded.validate(), [])
        self.assertEqual(loaded.payload["items"][0]["local_path"], "ppt-content/visuals/assets/P01/image.jpg")

    def test_svg_checker_consumes_asset_manifest_json(self) -> None:
        asset = self.project / "ppt-content" / "visuals" / "assets" / "P01" / "image.jpg"
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(HIGH_IMAGE)
        manifest_path = self.project / "ppt-content" / "visuals" / "asset-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 4,
                    "items": [
                        {
                            "asset_id": "asset-P01-main",
                            "slot_id": "P01-main",
                            "slide_number": 1,
                            "local_path": "ppt-content/visuals/assets/P01/image.jpg",
                            "filename": "image.jpg",
                            "source_type": "web",
                            "source_page_url": "https://example.org/page",
                            "original_image_url": "https://example.org/image.jpg",
                            "source_domain": "example.org",
                            "search_query": "example",
                            "verification_status": "presentation-verified",
                            "verification_risk": "presentation-grade",
                            "verification_method": "presentation-grade",
                            "verification_evidence": {"source_page": "https://example.org/page"},
                            "evidence_strength": "PRESENTATION_GRADE",
                            "confidence": 0.8,
                            "verification_timestamp": "2026-08-14T00:00:00+00:00",
                            "license_status": "known",
                            "provenance": {},
                            "display_attribution_mode": "full-credit",
                            "display_attribution": "Example Photographer · CC BY 4.0",
                            "author": "Example Photographer",
                            "license_name": "CC BY 4.0",
                        }
                    ],
                    "slots": {
                        "P01-main": {
                            "selected_asset_id": "asset-P01-main",
                            "verification_status": "presentation-verified",
                            "verification_risk": "presentation-grade",
                            "verification_method": "presentation-grade",
                            "evidence_strength": "PRESENTATION_GRADE",
                            "display_attribution_mode": "full-credit",
                            "display_attribution": "Example Photographer · CC BY 4.0",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        svg_path = self.project / "svg_output" / "P01.svg"
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        checker = SVGQualityChecker()
        result = {"errors": [], "warnings": [], "info": {}}
        checker._check_sourced_image_attribution(
            '<svg><image href="image.jpg"/><text>Example Photographer · CC BY 4.0</text></svg>',
            svg_path,
            result,
        )
        self.assertEqual(result["errors"], [])
        missing = {"errors": [], "warnings": [], "info": {}}
        checker._check_sourced_image_attribution(
            '<svg><image href="image.jpg"/></svg>',
            svg_path,
            missing,
        )
        self.assertTrue(any("Missing inline attribution" in error for error in missing["errors"]))


if __name__ == "__main__":
    unittest.main()

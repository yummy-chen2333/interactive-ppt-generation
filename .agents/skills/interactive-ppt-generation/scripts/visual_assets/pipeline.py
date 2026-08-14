from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from .asset_manifest import AssetManifest
from .capability_preflight import CapabilityPreflight
from .candidate_filter import CandidateFilter
from .config import RetrievalConfig
from .download_manager import DownloadError, DownloadManager
from .image_analyzer import ImageAnalyzer
from .image_ranker import ImageRanker
from .image_validator import ImageValidator
from .models import SearchCandidate, SlotRequirement
from .query_builder import QueryBuilder
from .retrieval_cache import RetrievalCache
from .retrieval_state import DomainCircuitBreaker, RetrievalState
from .search_adapter import ImageSearchAdapter, build_default_adapters
from .source_policy import SourcePolicyResolver
from .watchdog import NoProgressError, PipelineWatchdog, SlotDeadlineExceeded
from .verification import (
    EVIDENCE_STRENGTH,
    SourceGroundedVerifier,
    VerificationDecision,
    resolve_verification_mode,
)


ProgressCallback = Callable[[dict[str, Any]], None]


class VisualAssetPipeline:
    def __init__(
        self,
        project_path: Path,
        client: httpx.AsyncClient,
        config: RetrievalConfig | None = None,
        provider_names: list[str] | None = None,
        policy_path: Path | None = None,
        progress_callback: ProgressCallback | None = None,
        adapters: list[ImageSearchAdapter] | None = None,
        host_native_vision: str = "unknown",
    ):
        self.project_path = project_path.resolve()
        self.client = client
        self.config = config or RetrievalConfig.from_env()
        self.policy_resolver = SourcePolicyResolver(
            policy_path
            or Path(__file__).resolve().parents[2] / "references" / "source-policy-profiles.yaml"
        )
        self.cache = RetrievalCache(
            self.project_path / ".cache" / "visual-assets",
            self.config.cache_ttl_seconds,
        )
        self.adapters = adapters or build_default_adapters(client, self.cache, provider_names)
        self._custom_adapters = adapters is not None
        self.filter = CandidateFilter(self.policy_resolver)
        self.query_builder = QueryBuilder()
        self.validator = ImageValidator()
        self.analyzer = ImageAnalyzer(client)
        self.source_grounded_verifier = SourceGroundedVerifier()
        normalized_host_vision = host_native_vision.casefold().strip()
        if normalized_host_vision not in {"available", "unavailable", "unknown"}:
            raise ValueError("host_native_vision must be available, unavailable, or unknown")
        self.host_native_vision = normalized_host_vision
        self.ranker = ImageRanker()
        self.circuit_breaker = DomainCircuitBreaker(
            self.config.domain_failure_threshold,
            self.config.domain_cooldown_seconds,
        )
        self.downloader = DownloadManager(client, self.cache, self.config, self.circuit_breaker)
        self.manifest = AssetManifest(self.project_path)
        self.progress_callback = progress_callback or (lambda event: None)

    async def run_deck(self, requirements_path: Path) -> dict[str, Any]:
        payload = json.loads(requirements_path.read_text(encoding="utf-8-sig"))
        deck = payload.get("deck") or {}
        slots = [SlotRequirement.from_dict(item, deck) for item in payload.get("slots", [])]
        started = time.monotonic()
        preflight = await self.preflight(slots, probe_network=True)
        if not preflight["stage7_ready"]:
            return {
                "status": "capability-degraded",
                "requirements": str(requirements_path),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "slots": [
                    {
                        "slot_id": slot.slot_id,
                        "status": "capability-degraded",
                        "verification": preflight["slots"][slot.slot_id],
                    }
                    for slot in slots
                ],
                "preflight": preflight,
                "manifest": str(self.manifest.path),
            }
        results: list[dict[str, Any]] = []
        for slot in slots:
            elapsed = time.monotonic() - started
            if elapsed >= self.config.deck_deadline_seconds:
                results.append(
                    {
                        "slot_id": slot.slot_id,
                        "status": "skipped-deck-deadline",
                        "elapsed_seconds": round(elapsed, 3),
                    }
                )
                continue
            results.append(await self.run_slot(slot, preflight_checked=True))
        return {
            "status": "completed",
            "requirements": str(requirements_path),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "slots": results,
            "manifest": str(self.manifest.path),
        }

    async def preflight(self, slots: list[SlotRequirement], *, probe_network: bool = True) -> dict[str, Any]:
        return await CapabilityPreflight(
            self.project_path,
            self.client,
            self.adapters,
            self.policy_resolver,
            self.host_native_vision,
            self.analyzer.vision.available,
        ).run(slots, probe_network=probe_network)

    async def run_slot(self, slot: SlotRequirement, *, preflight_checked: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        state_path = (
            self.project_path
            / "research"
            / "visual-assets"
            / "retrieval-state"
            / f"{slot.slot_id}.json"
        )
        state = RetrievalState(state_path, slot.slot_id)
        state.cache_baseline = self.cache.snapshot_metrics()
        watchdog = PipelineWatchdog(
            self.config.slot_deadline_seconds,
            self.config.no_progress_seconds,
        )
        policy = self.policy_resolver.select(slot)
        verification = resolve_verification_mode(slot, policy)
        capabilities = self._capabilities()
        capabilities["host_native_vision"] = self.host_native_vision
        capabilities["external_visual_provider"] = (
            "available-optional" if self.analyzer.vision.available else "optional-unavailable"
        )
        capabilities["visual_semantic_verification_available"] = (
            self.host_native_vision == "available" or self.analyzer.vision.available
        )
        if not preflight_checked:
            preflight = await self.preflight([slot], probe_network=False)
            if not preflight["stage7_ready"]:
                state.transition("policy_selected", "source policy and verification mode selected", policy=policy.name)
                state.progress("capability preflight failed before retrieval", preflight=preflight)
                return self._unresolved(
                    slot,
                    policy,
                    state,
                    [],
                    started,
                    "search or network capability is unavailable for this photo slot",
                    status_override="capability-degraded",
                    capabilities=capabilities,
                    verification=verification,
                    verification_path="presentation-source-context",
                )
        state.transition("policy_selected", "source policy selected", policy=policy.name)
        state.progress("search backend capabilities evaluated", capabilities=capabilities, verification=verification.to_dict())
        self._emit(slot, state, "policy-selected", policy=policy.name)
        if state.resumed_from:
            state.progress(
                "resuming interrupted slot from persisted state; cache and manifest are reusable",
                resumed_from=state.resumed_from,
            )
        queries = self.query_builder.build(slot, policy, self.config.query_budget)
        state.transition("querying", "bounded queries generated", queries=queries)
        if not queries:
            return self._unresolved(
                slot, policy, state, queries, started, "no usable query generated",
                capabilities=capabilities, verification=verification,
            )
        if not capabilities["available"]:
            return self._unresolved(
                slot,
                policy,
                state,
                queries,
                started,
                "all configured search backends are unavailable",
                status_override="capability-degraded",
                capabilities=capabilities,
                verification=verification,
            )

        all_candidates: dict[str, SearchCandidate] = {}
        audit_candidates: dict[str, SearchCandidate] = {}
        attempted: set[str] = set()
        analyzed: list[SearchCandidate] = []
        perceptual_hashes: list[str] = []
        content_hashes = self.cache.content_hashes()
        saturation_count = 0

        try:
            for query in queries:
                watchdog.ensure_alive(state.last_progress_monotonic)
                if state.budgets.queries >= self.config.query_budget:
                    break
                state.transition("searching", "executing structured image search", query=query)
                state.budgets.queries += 1
                self._emit(slot, state, "search-start", query=query)
                query_candidates: list[SearchCandidate] = []
                candidates_by_provider: list[list[SearchCandidate]] = []
                for adapter in self.adapters:
                    remaining = watchdog.remaining()
                    if remaining <= 0:
                        raise SlotDeadlineExceeded("slot deadline exceeded before provider search")
                    if not adapter.available:
                        state.progress(
                            "search provider unavailable",
                            provider=adapter.name,
                            reason=adapter.unavailable_reason or "not configured",
                        )
                        continue
                    if query.lower().startswith(("http://", "https://")) and adapter.name != "official-page":
                        continue
                    try:
                        found = await watchdog.run(
                            adapter.search(query, self.config.results_per_query, policy),
                            min(self.config.operation_timeout_seconds, remaining),
                        )
                        if found:
                            candidates_by_provider.append(found)
                        state.progress(
                            "search provider returned",
                            provider=adapter.name,
                            query=query,
                            candidates=len(found),
                            cache_hit=adapter.last_cache_hit,
                        )
                    except (httpx.HTTPError, NoProgressError, ValueError) as exc:
                        state.progress(
                            "search provider failed; continuing",
                            provider=adapter.name,
                            query=query,
                            error=str(exc),
                        )
                        self._emit(slot, state, "search-provider-failed", provider=adapter.name, error=str(exc))
                for index in range(max((len(items) for items in candidates_by_provider), default=0)):
                    for items in candidates_by_provider:
                        if index < len(items):
                            query_candidates.append(items[index])
                for candidate in query_candidates:
                    audit_candidates.setdefault(candidate.candidate_id, candidate)
                previous_urls = {self.filter._canonical_url(item.image_url) for item in all_candidates.values()}
                overlap = self.filter.overlap_ratio(previous_urls, query_candidates)
                saturation_count = saturation_count + 1 if overlap >= self.config.query_saturation_threshold else 0
                combined = [*all_candidates.values(), *query_candidates]
                prefiltered = self.filter.filter_and_rank_metadata(combined, slot, policy)
                all_candidates = {
                    candidate.candidate_id: candidate
                    for candidate in prefiltered[: self.config.candidate_budget]
                }
                state.budgets.candidates = len(all_candidates)
                state.transition(
                    "filtering",
                    "candidate metadata filtered",
                    unique_candidates=len(all_candidates),
                    overlap=round(overlap, 3),
                )
                ranked = list(all_candidates.values())
                shortlist = [
                    candidate
                    for candidate in ranked
                    if candidate.candidate_id not in attempted
                ][: self.config.metadata_shortlist]

                for candidate in shortlist:
                    if state.budgets.downloads >= self.config.download_budget:
                        break
                    watchdog.ensure_alive(state.last_progress_monotonic)
                    attempted.add(candidate.candidate_id)
                    state.transition(
                        "downloading",
                        "downloading bounded candidate",
                        candidate_id=candidate.candidate_id,
                        domain=candidate.source_domain,
                    )
                    state.budgets.downloads += 1
                    try:
                        outcome = await watchdog.run(
                            self.downloader.download(
                                candidate,
                                self.project_path
                                / "research"
                                / "visual-assets"
                                / "candidates"
                                / slot.slot_id,
                            ),
                            min(self.config.operation_timeout_seconds, watchdog.remaining()),
                        )
                    except (DownloadError, httpx.HTTPError, NoProgressError) as exc:
                        candidate.status = "download-failed"
                        candidate.rejection_reason = str(exc)
                        state.progress("candidate download failed; continuing", candidate_id=candidate.candidate_id, error=str(exc))
                        continue
                    candidate.local_path = str(outcome.path)
                    candidate.cache_hit = outcome.cache_hit
                    state.budgets.retries += outcome.retries
                    if outcome.cache_hit:
                        state.budgets.cache_hits += 1
                    else:
                        state.budgets.network_downloads += 1
                    state.transition("validating", "validating downloaded pixels", candidate_id=candidate.candidate_id)
                    validation = self.validator.validate(outcome.path, outcome.content_type, slot)
                    candidate.validation = {"valid": validation.valid, "reason": validation.reason, **validation.details}
                    if not validation.valid:
                        candidate.status = "validation-failed"
                        candidate.rejection_reason = validation.reason
                        state.progress("candidate rejected by local validator", candidate_id=candidate.candidate_id, reason=validation.reason)
                        continue
                    candidate.content_sha256 = validation.details.get("content_sha256")
                    candidate.perceptual_hash = validation.details.get("perceptual_hash")
                    if candidate.content_sha256 and candidate.content_sha256 in content_hashes and not outcome.cache_hit:
                        candidate.status = "duplicate-rejected"
                        candidate.rejection_reason = "exact content duplicate"
                        state.progress("candidate rejected as exact content duplicate", candidate_id=candidate.candidate_id)
                        continue
                    if candidate.content_sha256:
                        content_hashes.add(candidate.content_sha256)
                    if candidate.perceptual_hash and self.validator.is_near_duplicate(
                        candidate.perceptual_hash,
                        perceptual_hashes,
                        self.config.duplicate_hamming_distance,
                    ):
                        candidate.status = "duplicate-rejected"
                        candidate.rejection_reason = "perceptual duplicate"
                        state.progress("candidate rejected as perceptual duplicate", candidate_id=candidate.candidate_id)
                        continue
                    if candidate.perceptual_hash:
                        perceptual_hashes.append(candidate.perceptual_hash)

                    state.transition("analyzing", "executing presentation-grade verification", candidate_id=candidate.candidate_id)
                    verification_path = "presentation-source-context"
                    source_grounded = self.source_grounded_verifier.verify(
                        candidate, slot, policy, verification
                    )
                    if source_grounded["passed"]:
                        candidate.analysis = self._source_grounded_analysis(candidate, source_grounded)
                    else:
                        candidate.analysis = self._verification_failure(
                            verification_path,
                            source_grounded["reason"],
                        )
                    candidate.analysis["verification"] = {
                        **verification.to_dict(),
                        "chosen_path": verification_path or candidate.analysis.get("verification_path"),
                        "provenance": candidate.provenance,
                        "source_grounded": source_grounded,
                        "host_native_vision": self.host_native_vision,
                        "host_visual_result": None,
                        "external_visual_provider_available": self.analyzer.vision.available,
                    }
                    candidate.hard_gate_verdict = dict(candidate.analysis.get("hard_gate_verdict") or {})
                    if not candidate.hard_gate_verdict.get("passed"):
                        candidate.status = "semantic-rejected"
                        failures = candidate.hard_gate_verdict.get("reasons") or ["unknown semantic hard gate"]
                        candidate.rejection_reason = "semantic hard gates failed: " + ", ".join(failures)
                        state.progress(
                            "candidate rejected by semantic hard gates",
                            candidate_id=candidate.candidate_id,
                            title=candidate.title,
                            reason=candidate.rejection_reason,
                            hard_gate_verdict=candidate.hard_gate_verdict,
                        )
                        continue
                    state.transition("ranking", "candidate scored", candidate_id=candidate.candidate_id)
                    self.ranker.score(candidate, slot, policy)
                    candidate.status = "ranked"
                    analyzed.append(candidate)
                    state.update_best(candidate.candidate_id, candidate.total_score)
                    good_enough, gate_reasons = self.ranker.is_good_enough(
                        candidate,
                        policy,
                        self.config.good_enough_threshold,
                    )
                    state.progress(
                        "quality gate evaluated",
                        candidate_id=candidate.candidate_id,
                        score=candidate.total_score,
                        good_enough=good_enough,
                        reasons=gate_reasons,
                    )
                    if good_enough:
                        candidate.status = "selected"
                        state.transition(
                            "selected",
                            "first usable presentation-grade candidate passed all basic gates; early stop",
                            candidate_id=candidate.candidate_id,
                        )
                        return self._selected(
                            slot,
                            policy,
                            state,
                            candidate,
                            queries[: state.budgets.queries],
                            started,
                            quality_gate_passed=True,
                            early_stop=True,
                            selection_reason="first readable, relevant, usable, non-contradictory, non-prohibited candidate passed",
                            candidate_audit=self._candidate_audit(audit_candidates),
                            capabilities=capabilities,
                            verification=verification,
                        )
                if state.budgets.downloads >= self.config.download_budget:
                    break
                if saturation_count >= self.config.query_saturation_limit:
                    state.progress("query saturation detected; stopping expansion", overlap=overlap)
                    break

            if analyzed:
                best = max(analyzed, key=lambda item: item.total_score)
                state.transition("selected", "bounded search ended; selecting usable best-so-far", candidate_id=best.candidate_id)
                return self._selected(
                    slot, policy, state, best, queries[: state.budgets.queries], started,
                    quality_gate_passed=True, early_stop=False,
                    selection_reason="bounded search ended with a usable presentation-grade best-so-far candidate",
                    candidate_audit=self._candidate_audit(audit_candidates),
                    capabilities=capabilities, verification=verification,
                )
            return self._unresolved(
                slot,
                policy,
                state,
                queries[: state.budgets.queries],
                started,
                "bounded search completed without any downloadable, readable, relevant, and permitted photo",
                status_override=(None if policy.allow_ai_fallback else "capability-degraded"),
                candidate_audit=self._candidate_audit(audit_candidates),
                capabilities=capabilities,
                verification=verification,
            )
        except (SlotDeadlineExceeded, NoProgressError) as exc:
            if analyzed:
                best = max(analyzed, key=lambda item: item.total_score)
                state.transition("selected", "slot deadline reached; selecting usable best-so-far", candidate_id=best.candidate_id)
                return self._selected(
                    slot, policy, state, best, queries[: state.budgets.queries], started,
                    quality_gate_passed=True, early_stop=False,
                    selection_reason=f"slot budget ended ({exc}); usable presentation-grade best-so-far selected",
                    candidate_audit=self._candidate_audit(audit_candidates),
                    capabilities=capabilities, verification=verification,
                )
            return self._unresolved(
                slot, policy, state, queries[: state.budgets.queries], started, str(exc),
                candidate_audit=self._candidate_audit(audit_candidates), capabilities=capabilities,
                verification=verification,
            )

    def _selected(
        self,
        slot: SlotRequirement,
        policy: Any,
        state: RetrievalState,
        candidate: SearchCandidate,
        queries: list[str],
        started: float,
        *,
        quality_gate_passed: bool,
        early_stop: bool,
        selection_reason: str,
        candidate_audit: list[dict[str, Any]],
        capabilities: dict[str, Any],
        verification: VerificationDecision,
    ) -> dict[str, Any]:
        elapsed = round(time.monotonic() - started, 3)
        stats = self._stats(state, elapsed)
        item = self.manifest.record_selection(
            slot,
            policy,
            candidate,
            quality_gate_passed=quality_gate_passed,
            early_stop=early_stop,
            queries=queries,
            stats=stats,
            selection_reason=selection_reason,
            candidate_audit=candidate_audit,
            search_backends=capabilities,
            verification=verification.to_dict(),
            verification_path=(candidate.analysis.get("verification") or {}).get("chosen_path"),
            provenance=candidate.provenance,
        )
        self._emit(slot, state, "selected", asset=item)
        return {
            "slot_id": slot.slot_id,
            "status": "selected",
            "source_policy": policy.name,
            "quality_gate_passed": quality_gate_passed,
            "early_stop": early_stop,
            "selected_asset": item,
            "stats": stats,
            "candidate_audit": candidate_audit,
            "search_backends": capabilities,
            "verification": candidate.analysis.get("verification") or verification.to_dict(),
        }

    def _unresolved(
        self,
        slot: SlotRequirement,
        policy: Any,
        state: RetrievalState,
        queries: list[str],
        started: float,
        reason: str,
        *,
        status_override: str | None = None,
        candidate_audit: list[dict[str, Any]] | None = None,
        capabilities: dict[str, Any] | None = None,
        verification: VerificationDecision | None = None,
        verification_path: str | None = None,
    ) -> dict[str, Any]:
        status = status_override or ("fallback-required" if policy.allow_ai_fallback else "unresolved")
        terminal = "fallback" if policy.allow_ai_fallback else "failed"
        state.transition(terminal, reason)
        elapsed = round(time.monotonic() - started, 3)
        stats = self._stats(state, elapsed)
        self.manifest.record_unresolved(
            slot, policy, status, queries, stats, reason,
            candidate_audit=candidate_audit or [], search_backends=capabilities or {},
            verification=verification.to_dict() if verification else {},
            verification_path=verification_path,
        )
        self._emit(slot, state, status, reason=reason)
        return {
            "slot_id": slot.slot_id,
            "status": status,
            "source_policy": policy.name,
            "reason": reason,
            "early_stop": False,
            "stats": stats,
            "candidate_audit": candidate_audit or [],
            "search_backends": capabilities or {},
            "verification": verification.to_dict() if verification else {},
        }

    @staticmethod
    def _source_grounded_analysis(candidate: SearchCandidate, evidence: dict[str, Any]) -> dict[str, Any]:
        confidence = float(evidence.get("confidence", 0.75))
        clarity = float(candidate.validation.get("clarity_score", 70.0))
        gates = {
            name: {
                "required": True,
                "passed": bool(passed),
                "confidence": confidence,
                "evidence": evidence["reason"],
            }
            for name, passed in evidence.get("gates", {}).items()
        }
        return {
            "provider": "presentation-source-context",
            "semantic_match": round(confidence * 100, 3),
            "authenticity": round(confidence * 100, 3),
            "composition": 72.0,
            "clarity": clarity,
            "summary": evidence["reason"],
            "hard_gate_verdict": {
                "passed": True,
                "provider": "presentation-source-context",
                "gates": gates,
                "reasons": [],
                "visual_semantic_validation": {
                    "required": False,
                    "passed": False,
                    "provider": None,
                },
            },
            "verification_path": "presentation-source-context",
            "verification_method": "presentation-grade",
            "verification_evidence": evidence,
            "evidence_strength": EVIDENCE_STRENGTH["presentation-grade"],
            "visual_review_actor": None,
            "confidence": confidence,
            "verification_timestamp": None,
            "capability_degraded": False,
        }

    @staticmethod
    def _verification_failure(
        path: str,
        reason: str,
        *,
        capability_degraded: bool = False,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "provider": f"{path}-verification-failed",
            "semantic_match": 0.0,
            "authenticity": 0.0,
            "composition": 0.0,
            "clarity": 0.0,
            "capability_degraded": capability_degraded,
            "capability_reason": reason if capability_degraded else None,
            "verification_path": path,
            "hard_gate_verdict": {
                "passed": False,
                "provider": f"{path}-verification-failed",
                "gates": (provenance or {}).get("gates", {}),
                "reasons": [reason],
            },
        }

    def _stats(self, state: RetrievalState, elapsed: float) -> dict[str, Any]:
        snapshot = state.snapshot(self.circuit_breaker.snapshot())
        counters = snapshot["budgets"]
        baseline = getattr(state, "cache_baseline", {})
        cache_now = self.cache.snapshot_metrics()
        cache_delta = {key: cache_now.get(key, 0) - baseline.get(key, 0) for key in cache_now}
        counters.update(cache_delta)
        counters["cache_hits"] = cache_delta["query_cache_hits"] + cache_delta["download_cache_hits"]
        counters["cache_misses"] = cache_delta["query_cache_misses"] + cache_delta["download_cache_misses"]
        return {
            "elapsed_seconds": elapsed,
            **counters,
            "best_so_far": snapshot["best_so_far"],
            "domain_circuits": snapshot["domains"],
        }

    def _capabilities(self) -> dict[str, Any]:
        available = [adapter.name for adapter in self.adapters if adapter.available]
        unavailable = {
            adapter.name: adapter.unavailable_reason or "not available"
            for adapter in self.adapters if not adapter.available
        }
        return {
            "available": available,
            "unavailable": unavailable,
            "general_search_available": any(
                adapter.available and adapter.capability_kind == "general-web" for adapter in self.adapters
            ),
            "institutional_search_available": any(
                adapter.available and adapter.capability_kind == "institutional-repository" for adapter in self.adapters
            ),
            "repository_search_available": any(
                adapter.available and adapter.capability_kind == "media-repository" for adapter in self.adapters
            ),
        }

    @staticmethod
    def _candidate_audit(candidates: dict[str, SearchCandidate]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for item in candidates.values():
            status = item.status
            reason = item.rejection_reason
            if status == "filtered" and not reason:
                status = "not-evaluated"
                reason = "not evaluated after early stop or bounded shortlist/budget"
            output.append({
                "candidate_id": item.candidate_id,
                "title": item.title,
                "provider": item.provider,
                "source_page_url": item.source_page_url,
                "status": status,
                "rejection_reason": reason,
                "hard_gate_verdict": item.hard_gate_verdict or item.analysis.get("hard_gate_verdict", {}),
                "verification": item.analysis.get("verification", {}),
                "provenance": item.provenance,
                "total_score": item.total_score,
            })
        return output

    def _emit(self, slot: SlotRequirement, state: RetrievalState, event: str, **data: Any) -> None:
        self.progress_callback(
            {
                "event": event,
                "slot_id": slot.slot_id,
                "state": state.state,
                "at": time.time(),
                **data,
            }
        )

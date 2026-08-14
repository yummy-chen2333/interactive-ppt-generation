from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .retrieval_cache import atomic_write_json


_ALLOWED_TRANSITIONS = {
    "pending": {"policy_selected", "failed"},
    "policy_selected": {"querying", "failed"},
    "querying": {"searching", "failed"},
    "searching": {"filtering", "failed"},
    "filtering": {"downloading", "searching", "selected", "fallback", "failed"},
    "downloading": {"validating", "searching", "fallback", "failed"},
    "validating": {"analyzing", "downloading", "searching", "fallback", "failed"},
    "analyzing": {"visual_review_pending", "ranking", "downloading", "searching", "fallback", "failed"},
    "visual_review_pending": {"analyzing", "ranking", "downloading", "searching", "fallback", "failed"},
    "ranking": {"selected", "downloading", "searching", "fallback", "failed"},
    "selected": set(),
    "fallback": set(),
    "failed": set(),
}


@dataclass(slots=True)
class BudgetCounters:
    queries: int = 0
    candidates: int = 0
    downloads: int = 0
    network_downloads: int = 0
    visual_analyses: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    query_cache_hits: int = 0
    query_cache_misses: int = 0
    download_cache_hits: int = 0
    download_cache_misses: int = 0
    retries: int = 0


@dataclass(slots=True)
class DomainStatus:
    consecutive_failures: int = 0
    opened_at: float | None = None
    last_error: str | None = None


class DomainCircuitBreaker:
    def __init__(self, threshold: int, cooldown_seconds: float):
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self.domains: dict[str, DomainStatus] = {}

    def allow(self, domain: str) -> bool:
        status = self.domains.get(domain)
        if not status or status.opened_at is None:
            return True
        if time.monotonic() - status.opened_at >= self.cooldown_seconds:
            status.consecutive_failures = 0
            status.opened_at = None
            return True
        return False

    def success(self, domain: str) -> None:
        self.domains[domain] = DomainStatus()

    def failure(self, domain: str, error: str) -> None:
        status = self.domains.setdefault(domain, DomainStatus())
        status.consecutive_failures += 1
        status.last_error = error
        if status.consecutive_failures >= self.threshold:
            status.opened_at = time.monotonic()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            domain: {
                "consecutive_failures": status.consecutive_failures,
                "circuit_open": status.opened_at is not None,
                "last_error": status.last_error,
            }
            for domain, status in self.domains.items()
        }


class RetrievalState:
    def __init__(self, path: Path, slot_id: str):
        self.path = path
        self.slot_id = slot_id
        prior = self._load_prior()
        prior_state = str(prior.get("state") or "pending")
        self.resumed_from = prior_state if prior_state not in {"pending", "selected", "fallback", "failed"} else None
        self.state = "pending"
        self.started_at = time.time()
        self.last_progress_wall = self.started_at
        self.last_progress_monotonic = time.monotonic()
        self.budgets = BudgetCounters()
        self.best_so_far: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self._persist()

    def transition(self, next_state: str, message: str, **data: Any) -> None:
        if next_state != self.state and next_state not in _ALLOWED_TRANSITIONS.get(self.state, set()):
            raise RuntimeError(f"Invalid retrieval transition: {self.state} -> {next_state}")
        self.state = next_state
        self.progress(message, **data)

    def progress(self, message: str, **data: Any) -> None:
        self.last_progress_wall = time.time()
        self.last_progress_monotonic = time.monotonic()
        event = {
            "at": self.last_progress_wall,
            "state": self.state,
            "message": message,
            **data,
        }
        self.events.append(event)
        self.events = self.events[-100:]
        self._persist()

    def update_best(self, candidate_id: str, score: float) -> None:
        if self.best_so_far is None or score > float(self.best_so_far["score"]):
            self.best_so_far = {"candidate_id": candidate_id, "score": round(score, 3)}
            self.progress("best-so-far updated", candidate_id=candidate_id, score=score)

    def snapshot(self, domains: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "slot_id": self.slot_id,
            "state": self.state,
            "resumed_from": self.resumed_from,
            "started_at": self.started_at,
            "last_progress_at": self.last_progress_wall,
            "budgets": {
                "queries": self.budgets.queries,
                "candidates": self.budgets.candidates,
                "downloads": self.budgets.downloads,
                "network_downloads": self.budgets.network_downloads,
                "visual_analyses": self.budgets.visual_analyses,
                "cache_hits": self.budgets.cache_hits,
                "cache_misses": self.budgets.cache_misses,
                "query_cache_hits": self.budgets.query_cache_hits,
                "query_cache_misses": self.budgets.query_cache_misses,
                "download_cache_hits": self.budgets.download_cache_hits,
                "download_cache_misses": self.budgets.download_cache_misses,
                "retries": self.budgets.retries,
            },
            "best_so_far": self.best_so_far,
            "domains": domains or {},
            "events": self.events,
        }

    def _persist(self) -> None:
        atomic_write_json(self.path, self.snapshot())

    def _load_prior(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

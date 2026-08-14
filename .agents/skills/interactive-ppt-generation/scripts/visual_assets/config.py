from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class RetrievalConfig:
    query_budget: int = 2
    results_per_query: int = 8
    candidate_budget: int = 5
    metadata_shortlist: int = 5
    download_budget: int = 2
    visual_analysis_budget: int = 1
    host_visual_review_budget: int = 1
    download_concurrency: int = 2
    retry_limit: int = 1
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 12.0
    operation_timeout_seconds: float = 15.0
    slot_deadline_seconds: float = 45.0
    deck_deadline_seconds: float = 600.0
    no_progress_seconds: float = 15.0
    domain_failure_threshold: int = 2
    domain_cooldown_seconds: float = 120.0
    max_download_bytes: int = 25 * 1024 * 1024
    cache_ttl_seconds: int = 7 * 24 * 60 * 60
    good_enough_threshold: float = 76.0
    duplicate_hamming_distance: int = 5
    query_saturation_threshold: float = 0.82
    query_saturation_limit: int = 2
    user_agent: str = "InteractivePPTVisualAssets/1.0 (https://github.com/)"

    @classmethod
    def from_env(cls, overrides: dict[str, Any] | None = None) -> "RetrievalConfig":
        config = cls()
        prefix = "VISUAL_ASSET_"
        type_map = {field: type(getattr(config, field)) for field in asdict(config)}
        for name, target_type in type_map.items():
            value = os.getenv(prefix + name.upper())
            if value is None:
                continue
            if target_type is bool:
                parsed: Any = value.lower() in {"1", "true", "yes", "on"}
            else:
                parsed = target_type(value)
            setattr(config, name, parsed)
        for name, value in (overrides or {}).items():
            if not hasattr(config, name):
                raise ValueError(f"Unknown retrieval config option: {name}")
            current = getattr(config, name)
            setattr(config, name, type(current)(value))
        config.validate()
        return config

    def validate(self) -> None:
        positive_ints = (
            "query_budget",
            "results_per_query",
            "candidate_budget",
            "metadata_shortlist",
            "download_budget",
            "visual_analysis_budget",
            "host_visual_review_budget",
            "download_concurrency",
            "max_download_bytes",
        )
        for name in positive_ints:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.retry_limit < 0:
            raise ValueError("retry_limit cannot be negative")
        hard_caps = {
            "query_budget": 2,
            "candidate_budget": 5,
            "metadata_shortlist": 5,
            "download_budget": 2,
            "visual_analysis_budget": 1,
            "host_visual_review_budget": 1,
            "retry_limit": 1,
            "slot_deadline_seconds": 45.0,
        }
        for name, maximum in hard_caps.items():
            if getattr(self, name) > maximum:
                raise ValueError(f"{name} cannot exceed presentation-grade hard cap {maximum}")
        if not 0 <= self.good_enough_threshold <= 100:
            raise ValueError("good_enough_threshold must be between 0 and 100")
        if self.operation_timeout_seconds > self.slot_deadline_seconds:
            raise ValueError("operation timeout cannot exceed the slot deadline")

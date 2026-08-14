from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


class RetrievalCache:
    def __init__(self, root: Path, ttl_seconds: int):
        self.root = root
        self.ttl_seconds = ttl_seconds
        self.query_dir = root / "queries"
        self.download_dir = root / "downloads"
        self.query_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.metrics = {
            "query_cache_hits": 0,
            "query_cache_misses": 0,
            "download_cache_hits": 0,
            "download_cache_misses": 0,
        }

    @staticmethod
    def key(*parts: str) -> str:
        digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
        return digest

    def get_query(self, provider: str, query: str, scope: str) -> list[dict[str, Any]] | None:
        path = self.query_dir / f"{self.key(provider, query, scope)}.json"
        payload = self._fresh_json(path)
        if payload is None:
            self.metrics["query_cache_misses"] += 1
            return None
        results = payload.get("results")
        self.metrics["query_cache_hits"] += 1
        return results if isinstance(results, list) else None

    def put_query(self, provider: str, query: str, scope: str, results: list[dict[str, Any]]) -> None:
        path = self.query_dir / f"{self.key(provider, query, scope)}.json"
        atomic_write_json(
            path,
            {
                "cached_at": time.time(),
                "provider": provider,
                "query": query,
                "scope": scope,
                "results": results,
            },
        )

    def get_download(self, url: str) -> tuple[Path, dict[str, Any]] | None:
        key = self.key(url)
        metadata_path = self.download_dir / f"{key}.json"
        metadata = self._fresh_json(metadata_path)
        if metadata is None:
            self.metrics["download_cache_misses"] += 1
            return None
        content_path = self.download_dir / str(metadata.get("content_file", ""))
        if not content_path.is_file():
            self.metrics["download_cache_misses"] += 1
            return None
        self.metrics["download_cache_hits"] += 1
        return content_path, metadata

    def snapshot_metrics(self) -> dict[str, int]:
        return dict(self.metrics)

    def put_download(self, url: str, source_path: Path, metadata: dict[str, Any]) -> Path:
        key = self.key(url)
        suffix = source_path.suffix.lower() or ".img"
        content_path = self.download_dir / f"{key}{suffix}"
        if source_path.resolve() != content_path.resolve():
            temporary = content_path.with_name(content_path.name + ".tmp")
            shutil.copy2(source_path, temporary)
            os.replace(temporary, content_path)
        payload = dict(metadata)
        payload.update(
            {
                "cached_at": time.time(),
                "url": url,
                "content_file": content_path.name,
            }
        )
        atomic_write_json(self.download_dir / f"{key}.json", payload)
        return content_path

    def content_hashes(self) -> set[str]:
        output: set[str] = set()
        for metadata_path in self.download_dir.glob("*.json"):
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            content_hash = payload.get("content_sha256")
            if content_hash:
                output.add(str(content_hash))
        return output

    def _fresh_json(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cached_at = float(payload.get("cached_at", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if time.time() - cached_at > self.ttl_seconds:
            return None
        return payload

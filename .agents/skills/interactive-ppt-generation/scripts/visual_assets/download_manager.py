from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import fitz

from .config import RetrievalConfig
from .models import SearchCandidate
from .retrieval_cache import RetrievalCache
from .retrieval_state import DomainCircuitBreaker


class DownloadError(RuntimeError):
    pass


class RateLimitError(DownloadError):
    pass


class DomainCircuitOpen(DownloadError):
    pass


@dataclass(slots=True)
class DownloadOutcome:
    path: Path
    content_type: str
    bytes_downloaded: int
    cache_hit: bool
    retries: int


class DownloadManager:
    def __init__(
        self,
        client: httpx.AsyncClient,
        cache: RetrievalCache,
        config: RetrievalConfig,
        circuit_breaker: DomainCircuitBreaker,
    ):
        self.client = client
        self.cache = cache
        self.config = config
        self.circuit_breaker = circuit_breaker
        self.semaphore = asyncio.Semaphore(config.download_concurrency)

    async def download(self, candidate: SearchCandidate, destination_dir: Path) -> DownloadOutcome:
        async with self.semaphore:
            destination_dir.mkdir(parents=True, exist_ok=True)
            cached = self.cache.get_download(candidate.image_url)
            if cached:
                cached_path, metadata = cached
                cached_provenance = metadata.get("document_provenance")
                requires_page_binding = bool(candidate.document_asset.get("page_search_terms"))
                if requires_page_binding and not (
                    isinstance(cached_provenance, dict)
                    and cached_provenance.get("rendered_page_matched_terms")
                ):
                    cached = None
                else:
                    if isinstance(cached_provenance, dict):
                        candidate.provenance.update(cached_provenance)
                    destination = destination_dir / self._filename(candidate, cached_path.suffix)
                    shutil.copy2(cached_path, destination)
                    return DownloadOutcome(
                        path=destination,
                        content_type=str(metadata.get("content_type", "application/octet-stream")),
                        bytes_downloaded=int(metadata.get("bytes_downloaded", destination.stat().st_size)),
                        cache_hit=True,
                        retries=0,
                    )

            domain = (urlparse(candidate.image_url).hostname or candidate.source_domain).lower()
            if not self.circuit_breaker.allow(domain):
                raise DomainCircuitOpen(f"domain circuit is open: {domain}")

            retries = 0
            last_error: Exception | None = None
            for attempt in range(self.config.retry_limit + 1):
                try:
                    outcome = await self._download_once(candidate, destination_dir)
                    outcome.retries = retries
                    self.circuit_breaker.success(domain)
                    self.cache.put_download(
                        candidate.image_url,
                        outcome.path,
                        {
                            "content_type": outcome.content_type,
                            "bytes_downloaded": outcome.bytes_downloaded,
                            "content_sha256": candidate.content_sha256,
                            "document_provenance": candidate.provenance,
                        },
                    )
                    return outcome
                except RateLimitError as exc:
                    self.circuit_breaker.failure(domain, str(exc))
                    raise
                except (httpx.TimeoutException, httpx.NetworkError, DownloadError) as exc:
                    last_error = exc
                    self.circuit_breaker.failure(domain, str(exc))
                    if attempt >= self.config.retry_limit or not self.circuit_breaker.allow(domain):
                        break
                    retries += 1
                    await asyncio.sleep(min(0.35 * (2**attempt), 1.5))
            raise DownloadError(f"download failed after {retries} retries: {last_error}")

    async def _download_once(self, candidate: SearchCandidate, destination_dir: Path) -> DownloadOutcome:
        async with self.client.stream("GET", candidate.image_url) as response:
            if response.status_code == 429:
                raise RateLimitError(f"429 from {candidate.source_domain}")
            if response.status_code >= 500:
                raise DownloadError(f"upstream {response.status_code}")
            response.raise_for_status()
            content_length = int(response.headers.get("content-length", "0") or 0)
            if content_length > self.config.max_download_bytes:
                raise DownloadError("image exceeds maximum download size")
            content_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
            is_document = content_type == "application/pdf" or bool(candidate.document_asset)
            suffix = ".pdf" if is_document else self._suffix(candidate.image_url, content_type)
            destination = destination_dir / self._filename(candidate, suffix)
            temporary = destination.with_name(destination.name + ".part")
            total = 0
            digest = hashlib.sha256()
            try:
                with temporary.open("wb") as handle:
                    async for chunk in response.aiter_bytes(64 * 1024):
                        total += len(chunk)
                        if total > self.config.max_download_bytes:
                            raise DownloadError("image exceeded maximum download size while streaming")
                        digest.update(chunk)
                        handle.write(chunk)
                os.replace(temporary, destination)
            finally:
                if temporary.exists():
                    temporary.unlink()
            candidate.content_sha256 = digest.hexdigest()
            if is_document:
                rendered = self._render_document_page(destination, candidate)
                destination.unlink(missing_ok=True)
                return DownloadOutcome(rendered, "image/png", total, False, 0)
            return DownloadOutcome(destination, content_type, total, False, 0)

    @staticmethod
    def _render_document_page(document: Path, candidate: SearchCandidate) -> Path:
        page_index = max(0, int(candidate.document_asset.get("page_index", 0)))
        search_terms = [
            str(term).casefold() for term in candidate.document_asset.get("page_search_terms", [])
            if str(term).strip()
        ]
        rendered = document.with_suffix(".png")
        try:
            with fitz.open(document) as pdf:
                if not pdf.page_count:
                    raise DownloadError("PDF has no renderable pages")
                page_index = min(page_index, pdf.page_count - 1)
                best_text = ""
                matched_terms: list[str] = []
                if search_terms:
                    best_score = 0
                    for index in range(pdf.page_count):
                        text = " ".join(pdf.load_page(index).get_text("text").split()).casefold()
                        current = [term for term in search_terms if term in text]
                        if len(current) > best_score:
                            page_index = index
                            best_score = len(current)
                            best_text = text
                            matched_terms = current
                        if best_score == len(search_terms):
                            break
                    if not matched_terms:
                        raise DownloadError("PDF contains no page matching the required entity terms")
                page = pdf.load_page(page_index)
                if not best_text:
                    best_text = " ".join(page.get_text("text").split()).casefold()
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2.2, 2.2), alpha=False)
                pixmap.save(rendered)
        except (fitz.FileDataError, RuntimeError, ValueError) as exc:
            rendered.unlink(missing_ok=True)
            raise DownloadError(f"PDF page rendering failed: {exc}") from exc
        candidate.provenance["rendered_page_index"] = page_index
        candidate.provenance["render_method"] = "PyMuPDF deterministic page render"
        candidate.provenance["rendered_page_matched_terms"] = matched_terms
        candidate.provenance["rendered_page_text_excerpt"] = best_text[:800]
        candidate.provenance["asset_url"] = candidate.image_url
        return rendered

    @staticmethod
    def _suffix(url: str, content_type: str) -> str:
        extension = mimetypes.guess_extension(content_type) or Path(urlparse(url).path).suffix
        extension = extension.lower()
        if extension == ".jpe":
            extension = ".jpg"
        if not re.fullmatch(r"\.[a-z0-9]{2,5}", extension):
            extension = ".img"
        return extension

    @staticmethod
    def _filename(candidate: SearchCandidate, suffix: str) -> str:
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate.title).strip("-.")[:60]
        if not stem:
            stem = candidate.candidate_id
        return f"{candidate.candidate_id}-{stem}{suffix}"

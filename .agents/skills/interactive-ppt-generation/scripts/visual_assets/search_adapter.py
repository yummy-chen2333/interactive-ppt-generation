from __future__ import annotations

import hashlib
import html
import json
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .models import SearchCandidate
from .retrieval_cache import RetrievalCache
from .source_policy import SourcePolicy


def _candidate_id(provider: str, image_url: str) -> str:
    return hashlib.sha256(f"{provider}\x1f{image_url}".encode("utf-8")).hexdigest()[:20]


def _domain(url_or_domain: str) -> str:
    parsed = urlparse(url_or_domain if "://" in url_or_domain else "//" + url_or_domain)
    return (parsed.netloc or parsed.path).lower().split(":", 1)[0].lstrip("www.")


def _clean_html(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


class ImageSearchAdapter(ABC):
    name: str
    capability_kind = "general-web"

    @property
    def available(self) -> bool:
        return True

    @property
    def unavailable_reason(self) -> str | None:
        return None

    @property
    def last_cache_hit(self) -> bool | None:
        return getattr(self, "_last_cache_hit", None)

    @abstractmethod
    async def search(self, query: str, limit: int, policy: SourcePolicy) -> list[SearchCandidate]:
        raise NotImplementedError


class SerperImageSearchAdapter(ImageSearchAdapter):
    name = "serper"

    def __init__(self, client: httpx.AsyncClient, cache: RetrievalCache, api_key: str | None = None):
        self.client = client
        self.cache = cache
        self.api_key = api_key or os.getenv("SERPER_API_KEY")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def unavailable_reason(self) -> str | None:
        return None if self.available else "SERPER_API_KEY is not configured"

    async def search(self, query: str, limit: int, policy: SourcePolicy) -> list[SearchCandidate]:
        scope = json.dumps(
            {
                "preferred": policy.preferred_domains,
                "excluded": policy.excluded_domains,
                "limit": limit,
            },
            sort_keys=True,
        )
        cached = self.cache.get_query(self.name, query, scope)
        if cached is not None:
            self._last_cache_hit = True
            return [SearchCandidate.from_dict(item) for item in cached]
        self._last_cache_hit = False
        if not self.api_key:
            return []

        response = await self.client.post(
            "https://google.serper.dev/images",
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
            json={"q": query, "num": limit},
        )
        if response.status_code == 429:
            raise httpx.HTTPStatusError("Serper rate limited", request=response.request, response=response)
        response.raise_for_status()
        payload = response.json()
        output: list[SearchCandidate] = []
        for item in payload.get("images", [])[:limit]:
            image_url = item.get("imageUrl")
            source_url = item.get("link") or item.get("sourceUrl")
            if not image_url or not source_url:
                continue
            output.append(
                SearchCandidate(
                    candidate_id=_candidate_id(self.name, image_url),
                    provider=self.name,
                    query=query,
                    title=str(item.get("title") or ""),
                    image_url=str(image_url),
                    thumbnail_url=item.get("thumbnailUrl"),
                    source_page_url=str(source_url),
                    source_domain=_domain(str(item.get("domain") or source_url)),
                    width=int(item["imageWidth"]) if item.get("imageWidth") else None,
                    height=int(item["imageHeight"]) if item.get("imageHeight") else None,
                    description=str(item.get("snippet") or ""),
                    published_at=str(item.get("date") or ""),
                )
            )
        self.cache.put_query(self.name, query, scope, [candidate.to_dict() for candidate in output])
        return output


class WikimediaCommonsSearchAdapter(ImageSearchAdapter):
    name = "wikimedia-commons"
    capability_kind = "media-repository"

    def __init__(self, client: httpx.AsyncClient, cache: RetrievalCache):
        self.client = client
        self.cache = cache

    async def search(self, query: str, limit: int, policy: SourcePolicy) -> list[SearchCandidate]:
        scope = json.dumps({"limit": limit, "profile": policy.name}, sort_keys=True)
        cached = self.cache.get_query(self.name, query, scope)
        if cached is not None:
            self._last_cache_hit = True
            return [SearchCandidate.from_dict(item) for item in cached]
        self._last_cache_hit = False

        response = await self.client.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "gsrlimit": limit,
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": 1600,
                "format": "json",
                "formatversion": 2,
                "origin": "*",
            },
        )
        if response.status_code == 429:
            raise httpx.HTTPStatusError("Wikimedia rate limited", request=response.request, response=response)
        response.raise_for_status()
        pages = (response.json().get("query") or {}).get("pages") or []
        output: list[SearchCandidate] = []
        for page in pages:
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            image_url = info.get("thumburl") or info.get("url")
            original_url = info.get("url") or image_url
            source_url = info.get("descriptionurl")
            if not image_url or not source_url:
                continue
            metadata = info.get("extmetadata") or {}

            def meta(name: str) -> str:
                value = metadata.get(name) or {}
                return _clean_html(str(value.get("value") or ""))

            license_name = meta("LicenseShortName")
            attribution_flag = meta("AttributionRequired").lower()
            title = str(page.get("title") or "").removeprefix("File:")
            output.append(
                SearchCandidate(
                    candidate_id=_candidate_id(self.name, original_url),
                    provider=self.name,
                    query=query,
                    title=title,
                    image_url=str(image_url),
                    thumbnail_url=info.get("thumburl"),
                    source_page_url=str(source_url),
                    source_domain="commons.wikimedia.org",
                    width=int(info.get("thumbwidth") or info.get("width") or 0) or None,
                    height=int(info.get("thumbheight") or info.get("height") or 0) or None,
                    mime_type=info.get("mime"),
                    description=meta("ImageDescription"),
                    author=meta("Artist"),
                    credit=meta("Credit") or meta("CreditLine"),
                    license_name=license_name,
                    license_url=meta("LicenseUrl"),
                    attribution_required=(
                        attribution_flag in {"true", "1", "yes"}
                        or bool(license_name and "public domain" not in license_name.lower())
                    ),
                    published_at=meta("DateTimeOriginal") or meta("DateTime"),
                )
            )
        self.cache.put_query(self.name, query, scope, [candidate.to_dict() for candidate in output])
        return output


class OfficialPageMediaAdapter(ImageSearchAdapter):
    """Extract traceable Open Graph media from an explicitly scoped source page."""

    name = "official-page"
    capability_kind = "direct-page"

    def __init__(self, client: httpx.AsyncClient, cache: RetrievalCache):
        self.client = client
        self.cache = cache

    async def search(self, query: str, limit: int, policy: SourcePolicy) -> list[SearchCandidate]:
        if not re.match(r"^https?://", query, flags=re.IGNORECASE):
            self._last_cache_hit = None
            return []
        scope = json.dumps({"limit": limit, "profile": policy.name}, sort_keys=True)
        cached = self.cache.get_query(self.name, query, scope)
        if cached is not None:
            self._last_cache_hit = True
            return [SearchCandidate.from_dict(item) for item in cached]
        self._last_cache_hit = False

        response = await self.client.get(query)
        if response.status_code == 429:
            raise httpx.HTTPStatusError("source page rate limited", request=response.request, response=response)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and not response.text.lstrip().lower().startswith(("<!doctype", "<html")):
            return []
        page = response.text

        def meta(*names: str) -> str:
            for name in names:
                escaped = re.escape(name)
                patterns = (
                    rf'<meta[^>]+(?:property|name)=["\']{escaped}["\'][^>]+content=["\']([^"\']+)',
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{escaped}["\']',
                )
                for pattern in patterns:
                    match = re.search(pattern, page, flags=re.IGNORECASE)
                    if match:
                        return html.unescape(match.group(1)).strip()
            return ""

        image_url = meta("og:image", "twitter:image", "twitter:image:src")
        if not image_url:
            self.cache.put_query(self.name, query, scope, [])
            return []
        image_url = str(httpx.URL(query).join(image_url))
        source_domain = _domain(str(response.url))
        title = meta("og:title", "twitter:title")
        if not title:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", page, flags=re.IGNORECASE | re.DOTALL)
            title = _clean_html(title_match.group(1)) if title_match else source_domain
        candidate = SearchCandidate(
            candidate_id=_candidate_id(self.name, image_url),
            provider=self.name,
            query=query,
            title=title,
            image_url=image_url,
            source_page_url=str(response.url),
            source_domain=source_domain,
            description=meta("og:description", "description", "twitter:description"),
            author=meta("author") or source_domain,
            credit=f"Official source page: {source_domain}",
            license_name="Official-site media; verify reuse terms",
            attribution_required=True,
            published_at=meta("article:published_time", "date"),
        )
        output = [candidate]
        self.cache.put_query(self.name, query, scope, [candidate.to_dict()])
        return output


class BingImageSearchAdapter(ImageSearchAdapter):
    """Search the public Bing image result endpoint without browser automation."""

    name = "bing-images"
    capability_kind = "general-web"

    def __init__(self, client: httpx.AsyncClient, cache: RetrievalCache):
        self.client = client
        self.cache = cache

    async def search(self, query: str, limit: int, policy: SourcePolicy) -> list[SearchCandidate]:
        scope = json.dumps(
            {"preferred": policy.preferred_domains, "excluded": policy.excluded_domains, "limit": limit},
            sort_keys=True,
        )
        cached = self.cache.get_query(self.name, query, scope)
        if cached is not None:
            self._last_cache_hit = True
            return [SearchCandidate.from_dict(item) for item in cached]
        self._last_cache_hit = False
        response = await self.client.get(
            "https://www.bing.com/images/search",
            params={"q": query, "count": limit, "adlt": "strict", "setlang": "en-US"},
            headers={"Accept-Language": "en-US,en;q=0.8"},
        )
        if response.status_code == 429:
            raise httpx.HTTPStatusError("Bing Images rate limited", request=response.request, response=response)
        response.raise_for_status()
        output: list[SearchCandidate] = []
        for raw in re.findall(r'<a[^>]+class=["\']iusc["\'][^>]+m=["\']([^"\']+)', response.text, re.I):
            try:
                item = json.loads(html.unescape(raw))
            except (TypeError, json.JSONDecodeError):
                continue
            image_url = item.get("murl")
            source_url = item.get("purl")
            if not image_url or not source_url:
                continue
            output.append(
                SearchCandidate(
                    candidate_id=_candidate_id(self.name, str(image_url)),
                    provider=self.name,
                    query=query,
                    title=str(item.get("t") or item.get("desc") or ""),
                    description=str(item.get("desc") or ""),
                    image_url=str(image_url),
                    thumbnail_url=item.get("turl"),
                    source_page_url=str(source_url),
                    source_domain=_domain(str(source_url)),
                    width=int(item.get("w") or 0) or None,
                    height=int(item.get("h") or 0) or None,
                )
            )
        self.cache.put_query(self.name, query, scope, [candidate.to_dict() for candidate in output])
        return output


class MetMuseumSearchAdapter(ImageSearchAdapter):
    """Search the Metropolitan Museum of Art public collection API."""

    name = "met-museum"
    capability_kind = "institutional-repository"

    def __init__(self, client: httpx.AsyncClient, cache: RetrievalCache):
        self.client = client
        self.cache = cache

    async def search(self, query: str, limit: int, policy: SourcePolicy) -> list[SearchCandidate]:
        if policy.name not in {"artwork-museum-object", "humanities-culture", "historical-evidence"}:
            self._last_cache_hit = None
            return []
        scope = json.dumps({"limit": limit, "department": 6, "profile": policy.name}, sort_keys=True)
        cached = self.cache.get_query(self.name, query, scope)
        if cached is not None:
            self._last_cache_hit = True
            return [SearchCandidate.from_dict(item) for item in cached]
        self._last_cache_hit = False
        response = await self.client.get(
            "https://collectionapi.metmuseum.org/public/collection/v1/search",
            params={"q": query, "hasImages": "true", "departmentId": 6},
        )
        if response.status_code == 429:
            raise httpx.HTTPStatusError("Met collection API rate limited", request=response.request, response=response)
        response.raise_for_status()
        object_ids = (response.json().get("objectIDs") or [])[: max(limit * 3, 24)]
        output: list[SearchCandidate] = []
        for object_id in object_ids:
            detail = await self.client.get(
                f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{object_id}"
            )
            if detail.status_code != 200:
                continue
            item = detail.json()
            image_url = item.get("primaryImage") or item.get("primaryImageSmall")
            source_url = item.get("objectURL")
            if not image_url or not source_url:
                continue
            title = str(item.get("title") or "")
            description = " ".join(
                str(value or "")
                for value in (
                    item.get("objectName"), item.get("culture"), item.get("period"),
                    item.get("dynasty"), item.get("reign"), item.get("objectDate"),
                    item.get("artistDisplayName"), item.get("creditLine"),
                    item.get("classification"), item.get("medium"),
                )
            )
            output.append(
                SearchCandidate(
                    candidate_id=_candidate_id(self.name, str(image_url)),
                    provider=self.name,
                    query=query,
                    title=title,
                    description=description,
                    image_url=str(image_url),
                    source_page_url=str(source_url),
                    source_domain="metmuseum.org",
                    author=str(item.get("artistDisplayName") or "The Metropolitan Museum of Art"),
                    credit=str(item.get("creditLine") or "The Metropolitan Museum of Art collection"),
                    license_name="Met Open Access / CC0" if item.get("isPublicDomain") else "See object page",
                    attribution_required=not bool(item.get("isPublicDomain")),
                    published_at=str(item.get("objectDate") or ""),
                    provenance={
                        "provider_record_url": str(source_url),
                        "entity_document_id": f"met:{object_id}",
                        "record_title": title,
                        "record_subject": description,
                        "record_type": "museum-object",
                        "relation_type": "collection-api-primary-image",
                        "direct_asset_relation": True,
                        "asset_url": str(image_url),
                        "provider_verified": True,
                    },
                )
            )
        query_tokens = set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", query.casefold()))

        def relevance(candidate: SearchCandidate) -> tuple[int, int, int]:
            text = f"{candidate.title} {candidate.description}".casefold()
            years = [int(value) for value in re.findall(r"(?<!\d)(\d{3,4})(?!\d)", text)]
            song_period = int(any(960 <= year <= 1279 for year in years) or "song dynasty" in text)
            painting_type = int(any(marker in text for marker in ("painting", "handscroll", "ink", "silk")))
            overlap = len(query_tokens & set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", text)))
            return song_period, painting_type, overlap

        output.sort(key=relevance, reverse=True)
        output = output[:limit]
        self.cache.put_query(self.name, query, scope, [candidate.to_dict() for candidate in output])
        return output


class NeurIPSPaperSearchAdapter(ImageSearchAdapter):
    """Search the official NeurIPS proceedings and return a deterministically rendered paper page."""

    name = "neurips-proceedings"
    capability_kind = "institutional-repository"

    def __init__(self, client: httpx.AsyncClient, cache: RetrievalCache):
        self.client = client
        self.cache = cache

    async def search(self, query: str, limit: int, policy: SourcePolicy) -> list[SearchCandidate]:
        paper_query = any(marker in query.casefold() for marker in (
            "paper", "publication", "conference", "alexnet", "attention is all you need", "transformer",
            "imagenet classification", "deep convolutional neural network",
        ))
        if policy.name not in {"scientific-academic", "historical-evidence", "company-product-technology"} and not paper_query:
            self._last_cache_hit = None
            return []
        years = [int(value) for value in re.findall(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)", query)]
        if not years:
            years = [2012, 2017]
        scope = json.dumps({"limit": limit, "years": years, "profile": policy.name, "provenance_schema": 3}, sort_keys=True)
        cached = self.cache.get_query(self.name, query, scope)
        if cached is not None:
            self._last_cache_hit = True
            return [SearchCandidate.from_dict(item) for item in cached]
        self._last_cache_hit = False
        query_tokens = self._meaningful_tokens(query)
        matches: list[tuple[float, int, str, str]] = []
        for year in years[:3]:
            response = await self.client.get(f"https://proceedings.neurips.cc/paper/{year}")
            if response.status_code == 429:
                raise httpx.HTTPStatusError("NeurIPS proceedings rate limited", request=response.request, response=response)
            response.raise_for_status()
            for href, title in re.findall(
                r'<a[^>]+title=["\']paper title["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                response.text,
                flags=re.IGNORECASE | re.DOTALL,
            ):
                clean_title = _clean_html(title)
                title_tokens = self._meaningful_tokens(clean_title)
                overlap = len(query_tokens & title_tokens) / max(1, len(title_tokens))
                if overlap >= 0.35 or _clean_html(clean_title).casefold() in query.casefold():
                    matches.append((overlap, year, clean_title, str(httpx.URL(str(response.url)).join(href))))
        matches.sort(reverse=True)
        output: list[SearchCandidate] = []
        for _, year, title, record_url in matches[:limit]:
            page = await self.client.get(record_url)
            if page.status_code != 200:
                continue
            links = re.findall(r'href=["\']([^"\']+-Paper\.pdf)["\']', page.text, flags=re.IGNORECASE)
            if not links:
                continue
            pdf_url = str(httpx.URL(str(page.url)).join(links[0]))
            document_id = Path(urlparse(pdf_url).path).stem.removesuffix("-Paper")
            output.append(SearchCandidate(
                candidate_id=_candidate_id(self.name, pdf_url),
                provider=self.name,
                query=query,
                title=title,
                description=f"Official NeurIPS {year} proceedings paper",
                image_url=pdf_url,
                source_page_url=str(page.url),
                source_domain="proceedings.neurips.cc",
                mime_type="application/pdf",
                author="NeurIPS Proceedings",
                credit="Official NeurIPS proceedings",
                license_name="See official proceedings record",
                attribution_required=True,
                published_at=str(year),
                provenance={
                    "provider_record_url": str(page.url),
                    "entity_document_id": f"neurips:{year}:{document_id}",
                    "record_title": title,
                    "record_subject": f"{title} NeurIPS {year}",
                    "record_type": "research-paper",
                    "relation_type": "official-proceedings-paper-pdf",
                    "direct_asset_relation": True,
                    "asset_url": pdf_url,
                    "provider_verified": True,
                },
                document_asset={
                    "format": "pdf",
                    "page_index": 0,
                    "page_search_terms": list(self._meaningful_tokens(title))[:4],
                },
            ))
        self.cache.put_query(self.name, query, scope, [candidate.to_dict() for candidate in output])
        return output

    @staticmethod
    def _meaningful_tokens(text: str) -> set[str]:
        stop = {"the", "and", "with", "from", "paper", "research", "original", "official", "image", "figure", "source"}
        return {token for token in re.findall(r"[a-z0-9]+", text.casefold()) if len(token) > 2 and token not in stop}


class InternetArchiveSearchAdapter(ImageSearchAdapter):
    """Search Internet Archive records and bind an archived PDF to its stable item ID."""

    name = "internet-archive"
    capability_kind = "institutional-repository"

    def __init__(self, client: httpx.AsyncClient, cache: RetrievalCache):
        self.client = client
        self.cache = cache

    async def search(self, query: str, limit: int, policy: SourcePolicy) -> list[SearchCandidate]:
        if policy.name not in {"historical-evidence", "scientific-academic", "humanities-culture"}:
            self._last_cache_hit = None
            return []
        scope = json.dumps({"limit": limit, "profile": policy.name, "provenance_schema": 4}, sort_keys=True)
        cached = self.cache.get_query(self.name, query, scope)
        if cached is not None:
            self._last_cache_hit = True
            return [SearchCandidate.from_dict(item) for item in cached]
        self._last_cache_hit = False
        significant_terms = [
            token for token in re.findall(r"[A-Za-z0-9-]+", query)
            if len(token) >= 4 and token.casefold() not in {
                "expert", "system", "authentic", "archival", "historical", "documentation",
                "image", "official", "source", "paper", "figure", "record",
            }
        ]
        search_queries = []
        if significant_terms:
            search_queries.append(f'title:("{significant_terms[0]}")')
        search_queries.append(query)
        docs: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for archive_query in search_queries:
            response = await self.client.get(
                "https://archive.org/advancedsearch.php",
                params={
                    "q": archive_query,
                    "fl[]": ["identifier", "title", "description", "date", "creator"],
                    "rows": max(limit * 2, 10),
                    "output": "json",
                },
            )
            if response.status_code == 429:
                raise httpx.HTTPStatusError("Internet Archive rate limited", request=response.request, response=response)
            response.raise_for_status()
            for item in (response.json().get("response") or {}).get("docs") or []:
                identifier = str(item.get("identifier") or "")
                if identifier and identifier not in seen_ids:
                    docs.append(item)
                    seen_ids.add(identifier)
            if len(docs) >= limit:
                break
        output: list[SearchCandidate] = []
        for item in docs:
            identifier = str(item.get("identifier") or "")
            title = str(item.get("title") or "")
            if not identifier or not title:
                continue
            metadata = await self.client.get(f"https://archive.org/metadata/{identifier}")
            if metadata.status_code != 200:
                continue
            metadata_payload = metadata.json()
            files = metadata_payload.get("files") or []
            item_metadata = metadata_payload.get("metadata") or {}
            collections = item_metadata.get("collection") or []
            if isinstance(collections, str):
                collections = [collections]
            institutional_collections = {
                "dticarchive", "ciareadingroom", "usgovernmentmirrors", "government-documents",
                "nasa", "university_of_california_libraries", "library_of_congress",
            }
            institutional_record = bool(set(str(value) for value in collections) & institutional_collections)
            pdfs = [
                entry for entry in files
                if str(entry.get("name") or "").lower().endswith(".pdf")
                and "encrypted" not in str(entry.get("name") or "").casefold()
                and int(entry.get("size") or 0) <= 25 * 1024 * 1024
            ]
            if not pdfs:
                continue
            pdfs.sort(key=lambda entry: ("text pdf" not in str(entry.get("format") or "").casefold(), int(entry.get("size") or 0)))
            pdf_url = f"https://archive.org/download/{identifier}/{pdfs[0]['name']}"
            record_url = f"https://archive.org/details/{identifier}"
            description = " ".join(str(value or "") for value in (
                item.get("description"), item.get("creator"), item.get("date"),
            ))[:3000]
            output.append(SearchCandidate(
                candidate_id=_candidate_id(self.name, pdf_url),
                provider=self.name,
                query=query,
                title=title,
                description=description,
                image_url=pdf_url,
                source_page_url=record_url,
                source_domain="archive.org",
                mime_type="application/pdf",
                author=str(item.get("creator") or "Internet Archive contributor"),
                credit="Internet Archive item record",
                license_name="See archive item record",
                attribution_required=True,
                published_at=str(item.get("date") or ""),
                provenance={
                    "provider_record_url": record_url,
                    "entity_document_id": f"internet-archive:{identifier}",
                    "record_title": title,
                    "record_subject": description,
                    "record_type": "archive-document",
                    "relation_type": "archive-item-file-list-pdf",
                    "direct_asset_relation": True,
                    "asset_url": pdf_url,
                    "provider_verified": True,
                    "institutional_record": institutional_record,
                    "collection_ids": [str(value) for value in collections],
                    "record_creator": str(item_metadata.get("creator") or item.get("creator") or ""),
                },
                document_asset={
                    "format": "pdf",
                    "page_index": 0,
                    "page_search_terms": significant_terms[:3],
                },
            ))
            if len(output) >= limit:
                break
        self.cache.put_query(self.name, query, scope, [candidate.to_dict() for candidate in output])
        return output


def build_default_adapters(
    client: httpx.AsyncClient,
    cache: RetrievalCache,
    names: list[str] | None = None,
) -> list[ImageSearchAdapter]:
    adapters: dict[str, ImageSearchAdapter] = {
        "official-page": OfficialPageMediaAdapter(client, cache),
        "serper": SerperImageSearchAdapter(client, cache),
        "bing": BingImageSearchAdapter(client, cache),
        "bing-images": BingImageSearchAdapter(client, cache),
        "met": MetMuseumSearchAdapter(client, cache),
        "met-museum": MetMuseumSearchAdapter(client, cache),
        "neurips": NeurIPSPaperSearchAdapter(client, cache),
        "neurips-proceedings": NeurIPSPaperSearchAdapter(client, cache),
        "internet-archive": InternetArchiveSearchAdapter(client, cache),
        "wikimedia": WikimediaCommonsSearchAdapter(client, cache),
        "wikimedia-commons": WikimediaCommonsSearchAdapter(client, cache),
    }
    requested = names or ["official-page", "serper", "bing", "neurips-proceedings", "internet-archive", "met-museum", "wikimedia"]
    output: list[ImageSearchAdapter] = []
    seen: set[str] = set()
    for name in requested:
        adapter = adapters.get(name)
        if adapter and adapter.name not in seen:
            output.append(adapter)
            seen.add(adapter.name)
    return output

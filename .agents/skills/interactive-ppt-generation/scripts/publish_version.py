#!/usr/bin/env python3
"""
Interactive PPT Generation - Publish Version

Publish a converted PPTX as an immutable project version and write verification manifests.

Usage:
    python scripts/publish_version.py <project_path> <source_pptx> [options]

Examples:
    python scripts/publish_version.py projects/demo build/editable.pptx --changed-pages 11

Dependencies:
    None (only uses the standard library)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from console_encoding import configure_utf8_stdio


configure_utf8_stdio()


VERSION_RE = re.compile(r"^V(?P<number>\d+)_")
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_pages(value: str) -> list[int]:
    if not value.strip():
        return []
    pages: set[int] = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start < 1 or end < start:
                raise ValueError(f"Invalid page range: {token}")
            pages.update(range(start, end + 1))
        else:
            page = int(token)
            if page < 1:
                raise ValueError(f"Invalid page number: {token}")
            pages.add(page)
    return sorted(pages)


def slide_count(path: Path) -> int:
    if not zipfile.is_zipfile(path):
        raise ValueError(f"Not a valid PPTX ZIP package: {path}")
    pattern = re.compile(r"^ppt/slides/slide\d+\.xml$")
    with zipfile.ZipFile(path) as archive:
        return sum(1 for name in archive.namelist() if pattern.match(name))


def repair_windows_argument(value: str) -> str:
    """Repair a Chinese CLI argument decoded as Latin-1 by a legacy shell."""
    if any("\u4e00" <= char <= "\u9fff" for char in value):
        return value
    for encoding in ("utf-8", "gbk"):
        try:
            candidate = value.encode("latin-1").decode(encoding)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if any("\u4e00" <= char <= "\u9fff" for char in candidate):
            return candidate
    return value


def safe_title(value: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("_", repair_windows_argument(value)).strip(" ._")
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:80] or "presentation"


def next_version(exports_dir: Path) -> int:
    versions = []
    for path in exports_dir.glob("V*_*.pptx"):
        match = VERSION_RE.match(path.name)
        if match:
            versions.append(int(match.group("number")))
    return max(versions, default=0) + 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy a temporary PPTX to a unique versioned filename and write verification manifests."
    )
    parser.add_argument("project_path", type=Path, help="Project workspace containing exports/")
    parser.add_argument("source_pptx", type=Path, help="Temporary PPTX produced by the converter")
    parser.add_argument("--title", help="Filename title; defaults to the source filename stem")
    parser.add_argument(
        "--changed-pages",
        default="",
        help="Comma-separated pages or ranges, for example 11 or 3,7-9",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        project_path = args.project_path.expanduser().resolve()
        source_pptx = args.source_pptx.expanduser().resolve()

        if not project_path.is_dir():
            raise FileNotFoundError(f"Project directory does not exist: {project_path}")
        if not source_pptx.is_file() or source_pptx.suffix.lower() != ".pptx":
            raise FileNotFoundError(f"PPTX source does not exist: {source_pptx}")

        changed_pages = parse_pages(args.changed_pages)
        count = slide_count(source_pptx)
        if count < 1:
            raise ValueError("PPTX contains no slides")
        if changed_pages and changed_pages[-1] > count:
            raise ValueError(
                f"Changed page {changed_pages[-1]} exceeds PPTX slide count {count}"
            )

        exports_dir = project_path / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        version = next_version(exports_dir)
        timestamp = datetime.now().astimezone()
        title = safe_title(args.title or source_pptx.stem)
        filename = f"V{version:03d}_{title}_{timestamp:%Y%m%d_%H%M%S}.pptx"
        destination = exports_dir / filename
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite existing export: {destination}")

        source_hash = sha256(source_pptx)
        shutil.copy2(source_pptx, destination)
        destination_hash = sha256(destination)
        if destination_hash != source_hash:
            destination.unlink(missing_ok=True)
            raise OSError("Published PPTX hash does not match source PPTX")

        receipt = {
            "schema_version": 1,
            "version": f"V{version:03d}",
            "filename": filename,
            "path": str(destination.resolve()),
            "sha256": destination_hash,
            "bytes": destination.stat().st_size,
            "slide_count": count,
            "changed_pages": changed_pages,
            "exported_at": timestamp.isoformat(timespec="seconds"),
            "source_pptx": str(source_pptx),
            "source_sha256": source_hash,
        }

        latest_path = exports_dir / "latest.json"
        latest_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        history_path = exports_dir / "publish_history.jsonl"
        with history_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False) + "\n")

        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    except (FileNotFoundError, FileExistsError, ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

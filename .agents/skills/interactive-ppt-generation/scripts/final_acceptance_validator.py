#!/usr/bin/env python3
"""
Interactive PPT Generation - Final PPTX Acceptance Validator

Validate the immutable PPTX selected by exports/latest.json, render every
slide on Windows, inspect package/media/notes consistency, and write one
machine-readable final acceptance report.

Usage:
    python scripts/final_acceptance_validator.py <project_path>

Dependencies:
    Microsoft PowerPoint on Windows, Pillow, and the standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from PIL import Image, ImageChops, ImageStat

from console_encoding import configure_utf8_stdio
from svg_to_pptx.pptx_package.notes import markdown_to_plain_text
from visual_assets.asset_manifest import AssetManifest


_DML = "http://schemas.openxmlformats.org/drawingml/2006/main"


class AcceptanceError(RuntimeError):
    """Reject a final PPTX that cannot be trusted for delivery."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    """Replace a text file without exposing a partially written projection."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _difference_hash_bytes(data: bytes) -> str | None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            resized = image.convert("L").resize((9, 8))
            if hasattr(resized, "get_flattened_data"):
                pixels = list(resized.get_flattened_data())
            else:
                pixels = list(resized.getdata())
    except (OSError, ValueError):
        return None
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value = (value << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    return f"{value:016x}"


def _hash_distance(left: str | None, right: str | None) -> int:
    if not left or not right:
        return 65
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _slide_number(path: str) -> int:
    match = re.search(r"(?:slide|幻灯片)(\d+)(?:\.xml|\.png)?$", path, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _resolve_part(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    base = PurePosixPath(source_part).parent
    parts: list[str] = []
    for part in (base / target).parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part not in {".", ""}:
            parts.append(part)
    return "/".join(parts)


def _relationship_source(rels_part: str) -> str:
    """Return the owner part for a package relationship part."""
    path = PurePosixPath(rels_part)
    if rels_part == "_rels/.rels":
        return ""
    if path.parent.name != "_rels" or not path.name.endswith(".rels"):
        return ""
    return str(path.parent.parent / path.name.removesuffix(".rels"))


def _relationships(archive: zipfile.ZipFile, part: str) -> list[dict[str, str]]:
    path = PurePosixPath(part)
    rels = str(path.parent / "_rels" / f"{path.name}.rels")
    if rels not in archive.namelist():
        return []
    root = ET.fromstring(archive.read(rels))
    return [{key.rsplit("}", 1)[-1]: value for key, value in item.attrib.items()} for item in root]


def _extract_notes_text(archive: zipfile.ZipFile, slide_parts: list[str]) -> dict[int, str]:
    notes: dict[int, str] = {}
    for index, slide_part in enumerate(slide_parts, start=1):
        note_target = None
        for relationship in _relationships(archive, slide_part):
            if relationship.get("Type", "").endswith("/notesSlide"):
                note_target = _resolve_part(slide_part, relationship.get("Target", ""))
                break
        if not note_target or note_target not in archive.namelist():
            continue
        root = ET.fromstring(archive.read(note_target))
        text = "\n".join(
            (element.text or "").strip()
            for element in root.iter(f"{{{_DML}}}t")
            if (element.text or "").strip()
        )
        if text:
            notes[index] = text
    return notes


def _canonical_notes(project: Path, expected_slides: int) -> dict[int, str]:
    """Read the only speaker-notes truth and require one substantive section per page."""
    path = project / "narrative" / "speaker-notes.md"
    if not path.is_file():
        raise AcceptanceError(f"missing canonical speaker notes: {path}")
    content = path.read_text(encoding="utf-8-sig")
    matches = list(re.finditer(r"(?m)^## P(?P<number>\d+)[ \t]*$", content))
    sections: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        section = markdown_to_plain_text(content[match.end():end].strip())
        if section:
            sections[int(match.group("number"))] = section
    missing = [page for page in range(1, expected_slides + 1) if not sections.get(page)]
    extras = sorted(set(sections) - set(range(1, expected_slides + 1)))
    if missing or extras:
        raise AcceptanceError(
            f"canonical speaker notes page roster mismatch: missing={missing}, extras={extras}"
        )
    return sections


def _normalize_notes(text: str) -> str:
    return " ".join(text.split())


def _audit_package(pptx: Path, expected_slides: int) -> dict[str, Any]:
    if not zipfile.is_zipfile(pptx):
        raise AcceptanceError(f"not a valid PPTX ZIP package: {pptx}")
    with zipfile.ZipFile(pptx) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise AcceptanceError(f"corrupt PPTX member: {bad_member}")
        names = set(archive.namelist())
        required_parts = {
            "[Content_Types].xml",
            "_rels/.rels",
            "ppt/presentation.xml",
            "ppt/_rels/presentation.xml.rels",
        }
        missing_core = sorted(required_parts - names)
        if missing_core:
            raise AcceptanceError("missing core PPTX parts: " + ", ".join(missing_core))
        try:
            ET.fromstring(archive.read("[Content_Types].xml"))
            ET.fromstring(archive.read("ppt/presentation.xml"))
        except ET.ParseError as error:
            raise AcceptanceError(f"invalid core PPTX XML: {error}") from error
        broken_package_relationships: list[str] = []
        for rels_part in sorted(name for name in names if name.endswith(".rels")):
            source = _relationship_source(rels_part)
            try:
                relationships = ET.fromstring(archive.read(rels_part))
            except ET.ParseError as error:
                broken_package_relationships.append(f"invalid relationships XML {rels_part}: {error}")
                continue
            for relationship in relationships:
                attributes = {
                    key.rsplit("}", 1)[-1]: value for key, value in relationship.attrib.items()
                }
                if attributes.get("TargetMode", "").casefold() == "external":
                    continue
                target = _resolve_part(source, attributes.get("Target", ""))
                if target and target not in names:
                    broken_package_relationships.append(
                        f"missing relationship target from {rels_part}: {target}"
                    )
        if broken_package_relationships:
            raise AcceptanceError("; ".join(broken_package_relationships))
        slide_parts = sorted(
            (name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=_slide_number,
        )
        if len(slide_parts) != expected_slides:
            raise AcceptanceError(
                f"slide count mismatch: PPTX={len(slide_parts)}, expected={expected_slides}"
            )
        missing_targets: list[str] = []
        image_parts: set[str] = set()
        slide_image_parts: dict[str, list[str]] = {}
        for slide_index, slide_part in enumerate(slide_parts, start=1):
            current_slide_images: set[str] = set()
            for relationship in _relationships(archive, slide_part):
                if relationship.get("TargetMode", "").casefold() == "external":
                    if relationship.get("Type", "").endswith("/image"):
                        missing_targets.append(
                            f"external image relationship in {slide_part}: {relationship.get('Target')}"
                        )
                    continue
                target = _resolve_part(slide_part, relationship.get("Target", ""))
                if target not in names:
                    missing_targets.append(f"missing relationship target: {target}")
                if relationship.get("Type", "").endswith("/image"):
                    image_parts.add(target)
                    current_slide_images.add(target)
            slide_image_parts[str(slide_index)] = sorted(current_slide_images)
        if missing_targets:
            raise AcceptanceError("; ".join(missing_targets))
        notes = _extract_notes_text(archive, slide_parts)
        missing_notes = [page for page in range(1, expected_slides + 1) if page not in notes]
        if missing_notes:
            raise AcceptanceError(
                "speaker notes are not embedded for pages: " + ", ".join(map(str, missing_notes))
            )
        return {
            "zip_integrity": "passed",
            "slide_count": len(slide_parts),
            "notes_count": len(notes),
            "image_part_count": len(image_parts),
            "slide_image_parts": slide_image_parts,
            "notes_text": notes,
        }


def _audit_manifest(project: Path, package: dict[str, Any]) -> dict[str, Any]:
    path = project / "ppt-content" / "visuals" / "asset-manifest.json"
    if not path.is_file():
        raise AcceptanceError(f"missing asset manifest: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise AcceptanceError(f"invalid asset manifest: {error}") from error
    readiness = AssetManifest(project).validation_report()
    if not readiness["stage7_ready"]:
        errors = [
            *readiness["schema_errors"],
            *readiness["file_errors"],
            *readiness["completion_errors"],
        ]
        raise AcceptanceError("asset manifest is not Stage 7 ready: " + "; ".join(errors))
    errors: list[str] = []
    expected_assets: dict[str, tuple[int, str, str | None]] = {}
    for item in payload.get("items", []):
        local = project / str(item.get("local_path") or "")
        if not local.is_file():
            errors.append(f"manifest asset is missing: {local}")
            continue
        expected_hash = item.get("content_sha256")
        if expected_hash and expected_hash != _sha256(local):
            errors.append(f"manifest hash mismatch: {local}")
        try:
            slide_number = int(item.get("slide_number"))
        except (TypeError, ValueError):
            errors.append(f"manifest asset {item.get('slot_id')} lacks a valid slide_number")
            continue
        expected_assets[str(item.get("slot_id") or local.name)] = (
            slide_number,
            expected_hash or _sha256(local),
            _difference_hash_bytes(local.read_bytes()),
        )
        if item.get("source_type") == "web":
            for field in ("source_page_url", "original_image_url", "source_domain", "search_query"):
                if not item.get(field):
                    errors.append(f"manifest web asset {item.get('slot_id')} lacks {field}")
    if errors:
        raise AcceptanceError("; ".join(errors))
    if payload.get("items") and package["image_part_count"] < 1:
        raise AcceptanceError("manifest selects images but final PPTX contains no image media")
    pptx = Path(str(package["pptx_path"]))
    with zipfile.ZipFile(pptx) as archive:
        media_hashes = {
            name: (
                hashlib.sha256(archive.read(name)).hexdigest(),
                _difference_hash_bytes(archive.read(name)),
            )
            for name in archive.namelist()
            if name.startswith("ppt/media/")
        }
    unmatched = []
    for slot_id, (slide_number, exact_hash, visual_hash) in expected_assets.items():
        referenced_parts = package["slide_image_parts"].get(str(slide_number), [])
        if not any(
            part in media_hashes
            and (
                exact_hash == media_hashes[part][0]
                or _hash_distance(visual_hash, media_hashes[part][1]) <= 6
            )
            for part in referenced_parts
        ):
            unmatched.append(f"{slot_id}@P{slide_number:02d}")
    if unmatched:
        raise AcceptanceError(
            "manifest assets are not represented in final PPTX media: " + ", ".join(unmatched)
        )
    return {
        "path": str(path.resolve()),
        "selected_items": len(payload.get("items", [])),
        "source_records": len(payload.get("items", [])),
        "files_valid": True,
        "embedded_assets_verified": len(expected_assets),
        "slide_correspondence_verified": True,
        "stage7_ready": True,
    }


def _powerpoint_path() -> Path | None:
    configured = os.environ.get("POWERPOINT_EXE")
    candidates = [
        Path(configured) if configured else None,
        Path(r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE"),
        Path(r"C:\Program Files (x86)\Microsoft Office\root\Office16\POWERPNT.EXE"),
    ]
    return next((path for path in candidates if path and path.is_file()), None)


def _render_with_powerpoint(
    pptx: Path, output_dir: Path, timeout_seconds: float
) -> tuple[list[Path], dict[str, Any]]:
    executable = _powerpoint_path()
    if executable is None:
        raise AcceptanceError(
            "Microsoft PowerPoint renderer is unavailable; set POWERPOINT_EXE to POWERPNT.EXE"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ppt-acceptance-") as temp_name:
        script = Path(temp_name) / "render.ps1"
        qa_path = Path(temp_name) / "powerpoint-qa.json"
        script.write_text(
            """
param([string]$Pptx,[string]$Output,[string]$Qa)
$ErrorActionPreference='Stop'
$app=$null
$presentation=$null
try {
  $app=New-Object -ComObject PowerPoint.Application
  $presentation=$app.Presentations.Open($Pptx,$true,$true,$false)
  $issues=@()
  $slideWidth=[double]$presentation.PageSetup.SlideWidth
  $slideHeight=[double]$presentation.PageSetup.SlideHeight
  foreach($slide in $presentation.Slides) {
    foreach($shape in $slide.Shapes) {
      $left=[double]$shape.Left
      $top=[double]$shape.Top
      $width=[double]$shape.Width
      $height=[double]$shape.Height
      if($width -le 0 -or $height -le 0) {
        $issues += @{slide=[int]$slide.SlideIndex; shape=$shape.Name; kind='zero-size'}
      }
      if($left -lt -1 -or $top -lt -1 -or ($left+$width) -gt ($slideWidth+1) -or ($top+$height) -gt ($slideHeight+1)) {
        $issues += @{slide=[int]$slide.SlideIndex; shape=$shape.Name; kind='outside-slide-bounds'}
      }
      try {
        if($shape.HasTextFrame -and $shape.TextFrame2.HasText) {
          $boundHeight=[double]$shape.TextFrame2.TextRange.BoundHeight
          $boundWidth=[double]$shape.TextFrame2.TextRange.BoundWidth
          if($boundHeight -gt ($height+2)) {
            $issues += @{slide=[int]$slide.SlideIndex; shape=$shape.Name; kind='text-overflow-height'; bound=$boundHeight; frame=$height}
          }
          if($shape.TextFrame2.WordWrap -eq 0 -and $boundWidth -gt ($width+2)) {
            $issues += @{slide=[int]$slide.SlideIndex; shape=$shape.Name; kind='text-overflow-width'; bound=$boundWidth; frame=$width}
          }
        }
      } catch {}
    }
  }
  @{slide_width=$slideWidth; slide_height=$slideHeight; issues=$issues} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Qa -Encoding UTF8
  $presentation.Export($Output,'PNG',1600,900)
} finally {
  if ($presentation -ne $null) { $presentation.Close() }
  if ($app -ne $null) { $app.Quit() }
}
""".lstrip(),
            encoding="utf-8",
        )
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Pptx",
            str(pptx),
            "-Output",
            str(output_dir),
            "-Qa",
            str(qa_path),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            raise AcceptanceError(f"PowerPoint rendering timed out after {timeout_seconds}s") from error
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            raise AcceptanceError(f"PowerPoint rendering failed: {details}")
        try:
            office_qa = json.loads(qa_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            raise AcceptanceError(f"PowerPoint QA report is unreadable: {error}") from error
    images = sorted(output_dir.glob("*.PNG"), key=lambda path: _slide_number(path.name))
    if not images:
        images = sorted(output_dir.glob("*.png"), key=lambda path: _slide_number(path.name))
    if not images:
        raise AcceptanceError("PowerPoint rendering produced no slide images")
    issues = office_qa.get("issues") or []
    if issues:
        summary = "; ".join(
            f"slide {item.get('slide')} {item.get('shape')}: {item.get('kind')}"
            for item in issues[:20]
        )
        raise AcceptanceError(f"PowerPoint layout QA failed: {summary}")
    return images, office_qa


def _visual_checks(images: list[Path], expected_slides: int) -> dict[str, Any]:
    if len(images) != expected_slides:
        raise AcceptanceError(
            f"rendered slide count mismatch: rendered={len(images)}, expected={expected_slides}"
        )
    failures: list[str] = []
    slide_reports = []
    for index, path in enumerate(images, start=1):
        try:
            with Image.open(path) as image:
                rgb = image.convert("RGB")
                if rgb.width < 640 or rgb.height < 360:
                    failures.append(f"slide {index} rendered too small: {rgb.size}")
                extrema = ImageStat.Stat(rgb).extrema
                dynamic_range = max(high - low for low, high in extrema)
                if dynamic_range < 2:
                    failures.append(f"slide {index} is visually blank or uniform")
                edge = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
                difference = ImageChops.difference(rgb, edge)
                bbox = difference.getbbox()
                slide_reports.append(
                    {
                        "slide": index,
                        "path": str(path.resolve()),
                        "size": [rgb.width, rgb.height],
                        "dynamic_range": dynamic_range,
                        "content_bbox": list(bbox) if bbox else None,
                    }
                )
        except (OSError, ValueError) as error:
            failures.append(f"slide {index} render is unreadable: {error}")
    if failures:
        raise AcceptanceError("; ".join(failures))
    return {"rendered_slides": len(images), "slides": slide_reports, "obvious_anomalies": []}


def _record_final_layout_pass(project: Path, page_count: int) -> dict[str, Any]:
    """Project successful machine checks into the final-PPTX review column."""
    path = project / "production" / "layout-review.md"
    if not path.is_file():
        raise AcceptanceError(f"missing layout review: {path}")
    text = path.read_text(encoding="utf-8-sig")
    matches = list(re.finditer(r"(?m)^## P(?P<number>\d+)[ \t]*$", text))
    expected = list(range(1, page_count + 1))
    actual = [int(match.group("number")) for match in matches]
    if actual != expected:
        raise AcceptanceError(
            f"layout review page roster mismatch: actual={actual}, expected={expected}"
        )
    output: list[str] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if index == 0:
            output.append(text[:start])
        section = text[start:end]
        lines: list[str] = []
        data_rows = 0
        table_row_index = 0
        for line in section.splitlines():
            if line.strip().startswith("|") and line.count("|") >= 5:
                cells = line.split("|")
                separator = bool(cells[1].strip()) and set(cells[1].strip()) <= {"-", ":"}
                if table_row_index >= 2 and not separator:
                    cells[-2] = " passed "
                    line = "|".join(cells)
                    data_rows += 1
                table_row_index += 1
            if re.match(r"^\s*-\s*(?:状态|status)\s*[：:]", line, re.IGNORECASE):
                line = re.sub(r"(?i)(passed|pending|failed)", "passed", line)
            lines.append(line)
        if data_rows < 1:
            raise AcceptanceError(f"layout review P{actual[index]:02d} has no check rows")
        output.append("\n".join(lines))
        if end < len(text):
            output.append("\n")
    _atomic_write_text(path, "".join(output).rstrip() + "\n")
    return {"path": str(path.resolve()), "final_pptx_passed_pages": expected}


def validate_project(
    project: Path,
    *,
    render_timeout: float = 120.0,
) -> dict[str, Any]:
    """Validate the final published deck and return a report payload."""
    project = project.expanduser().resolve()
    latest_path = project / "exports" / "latest.json"
    if not latest_path.is_file():
        raise AcceptanceError(f"missing final pointer: {latest_path}")
    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise AcceptanceError(f"invalid latest.json: {error}") from error
    pptx = Path(str(latest.get("path") or ""))
    if not pptx.is_absolute():
        pptx = project / pptx
    if not pptx.is_file():
        raise AcceptanceError(f"latest PPTX does not exist: {pptx}")
    actual_hash = _sha256(pptx)
    if latest.get("sha256") != actual_hash:
        raise AcceptanceError("latest PPTX SHA-256 does not match latest.json")
    expected_slides = int(latest.get("slide_count") or 0)
    if expected_slides < 1:
        raise AcceptanceError("latest.json slide_count must be positive")
    started = time.monotonic()
    package = _audit_package(pptx, expected_slides)
    canonical_notes = _canonical_notes(project, expected_slides)
    mismatched_notes = [
        page
        for page in range(1, expected_slides + 1)
        if _normalize_notes(package["notes_text"].get(page, ""))
        != _normalize_notes(canonical_notes[page])
    ]
    if mismatched_notes:
        raise AcceptanceError(
            "embedded speaker notes do not match canonical pages: "
            + ", ".join(f"P{page:02d}" for page in mismatched_notes)
        )
    package["canonical_notes_verified"] = expected_slides
    package["pptx_path"] = str(pptx)
    manifest = _audit_manifest(project, package)
    package.pop("pptx_path", None)
    render_dir = project / "validation" / "final-render"
    if render_dir.exists():
        validation_root = (project / "validation").resolve()
        if validation_root not in render_dir.resolve().parents:
            raise AcceptanceError(f"unsafe render cleanup target: {render_dir}")
        shutil.rmtree(render_dir)
    images, office_qa = _render_with_powerpoint(pptx, render_dir, render_timeout)
    visual = _visual_checks(images, expected_slides)
    visual["powerpoint_shape_qa"] = office_qa
    layout = _record_final_layout_pass(project, expected_slides)
    return {
        "schema": "interactive-ppt-generation.final-acceptance.v1",
        "status": "passed",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "pptx": {
            "path": str(pptx.resolve()),
            "sha256": actual_hash,
            "bytes": pptx.stat().st_size,
            "slide_count": expected_slides,
        },
        "package": package,
        "manifest": manifest,
        "render": visual,
        "layout_review": layout,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the final PPTX selected by exports/latest.json.")
    parser.add_argument("project_path", type=Path)
    parser.add_argument("--render-timeout", type=float, default=120.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    project = args.project_path.expanduser().resolve()
    report_path = project / "validation" / "final-acceptance-report.json"
    try:
        report = validate_project(
            project,
            render_timeout=args.render_timeout,
        )
        exit_code = 0
    except (AcceptanceError, OSError, ValueError) as error:
        report = {
            "schema": "interactive-ppt-generation.final-acceptance.v1",
            "status": "failed",
            "error": str(error),
        }
        exit_code = 2
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

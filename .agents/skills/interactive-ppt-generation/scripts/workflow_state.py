#!/usr/bin/env python3
"""
Interactive PPT Generation - Workflow State Controller

Validate stage artifacts, close gates atomically, detect stale state, and find
the last genuinely completed gate for resume.

Dependencies:
    PyYAML and the bundled Visual Asset modules.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from visual_assets.asset_manifest import AssetManifest
from stage8_contract import validate_stage8_contract


GATE_ORDER = (
    "template_route",
    "presentation_brief",
    "text_plan",
    "content_final",
    "user_assets",
    "speaker_notes",
    "visual_assets",
    "production_plan",
    "layout_review",
    "svg",
    "export",
    "final_validation",
)
BACKEND_REQUIRED_GATES = GATE_ORDER[:10]
_PAGE_HEADING_RE = re.compile(r"(?m)^## P(?P<number>\d+)[ \t]*$")
_CHECKED_RE = re.compile(r"(?im)^\s*-\s*\[[xX]\]")
_STATUS_CONFIRMED_RE = re.compile(r"(?im)(?:状态|status)\s*[：:]\s*(?:已确认|confirmed)")
_STATUS_PASSED_RE = re.compile(r"(?im)(?:状态|status)\s*[：:]\s*passed")


@dataclass(slots=True)
class ArtifactCheck:
    ok: bool
    files: list[Path]
    errors: list[str]
    logical_values: dict[str, Any]


class WorkflowStateError(RuntimeError):
    """Reject an invalid transition or an unready workflow."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            yaml.safe_dump(payload, stream, allow_unicode=True, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _page_sections(path: Path) -> dict[int, str]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    matches = list(_PAGE_HEADING_RE.finditer(text))
    sections: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[int(match.group("number"))] = text[match.end():end].strip()
    return sections


def _substantive(text: str) -> bool:
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|", "- [ ]")):
            continue
        if re.fullmatch(r"[-:| ]+", stripped):
            continue
        if re.fullmatch(r"[-*]\s*[^：:]+[：:]?\s*", stripped):
            continue
        lines.append(stripped)
    return len(" ".join(lines)) >= 8


class WorkflowStateController:
    """Own workflow stage validation and atomic project-state transitions."""

    def __init__(self, project_path: Path):
        self.project = project_path.expanduser().resolve()
        self.path = self.project / "project-state.yaml"

    def ensure_schema(self) -> dict[str, Any]:
        """Upgrade a project state to the current schema without closing gates."""
        state = self._load()
        legacy_schema = state.get("schema_version") != 2
        changed = False
        if state.get("schema_version") != 2:
            state["schema_version"] = 2
            changed = True
        state.setdefault("template_route", "pending")
        state.setdefault("page_count", None)
        gates = state.setdefault("gates", {})
        for gate in GATE_ORDER:
            if gate not in gates:
                gates[gate] = "pending"
                changed = True
        if "gate_artifacts" not in state:
            state["gate_artifacts"] = {}
            changed = True
        normalized_stage = self._derived_current_stage(state)
        current = str(state.get("current_stage") or "")
        aliases = {"brief": "presentation_brief"}
        current = aliases.get(current, current)
        if current != normalized_stage:
            state["current_stage"] = normalized_stage
            changed = True
        state.setdefault("changed_pages", [])
        if legacy_schema:
            for gate in GATE_ORDER:
                if gates.get(gate) == "confirmed":
                    gates[gate] = "stale"
            state["current_stage"] = GATE_ORDER[0]
            changed = True
        if changed:
            state["updated_at"] = _now()
            _atomic_write_yaml(self.path, state)
        return state

    def close_gate(self, gate: str, *, template_route: str | None = None) -> dict[str, Any]:
        """Validate and atomically close exactly one legal next gate."""
        if gate not in GATE_ORDER:
            raise WorkflowStateError(f"Unknown workflow gate: {gate}")
        state = self.ensure_schema()
        if gate == "template_route" and template_route is not None:
            if template_route not in {"reference", "free-design"}:
                raise WorkflowStateError("template route must be reference or free-design")
            state["template_route"] = template_route

        gate_index = GATE_ORDER.index(gate)
        inspection = self.inspect(state_override=state)
        prior_errors = []
        for prior in GATE_ORDER[:gate_index]:
            report = inspection["gates"][prior]
            if report["status"] != "confirmed" or not report["artifact_current"]:
                prior_errors.append(
                    f"{prior}: status={report['status']}, artifact_current={report['artifact_current']}"
                )
        if prior_errors:
            raise WorkflowStateError(
                "Cannot skip unclosed or stale prerequisite gates: " + "; ".join(prior_errors)
            )

        check = self._validate_artifact(gate, state)
        if not check.ok:
            raise WorkflowStateError(
                f"Cannot close {gate}; artifact validation failed: " + "; ".join(check.errors)
            )
        fingerprint = self._fingerprint(check)
        state["gates"][gate] = "confirmed"
        state["gate_artifacts"][gate] = {
            **fingerprint,
            "closed_at": _now(),
        }
        for downstream in GATE_ORDER[gate_index + 1:]:
            state["gates"][downstream] = "pending"
            state["gate_artifacts"].pop(downstream, None)
        state["current_stage"] = self._derived_current_stage(state)
        state["updated_at"] = _now()
        _atomic_write_yaml(self.path, state)
        return self.inspect()

    def record_user_assets(self, mode: str) -> dict[str, Any]:
        """Record the explicit user-assets decision from project-local files."""
        if mode not in {"none", "scan"}:
            raise WorkflowStateError("user asset mode must be none or scan")
        root = self.project / "ppt-content" / "visuals"
        files = sorted(
            path for path in root.glob("slide-*/user/*") if path.is_file()
        )
        if mode == "none" and files:
            raise WorkflowStateError(
                "user asset mode is none but files exist under slide-*/user/"
            )
        if mode == "scan" and not files:
            raise WorkflowStateError(
                "user asset mode is scan but no files exist under slide-*/user/"
            )
        payload = {
            "schema_version": 1,
            "status": "none" if mode == "none" else "provided",
            "recorded_at": _now(),
            "files": [
                {"path": self._relative(path), "sha256": _sha256(path)}
                for path in files
            ],
        }
        path = root / "user-assets.json"
        _atomic_write_json(path, payload)
        return payload

    def inspect(self, *, state_override: dict[str, Any] | None = None) -> dict[str, Any]:
        """Compare every declared gate with its current owning artifacts."""
        state = state_override or self._load()
        gates = state.get("gates") or {}
        recorded = state.get("gate_artifacts") or {}
        reports: dict[str, Any] = {}
        chain_intact = True
        last_completed: str | None = None
        for gate in GATE_ORDER:
            status = str(gates.get(gate) or "pending")
            check = self._validate_artifact(gate, state)
            current = self._fingerprint(check) if check.ok else None
            previous = recorded.get(gate) if isinstance(recorded.get(gate), dict) else None
            fingerprint_match = bool(
                current
                and previous
                and previous.get("fingerprint") == current.get("fingerprint")
            )
            artifact_current = check.ok and fingerprint_match
            if status == "confirmed" and chain_intact and artifact_current:
                last_completed = gate
            else:
                chain_intact = False
            reports[gate] = {
                "status": status,
                "artifact_valid": check.ok,
                "fingerprint_recorded": previous is not None,
                "fingerprint_match": fingerprint_match,
                "artifact_current": artifact_current,
                "errors": check.errors,
                "files": [self._relative(path) for path in check.files],
            }
        expected_stage = self._next_after(last_completed)
        declared_stage = str(state.get("current_stage") or "")
        return {
            "project": str(self.project),
            "schema_version": state.get("schema_version"),
            "declared_stage": declared_stage,
            "expected_stage": expected_stage,
            "state_stale": declared_stage != expected_stage or any(
                report["status"] == "confirmed" and not report["artifact_current"]
                for report in reports.values()
            ),
            "last_completed_gate": last_completed,
            "next_stage": expected_stage,
            "gates": reports,
        }

    def resume(self) -> dict[str, Any]:
        """Reconcile stale declarations and return the last true resume point."""
        state = self.ensure_schema()
        report = self.inspect(state_override=state)
        last = report["last_completed_gate"]
        boundary = GATE_ORDER.index(last) + 1 if last else 0
        for index, gate in enumerate(GATE_ORDER):
            gate_report = report["gates"][gate]
            if index < boundary:
                state["gates"][gate] = "confirmed"
                continue
            if gate_report["status"] == "confirmed" and not gate_report["artifact_current"]:
                state["gates"][gate] = "stale"
            else:
                state["gates"][gate] = "pending"
            if index > boundary:
                state["gate_artifacts"].pop(gate, None)
        state["current_stage"] = report["next_stage"]
        state["updated_at"] = _now()
        _atomic_write_yaml(self.path, state)
        return self.inspect()

    def assert_ready_for_backend(self) -> dict[str, Any]:
        """Reject backend entry unless all upstream gates and SVGs are current."""
        report = self.inspect()
        failures = []
        for gate in BACKEND_REQUIRED_GATES:
            item = report["gates"][gate]
            if item["status"] != "confirmed" or not item["artifact_current"]:
                failures.append(
                    f"{gate}: status={item['status']}, artifact_current={item['artifact_current']}"
                )
        if failures:
            raise WorkflowStateError(
                "Backend entry denied; close and validate every upstream gate: "
                + "; ".join(failures)
            )
        return report

    def _load(self) -> dict[str, Any]:
        if not self.project.is_dir():
            raise WorkflowStateError(f"Project directory does not exist: {self.project}")
        if not self.path.is_file():
            raise WorkflowStateError(f"Missing project state: {self.path}")
        try:
            payload = yaml.safe_load(self.path.read_text(encoding="utf-8-sig")) or {}
        except (OSError, yaml.YAMLError) as error:
            raise WorkflowStateError(f"Unreadable project state: {error}") from error
        if not isinstance(payload, dict):
            raise WorkflowStateError("project-state.yaml must contain a mapping")
        return payload

    def _validate_artifact(self, gate: str, state: dict[str, Any]) -> ArtifactCheck:
        page_count = state.get("page_count")
        page_count = int(page_count) if isinstance(page_count, int) and page_count > 0 else 0
        if gate == "template_route":
            route = str(state.get("template_route") or "pending")
            errors = [] if route in {"reference", "free-design"} else ["template_route is unresolved"]
            return ArtifactCheck(not errors, [], errors, {"template_route": route})
        if gate == "presentation_brief":
            path = self.project / "narrative" / "presentation-brief.md"
            errors = self._require_files([path])
            text = path.read_text(encoding="utf-8-sig") if not errors else ""
            if text and not _STATUS_CONFIRMED_RE.search(text):
                errors.append("presentation brief confirmation record is not confirmed")
            return ArtifactCheck(not errors, [path], errors, {})
        if gate in {"text_plan", "content_final"}:
            files = [
                self.project / "narrative" / "presentation-structure.md",
                self.project / "narrative" / "slide-intent.md",
            ]
            errors = self._require_files(files)
            if not errors:
                if page_count < 1:
                    errors.append("project-state.page_count must be a positive integer")
                structure = files[0].read_text(encoding="utf-8-sig")
                if not _STATUS_CONFIRMED_RE.search(structure):
                    errors.append("presentation structure confirmation record is not confirmed")
                errors.extend(self._validate_pages(files[1], page_count, "slide intent"))
            return ArtifactCheck(not errors, files, errors, {"page_count": page_count})
        if gate == "user_assets":
            root = self.project / "ppt-content" / "visuals"
            decision = root / "user-assets.json"
            files = [decision]
            errors = self._require_files(files)
            logical: dict[str, Any] = {}
            if not errors:
                try:
                    payload = json.loads(decision.read_text(encoding="utf-8-sig"))
                    status = str(payload.get("status") or "")
                    if status not in {"none", "provided"}:
                        errors.append("user-assets.json status must be none or provided")
                    actual = sorted(
                        path for path in root.glob("slide-*/user/*") if path.is_file()
                    )
                    declared = {
                        str(item.get("path") or ""): str(item.get("sha256") or "")
                        for item in payload.get("files", [])
                    }
                    current = {self._relative(path): _sha256(path) for path in actual}
                    if status == "none" and current:
                        errors.append("user assets exist but decision status is none")
                    if status == "provided" and not current:
                        errors.append("user assets status is provided but no files exist")
                    if declared != current:
                        errors.append("user-assets.json file roster/hash is stale")
                    files.extend(actual)
                    logical = {"status": status, "file_count": len(actual)}
                except (OSError, json.JSONDecodeError) as error:
                    errors.append(f"user-assets.json is invalid: {error}")
            return ArtifactCheck(not errors, files, errors, logical)
        if gate == "speaker_notes":
            files = [
                self.project / "research" / "research-notes.md",
                self.project / "narrative" / "speaker-notes.md",
            ]
            errors = self._require_files(files)
            if not errors:
                errors.extend(self._validate_pages(files[1], page_count, "speaker notes"))
            return ArtifactCheck(not errors, files, errors, {"page_count": page_count})
        if gate == "visual_assets":
            requirements = self.project / "research" / "visual-assets" / "visual-requirements.json"
            manifest_path = self.project / "ppt-content" / "visuals" / "asset-manifest.json"
            files = [requirements, manifest_path]
            errors = self._require_files(files)
            if not errors:
                try:
                    json.loads(requirements.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError) as error:
                    errors.append(f"visual requirements are invalid JSON: {error}")
                manifest_report = AssetManifest(self.project).validation_report()
                if not manifest_report["stage7_ready"]:
                    errors.extend(
                        [
                            *manifest_report["schema_errors"],
                            *manifest_report["file_errors"],
                            *manifest_report["completion_errors"],
                        ]
                    )
                for item in AssetManifest(self.project).payload.get("items", []):
                    local_path = item.get("local_path")
                    if local_path:
                        files.append(self.project / str(local_path))
            return ArtifactCheck(not errors, files, errors, {})
        if gate == "production_plan":
            files = [
                self.project / "production" / "slide-production-plan.md",
                self.project / "ppt-content" / "text" / "slide-copy.md",
                self.project / "ppt-content" / "design" / "design-system.md",
                self.project / "ppt-content" / "design" / "page-layouts.md",
                self.project / "spec_lock.md",
                self.project / "ppt-content" / "visuals" / "asset-manifest.json",
            ]
            errors = self._require_files(files)
            if not errors:
                errors.extend(self._validate_pages(files[0], page_count, "production plan"))
                errors.extend(self._validate_pages(files[1], page_count, "slide copy"))
                checked = len(_CHECKED_RE.findall(files[0].read_text(encoding="utf-8-sig")))
                if checked < page_count * 4:
                    errors.append(
                        f"production plan has {checked} checked completion items; expected at least {page_count * 4}"
                    )
                if not _substantive(files[2].read_text(encoding="utf-8-sig")):
                    errors.append("design-system.md is still empty")
                if not _substantive(files[3].read_text(encoding="utf-8-sig")):
                    errors.append("page-layouts.md is still empty")
                stage8 = validate_stage8_contract(self.project)
                errors.extend(stage8["errors"])
            return ArtifactCheck(not errors, files, errors, {"page_count": page_count})
        if gate == "layout_review":
            path = self.project / "production" / "layout-review.md"
            errors = self._require_files([path])
            logical: dict[str, Any] = {"pre_export": {}}
            if not errors:
                errors.extend(self._validate_pages(path, page_count, "layout review"))
                sections = _page_sections(path)
                for page in range(1, page_count + 1):
                    rows = [
                        line for line in sections.get(page, "").splitlines()
                        if line.strip().startswith("|")
                    ]
                    page_values: list[list[str]] = []
                    for row in rows[2:]:
                        cells = [cell.strip().casefold() for cell in row.split("|")[1:-1]]
                        if len(cells) < 4:
                            continue
                        pre_export = cells[1:3]
                        page_values.append(pre_export)
                        if any(value not in {"passed", "not-applicable"} for value in pre_export):
                            errors.append(
                                f"layout review P{page:02d} pre-export columns are not passed"
                            )
                    if not page_values:
                        errors.append(f"layout review P{page:02d} has no review rows")
                    logical["pre_export"][f"P{page:02d}"] = page_values
            return ArtifactCheck(not errors, [], errors, logical)
        if gate == "svg":
            expected = [self.project / "svg_output" / f"P{page:02d}.svg" for page in range(1, page_count + 1)]
            errors = self._require_files(expected)
            actual = sorted((self.project / "svg_output").glob("*.svg"))
            if len(actual) != page_count:
                errors.append(f"svg_output contains {len(actual)} SVG files; expected {page_count}")
            return ArtifactCheck(not errors, expected, errors, {"page_count": page_count})
        if gate == "export":
            latest = self.project / "exports" / "latest.json"
            files = [latest]
            errors = self._require_files(files)
            logical: dict[str, Any] = {}
            if not errors:
                try:
                    receipt = json.loads(latest.read_text(encoding="utf-8-sig"))
                    pptx = Path(str(receipt.get("path") or ""))
                    if not pptx.is_absolute():
                        pptx = self.project / pptx
                    if not pptx.is_file():
                        errors.append(f"latest PPTX does not exist: {pptx}")
                    else:
                        files.append(pptx)
                        actual_hash = _sha256(pptx)
                        if receipt.get("sha256") != actual_hash:
                            errors.append("latest PPTX SHA-256 does not match latest.json")
                        if int(receipt.get("slide_count") or 0) != page_count:
                            errors.append("latest PPTX slide_count does not match project page_count")
                        logical["latest_sha256"] = actual_hash
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    errors.append(f"latest.json is invalid: {error}")
            return ArtifactCheck(not errors, files, errors, logical)
        if gate == "final_validation":
            report_path = self.project / "validation" / "final-acceptance-report.json"
            render_dir = self.project / "validation" / "final-render"
            files = [report_path]
            errors = self._require_files(files)
            logical: dict[str, Any] = {}
            if not errors:
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
                    if report.get("status") != "passed":
                        errors.append("final acceptance report status is not passed")
                    latest = self._validate_artifact("export", state)
                    errors.extend(latest.errors)
                    expected_hash = latest.logical_values.get("latest_sha256")
                    if report.get("pptx", {}).get("sha256") != expected_hash:
                        errors.append("final acceptance report does not match exports/latest.json")
                    if not report.get("manifest", {}).get("stage7_ready"):
                        errors.append("final acceptance report did not verify a Stage 7-ready manifest")
                    if int(report.get("package", {}).get("canonical_notes_verified") or 0) != int(
                        state.get("page_count") or 0
                    ):
                        errors.append("final acceptance report did not verify all canonical speaker notes")
                    if not report.get("manifest", {}).get("slide_correspondence_verified"):
                        errors.append("final acceptance report did not verify per-slide manifest correspondence")
                    if report.get("render", {}).get("obvious_anomalies"):
                        errors.append("final acceptance report contains visual anomalies")
                    if report.get("render", {}).get("powerpoint_shape_qa", {}).get("issues"):
                        errors.append("final acceptance report contains PowerPoint shape QA issues")
                    rendered = sorted(
                        {path.resolve() for path in render_dir.iterdir() if path.is_file() and path.suffix.casefold() == ".png"}
                    ) if render_dir.is_dir() else []
                    expected_pages = int(state.get("page_count") or 0)
                    if len(rendered) != expected_pages:
                        errors.append(
                            f"final render contains {len(rendered)} pages; expected {expected_pages}"
                        )
                    files.extend(rendered)
                    logical["latest_sha256"] = expected_hash
                except (OSError, json.JSONDecodeError) as error:
                    errors.append(f"final acceptance report is invalid: {error}")
            return ArtifactCheck(not errors, files, errors, logical)
        raise WorkflowStateError(f"No artifact validator for gate: {gate}")

    @staticmethod
    def _require_files(paths: list[Path]) -> list[str]:
        return [f"required artifact is missing: {path}" for path in paths if not path.is_file()]

    @staticmethod
    def _validate_pages(path: Path, page_count: int, label: str) -> list[str]:
        if page_count < 1:
            return ["project-state.page_count must be a positive integer"]
        sections = _page_sections(path)
        expected = set(range(1, page_count + 1))
        errors: list[str] = []
        if set(sections) != expected:
            errors.append(
                f"{label} page keys are {sorted(sections)}; expected {sorted(expected)}"
            )
        for page in sorted(expected & set(sections)):
            if not _substantive(sections[page]):
                errors.append(f"{label} P{page:02d} has no substantive content")
        return errors

    def _fingerprint(self, check: ArtifactCheck) -> dict[str, Any]:
        digest = hashlib.sha256()
        file_rows = []
        for path in sorted(set(check.files), key=lambda item: self._relative(item)):
            if not path.is_file():
                continue
            relative = self._relative(path)
            value = _sha256(path)
            file_rows.append({"path": relative, "sha256": value})
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(value.encode("ascii"))
            digest.update(b"\n")
        logical = json.dumps(check.logical_values, ensure_ascii=False, sort_keys=True)
        digest.update(logical.encode("utf-8"))
        return {
            "fingerprint": digest.hexdigest(),
            "files": file_rows,
            "logical_values": check.logical_values,
        }

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.project).as_posix()
        except ValueError:
            return str(path.resolve())

    @staticmethod
    def _next_after(last_completed: str | None) -> str:
        if last_completed is None:
            return GATE_ORDER[0]
        index = GATE_ORDER.index(last_completed) + 1
        return GATE_ORDER[index] if index < len(GATE_ORDER) else "completed"

    @staticmethod
    def _derived_current_stage(state: dict[str, Any]) -> str:
        gates = state.get("gates") or {}
        for gate in GATE_ORDER:
            if gates.get(gate) != "confirmed":
                return gate
        return "completed"

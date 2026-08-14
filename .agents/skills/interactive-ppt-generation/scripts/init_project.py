#!/usr/bin/env python3
"""
Interactive PPT Generation - Project Initializer

Create the canonical project workspace without overwriting existing content.
See references/project-workspace.md for directory ownership and file formats.

Usage:
    python scripts/init_project.py <project_path> [--slides N]

Examples:
    python scripts/init_project.py projects/demo
    python scripts/init_project.py projects/demo --slides 15

Dependencies:
    None (only uses standard library)
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from console_encoding import configure_utf8_stdio
from workflow_state import WorkflowStateController


configure_utf8_stdio()

_SKILL_DIR = Path(__file__).resolve().parent.parent
_SKELETON_DIR = _SKILL_DIR / "assets" / "project-skeleton"
_PAGE_HEADING_RE = re.compile(r"(?m)^## P(?P<number>\d+)[ \t]*$")
_PAGE_COUNT_RE = re.compile(r"(?m)^page_count:[ \t]*(?:null|\d+)[ \t]*$")
_STATE_GATE_FIELDS = (
    "speaker_notes",
    "visual_assets",
    "layout_review",
    "svg",
    "export",
    "final_validation",
)

_PROJECT_DIRECTORIES = (
    "inputs/other-sources",
    "research/sources",
    "research/visual-assets/candidates",
    "research/visual-assets/retrieval-state",
    "narrative",
    "ppt-content/text",
    "ppt-content/visuals/assets",
    "ppt-content/design/backgrounds",
    "ppt-content/design/shared-assets",
    ".cache/visual-assets/queries",
    ".cache/visual-assets/downloads",
    "production",
    "svg_output",
    "svg_final",
    "validation",
    "exports",
)

_PAGE_DIRECTORIES = ("user", "ai", "vectors")

_PAGE_FILES = {
    "narrative/slide-intent.md": """

## P{page:02d}

### 页面目的

### 核心观点

### 讲述内容

### 页面衔接
""",
    "narrative/speaker-notes.md": """

## P{page:02d}

### 开场句

### 讲述正文

### 证据引用

### 与画面关系

### 页面衔接
""",
    "ppt-content/text/slide-copy.md": """

## P{page:02d}

### 标题

### 文字对象

| 内容键 | 类型 | 文字 | 预计行数 | 最大行数 |
|---|---|---|---:|---:|

### 图注

### 页脚来源
""",
    "production/slide-production-plan.md": """

## P{page:02d}

### 页面依据

- 页面目的：`narrative/slide-intent.md#P{page:02d}`
- 布局键：

### 文字引用

- 上屏文字：`ppt-content/text/slide-copy.md#P{page:02d}`

### 文字对象

| 内容键 | x | y | w | h | 字号角色 | 预计/最大行数 | 对齐 | 邻接与安全间距 |
|---|---:|---:|---:|---:|---|---|---|---|

### 视觉素材

| 元素 | 本地路径 | 用途 | 裁剪方式 |
|---|---|---|---|

### Stage 7 素材决策

| 素材 ID | 验证风险 | 验证方法 | 证据强度 | 署名模式 | 显示署名 | 位置 | 字号角色 |
|---|---|---|---|---|---|---|---|

### 元素坐标

| 元素 | x | y | w | h | 样式说明 |
|---|---:|---:|---:|---:|---|

### 完成状态

- [ ] 文字引用可解析
- [ ] 视觉素材可解析
- [ ] 布局与坐标已锁定
- [ ] 施工图图文碰撞检查通过
""",
    "production/layout-review.md": """

## P{page:02d}

| 检查项 | 施工图 | SVG 预览 | 最终 PPTX |
|---|---|---|---|
| 文字未越界 | pending | pending | pending |
| 图文无非预期碰撞 | pending | pending | pending |
| 段落与层级清楚 | pending | pending | pending |
| 转换后换行正常 | not-applicable | not-applicable | pending |

### 问题与修复

### 结论

- status: pending
""",
}


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("slides must be a positive integer")
    return number


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or extend an Interactive PPT project workspace."
    )
    parser.add_argument("project_path", type=Path)
    parser.add_argument(
        "--slides",
        type=_positive_int,
        help="Create per-slide records and asset directories for pages 1..N",
    )
    return parser


def _copy_missing_skeleton_files(project: Path) -> list[Path]:
    created: list[Path] = []
    for source in _SKELETON_DIR.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(_SKELETON_DIR)
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            continue
        shutil.copy2(source, destination)
        created.append(destination)
    return created


def _append_missing_page_sections(project: Path, page_count: int) -> list[Path]:
    changed: list[Path] = []
    for relative, template in _PAGE_FILES.items():
        path = project / relative
        text = path.read_text(encoding="utf-8")
        existing = {int(match.group("number")) for match in _PAGE_HEADING_RE.finditer(text)}
        additions = [template.format(page=page) for page in range(1, page_count + 1) if page not in existing]
        if not additions:
            continue
        path.write_text(text.rstrip() + "".join(additions) + "\n", encoding="utf-8", newline="\n")
        changed.append(path)
    return changed


def _create_page_directories(project: Path, page_count: int) -> list[Path]:
    created: list[Path] = []
    for page in range(1, page_count + 1):
        page_root = project / "ppt-content" / "visuals" / f"slide-{page:02d}"
        for name in _PAGE_DIRECTORIES:
            directory = page_root / name
            if directory.exists():
                continue
            directory.mkdir(parents=True, exist_ok=True)
            created.append(directory)
    return created


def _update_page_count(project: Path, page_count: int) -> Path | None:
    """Update only the initializer-owned page_count field."""
    path = project / "project-state.yaml"
    text = path.read_text(encoding="utf-8")
    replacement = f"page_count: {page_count}"
    updated, count = _PAGE_COUNT_RE.subn(replacement, text, count=1)
    if count != 1:
        raise ValueError(f"project-state.yaml has no single page_count field: {path}")
    if updated == text:
        return None
    path.write_text(updated, encoding="utf-8", newline="\n")
    return path


def _ensure_state_gate_fields(project: Path) -> Path | None:
    """Append initializer-owned gate keys without changing existing values."""
    path = project / "project-state.yaml"
    text = path.read_text(encoding="utf-8")
    missing = [
        field
        for field in _STATE_GATE_FIELDS
        if re.search(rf"(?m)^  {re.escape(field)}:[ \t]*", text) is None
    ]
    if not missing:
        return None
    lines = text.splitlines()
    try:
        gates_index = lines.index("gates:")
    except ValueError as error:
        raise ValueError(f"project-state.yaml has no gates mapping: {path}") from error
    insert_at = len(lines)
    for index in range(gates_index + 1, len(lines)):
        line = lines[index]
        if line and not line.startswith((" ", "\t")):
            insert_at = index
            break
    lines[insert_at:insert_at] = [f"  {field}: pending" for field in missing]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _existing_page_count(project: Path) -> int:
    """Return the highest initialized page number without changing the project."""
    pages: set[int] = set()
    for relative in _PAGE_FILES:
        path = project / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        pages.update(int(match.group("number")) for match in _PAGE_HEADING_RE.finditer(text))
    visuals = project / "ppt-content" / "visuals"
    if visuals.is_dir():
        for directory in visuals.glob("slide-[0-9][0-9]"):
            try:
                pages.add(int(directory.name.removeprefix("slide-")))
            except ValueError:
                continue
    return max(pages, default=0)


def initialize_project(project: Path, page_count: int | None = None) -> tuple[list[Path], list[Path]]:
    """Create missing workspace files and optionally extend page-owned records."""
    project = project.expanduser().resolve()
    if project.exists() and not project.is_dir():
        raise ValueError(f"Project path is not a directory: {project}")
    project.mkdir(parents=True, exist_ok=True)
    for relative in _PROJECT_DIRECTORIES:
        (project / relative).mkdir(parents=True, exist_ok=True)

    created = _copy_missing_skeleton_files(project)
    changed: list[Path] = []
    state_path = _ensure_state_gate_fields(project)
    if state_path is not None:
        changed.append(state_path)
    if page_count is not None:
        existing_count = _existing_page_count(project)
        if page_count < existing_count:
            raise ValueError(
                f"Refusing to shrink initialized pages from {existing_count} to {page_count}; "
                "revise the narrative and remove obsolete page-owned records explicitly"
            )
        created.extend(_create_page_directories(project, page_count))
        changed.extend(_append_missing_page_sections(project, page_count))
        page_state_path = _update_page_count(project, page_count)
        if page_state_path is not None and page_state_path not in changed:
            changed.append(page_state_path)
    controller = WorkflowStateController(project)
    controller.ensure_schema()
    return created, changed


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        project = args.project_path.expanduser().resolve()
        created, changed = initialize_project(project, args.slides)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    print(f"[PROJECT] {project}")
    print(f"[CREATED] {len(created)}")
    print(f"[EXTENDED] {len(changed)}")
    print("[SPEC_LOCK] create after the design system is approved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

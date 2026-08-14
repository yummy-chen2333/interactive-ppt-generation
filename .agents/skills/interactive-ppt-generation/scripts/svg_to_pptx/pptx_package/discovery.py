"""Find SVG and notes files in a project directory."""

from __future__ import annotations

import re
from pathlib import Path


def find_svg_files(
    project_path: Path,
    source: str = 'output',
) -> tuple[list[Path], str]:
    """Find SVG files in the project.

    Args:
        project_path: Project directory path.
        source: SVG source directory alias or name.
            - 'output': svg_output (hand-authored source; native default)
            - 'final': svg_final (post-processed preview; diagnostic input)
            - or any subdirectory name

    Returns:
        (list_of_svg_files, actual_directory_name) tuple.
    """
    dir_map = {
        'output': 'svg_output',
        'final': 'svg_final',
    }

    dir_name = dir_map.get(source, source)
    svg_dir = project_path / dir_name

    if not svg_dir.exists():
        print(f"  Warning: {dir_name} directory does not exist, trying svg_output")
        dir_name = 'svg_output'
        svg_dir = project_path / dir_name

    if not svg_dir.exists():
        if project_path.is_dir():
            svg_dir = project_path
            dir_name = project_path.name
        else:
            return [], ''

    return sorted(svg_dir.glob('*.svg')), dir_name


def find_notes_files(
    project_path: Path,
    svg_files: list[Path] | None = None,
) -> dict[str, str]:
    """Map canonical narrative/speaker-notes.md sections to SVG files.

    The single source of truth is narrative/speaker-notes.md. Sections use
    ``## P01`` keys and are matched by slide number, independent of SVG stem.

    Args:
        project_path: Project directory path.
        svg_files: SVG file list (for filename matching).

    Returns:
        Dict mapping SVG filename stem to notes content.
    """
    canonical_path = project_path / 'narrative' / 'speaker-notes.md'
    notes: dict[str, str] = {}
    if not canonical_path.is_file() or not svg_files:
        return notes
    try:
        content = canonical_path.read_text(encoding='utf-8-sig')
    except OSError:
        return notes
    matches = list(re.finditer(r'(?m)^## P(?P<number>\d+)[ \t]*$', content))
    sections: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        section = content[match.end():end].strip()
        if section:
            sections[int(match.group('number'))] = section
    for index, svg_path in enumerate(svg_files, start=1):
        if sections.get(index):
            notes[svg_path.stem] = sections[index]

    return notes

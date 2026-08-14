# AGENTS.md

This repository contains one presentation-generation workflow.

Before any PPT planning, SVG authoring, PPTX generation, or repository modification, read:

```text
.agents/skills/interactive-ppt-generation/SKILL.md
```

Then follow its required references and user-confirmation gates in order.
For every concrete deck project, read
`.agents/skills/interactive-ppt-generation/references/project-workspace.md`
and keep narrative, screen content, production instructions, and derived output
in their declared owner paths.

Do not invoke another PPT Skill or a second template/planning workflow. The
bundled backend under `.agents/skills/interactive-ppt-generation/scripts/`
owns SVG validation, finalization, SVG-to-DrawingML/PPTX conversion, and
immutable version publication.

Install runtime dependencies from the repository root with:

```text
python -m pip install -r requirements.txt
```

Initialize each project with:

```text
python .agents/skills/interactive-ppt-generation/scripts/init_project.py projects/<name>
```

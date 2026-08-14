# Third-Party Notices

The SVG quality-check, SVG finalization, and SVG-to-DrawingML/PPTX backend in
`scripts/` is adapted from PPT Master at commit
`9f4f9098e3d5d78ce856b39fb8d440c63f90a2b5`:

- Project: `hugohe3/ppt-master`
- License: MIT
- Copyright: 2025-2026 Hugo He

The complete MIT license is retained in [`LICENSE`](./LICENSE).

The preset-shape data under `scripts/pptx_shapes/data/` retains its own notice
and license files, including the Open XML SDK MIT notice and Apache-2.0 text.

This project independently owns its interaction gates, content planning,
reference-PPT learning, asset workflow, version publication, and page-revision
procedure. It reuses the third-party backend only after project SVG pages have
been authored.

The built-in Visual Asset Retrieval subsystem was informed by public MIT-licensed
SenseNova skill implementations at commit
`61b60bb36857e20d490ccada80863d8886b551d3`:

- `sn-search-image`: structured Serper result fields (`imageUrl`, `thumbnailUrl`,
  source page, title, and domain).
- `sn-ppt-standard`: bounded stage execution, JSON stage outputs, progress events,
  resumable state, and isolated concurrent task outcomes.
- `sn-image-base`: provider abstraction for pixel-aware visual analysis.

Those skills are not runtime dependencies and are never invoked by this Skill.
Their useful ideas were rewritten into local Python modules with independent
timeouts, retries, cache, circuit breakers, validation, policy profiles, ranking,
and manifest ownership. The SenseNova repository retains its MIT license and
copyright notices.

# Interactive PPT Generation Skill

一个可由 Coding Agent 执行的交互式 PPT 生成 Skill：先通过多轮确认完成内容规划，形成有研究依据的逐页讲稿，再用讲稿驱动图片搜索、上屏文案与逐页排版，最后生成规范 SVG 并转换为原生可编辑 DrawingML/PPTX。

本仓库不要求使用者额外安装或调用 PPT Master Skill。SVG 检查、预处理和 SVG→DrawingML/PPTX 后端已经放在本仓库的 Skill 中。

## 安装

需要 Python 3.10 或更高版本。

```text
git clone <your-repository-url>
cd interactive-ppt-generation
python -m pip install -r requirements.txt
```

Windows 中若 `python` 命令不可用，改用 `py -3`，例如 `py -3 -m pip install -r requirements.txt`。

在支持仓库级 Skill 的 Coding Agent 中打开本仓库。Skill 位于：

```text
.agents/skills/interactive-ppt-generation/
```

## 使用

向 Coding Agent 提出 PPT 生成请求。Agent 必须读取 `SKILL.md`，按 `references/workflow.md` 完成用户确认，并使用 `references/project-workspace.md` 保存每个阶段的实际内容。

主题与受众确认后初始化项目：

```text
python .agents/skills/interactive-ppt-generation/scripts/init_project.py \
  projects/<project-name>
```

页数确认后补齐逐页记录和素材目录：

```text
python .agents/skills/interactive-ppt-generation/scripts/init_project.py \
  projects/<project-name> \
  --slides 15
```

初始化工具不覆盖已有内容。项目结构分为：

```text
narrative/       演讲逻辑、板块、逐页目的和完整讲稿
research/        事实来源、图片搜索词、候选和选择记录
ppt-content/     最终上屏文字、视觉素材、背景和页面结构
production/      逐页组装施工图和三阶段布局验收
spec_lock.md     设计定稿后生成的后端技术契约
svg_output/      按施工图生成的逐页 SVG
```

内容方案确认后，Agent 先读取讲稿与图片检索规范，再把真实图片位写入 `research/visual-assets/visual-requirements.json`。随后固定调用内置 Visual Asset Pipeline：

```text
python .agents/skills/interactive-ppt-generation/scripts/visual_asset_cli.py retrieve \
  projects/<project-name>

python .agents/skills/interactive-ppt-generation/scripts/visual_asset_cli.py validate-manifest \
  projects/<project-name>
```

Pipeline 根据 deck theme、slide topic 和 slot purpose 动态选择 source policy，完成结构化检索、限时下载、验证、分析、排序、缓存、熔断、best-so-far 与 good-enough early stop。`ppt-content/visuals/asset-manifest.json` 是唯一机器真相。无需、也不得在运行时调用第三方图片 Skill。

编写施工图与 SVG 时执行文字溢出和图文碰撞验收，导出阶段按需读取四份后端规范。

已有项目完成 `spec_lock.md` 与 `svg_output/` 后，可以直接运行：

```text
python .agents/skills/interactive-ppt-generation/scripts/run_backend.py \
  examples/minimal-project \
  --title minimal-demo \
  --changed-pages 1 \
  --format ppt169
```

最终文件路径记录在：

```text
examples/minimal-project/exports/latest.json
```

每次导出使用新版本号和新文件名，不覆盖上一版。

## 核心目录

```text
.agents/skills/interactive-ppt-generation/
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── workflow.md
│   ├── project-workspace.md
│   ├── speaker-notes-image-search.md
│   ├── source-policy-profiles.yaml
│   ├── layout-review.md
│   ├── svg-authoring.md
│   ├── svg-quality-checker.md
│   ├── svg-finalization.md
│   ├── svg-to-drawingml.md
│   └── pptx-export.md
├── scripts/
│   ├── init_project.py
│   ├── visual_asset_cli.py
│   ├── visual_assets/
│   ├── run_backend.py
│   ├── svg_quality_checker.py
│   ├── finalize_svg.py
│   ├── svg_to_pptx.py
│   ├── publish_version.py
│   └── 转换器依赖模块
├── assets/
│   └── project-skeleton/
├── requirements.txt
├── LICENSE
└── THIRD_PARTY_NOTICES.md
```

## 测试

```text
py -3 -m unittest discover \
  -s .agents/skills/interactive-ppt-generation/tests \
  -v
```

真实四主题验收样例保存在 `examples/visual-asset-e2e/`，包含科学、历史、产品与地理 slot 的 requirements、事件流、最终结果、机器 manifest 和已下载资产。

## 第三方代码

SVG 转换后端复用自 PPT Master 的 MIT 许可代码。固定来源、commit 和许可证说明见 `.agents/skills/interactive-ppt-generation/THIRD_PARTY_NOTICES.md`。

Visual Asset Pipeline 研究了 SenseNova 的 `sn-search-image`、`sn-ppt-standard` 和 `sn-image-base`，但相关能力已改写为本仓库内部 Python 模块；这些 Skill 不是运行时依赖。

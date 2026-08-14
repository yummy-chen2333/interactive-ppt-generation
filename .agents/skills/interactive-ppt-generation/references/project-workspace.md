# Project Workspace Specification

为每次 PPT 任务建立独立项目工作区，并把讲述逻辑、上屏内容、逐页组装方案和后端产物分开保存。

## Contents

1. 创建时机
2. 项目目录
3. 固定所有权
4. 文件格式
5. 三库一图
6. `spec_lock.md` 生成边界
7. 修订规则

## 1. 创建时机

在 Stage 2 的主题与受众确认后创建项目。执行：

```text
python scripts/init_project.py <project_path> --slides <page_count>
```

页数尚未确定时省略 `--slides`；在 Stage 4 页数确认后重新执行并追加缺少的逐页素材目录。脚本可以扩展页数，但拒绝自动缩减；删页属于内容结构修改，必须显式清理所有受影响记录。

**Hard rule**：初始化脚本不得覆盖已有项目内容。它只创建缺失文件、追加缺少的逐页章节、升级流程状态 schema，并同步工具拥有的 `project-state.yaml.page_count`；Gate 关闭与恢复由 `workflow_state_cli.py` 负责。

---

## 2. 项目目录

```text
<project>/
├── project-state.yaml
├── inputs/
│   └── other-sources/
├── research/
│   ├── research-notes.md
│   ├── sources/
│   └── visual-assets/
│       ├── visual-requirements.json
│       ├── image-search-log.md
│       ├── candidates/
│       └── retrieval-state/
├── narrative/
│   ├── presentation-brief.md
│   ├── presentation-structure.md
│   ├── slide-intent.md
│   └── speaker-notes.md
├── ppt-content/
│   ├── text/slide-copy.md
│   ├── visuals/asset-manifest.json
│   ├── visuals/asset-manifest.md
│   ├── visuals/user-assets.json
│   ├── visuals/assets/PXX/
│   ├── visuals/slide-XX/{user,ai,vectors}/
│   └── design/
│       ├── design-system.md
│       ├── page-layouts.md
│       ├── template-analysis.md
│       ├── backgrounds/
│       └── shared-assets/
├── production/
│   ├── slide-production-plan.md
│   └── layout-review.md
├── spec_lock.md
├── svg_output/
├── svg_final/
├── validation/
└── exports/
```

`spec_lock.md` 在 Stage 8 结束时创建；初始化空项目时不提前生成它。

---

## 3. 固定所有权

| 路径 | 保存内容 | 不得保存 |
|---|---|---|
| `project-state.yaml` | 当前阶段、确认门状态、页数和待修改页码 | PPT 内容和设计决定 |
| `inputs/` | 用户上传的参考 PPT、演讲稿、PDF、Word 和原始资料 | Agent 改写后的内容 |
| `research/` | 外部事实、数据、人物故事、来源、图片搜索过程及保存的文字资料 | 最终上屏文案和图片本体 |
| `narrative/` | PPT 类型、板块、每页目的、核心观点、完整讲稿和衔接 | 页面坐标和最终排版 |
| `ppt-content/text/` | 最终出现在页面上的标题、正文、图注和页脚来源 | 长篇研究笔记和演讲逻辑 |
| `ppt-content/visuals/` | 图片、原创 SVG 本体及其来源索引 | 页面整体排版 |
| `ppt-content/design/` | 背景、配色、字体、字号、页面结构和共享装饰 | 逐页最终文字 |
| `production/` | 每页如何引用并组合文字、视觉和设计，以及三阶段布局验收 | 新事实、新素材和新文案 |
| `spec_lock.md` | 从已定稿设计投影出的完整具名 typography tokens 与后端技术契约 | 完整设计说明和逐页内容 |
| `svg_output/` | 按施工图生成的完整逐页 SVG 源文件 | 上游未确认的替代设计 |

**Hard rule**：一个决定只有一个所有者。下游文件引用上游内容，不复制后再独立修改。

`project-state.yaml` 由 `workflow_state_cli.py` 原子更新。每个 confirmed Gate 同时保存对应 artifact 指纹；状态声明、artifact 有效性和指纹任一不一致即为 stale。使用 `status` 检查，使用 `resume` 回到最后一个真实完成的 Gate。

---

## 4. 文件格式

| 文件 | 必需章节 |
|---|---|
| `narrative/presentation-brief.md` | 主题、受众、页数、时长、演讲材料、参考 PPT、特殊要求 |
| `research/research-notes.md` | 待核实问题、事实与数据、人物与故事、来源清单 |
| `research/visual-assets/visual-requirements.json` | deck theme、slide topic、slot purpose、图片要求与可选 profile 覆盖 |
| `research/visual-assets/image-search-log.md` | 从机器 manifest 自动生成的查询、状态与统计视图；不得手工编辑 |
| `narrative/presentation-structure.md` | 演讲目标、PPT 类型、整体逻辑、板块与页码范围 |
| `narrative/slide-intent.md` | 每页目的、核心观点、讲述内容、前后衔接 |
| `narrative/speaker-notes.md` | 每页开场句、完整口语讲稿、证据编号、与画面关系和衔接 |
| `ppt-content/text/slide-copy.md` | 每页标题、结构化正文对象、图注、页脚来源和容量约束 |
| `ppt-content/visuals/asset-manifest.json` | 唯一机器真相：slot、最终文件、原始链接、来源页、profile、许可、哈希、分数、状态 |
| `ppt-content/visuals/asset-manifest.md` | 从 JSON 自动生成的人类可读视图；不得手工编辑 |
| `ppt-content/design/design-system.md` | 设计来源、画布、配色、字体、字号、留白、共享元素 |
| `ppt-content/design/page-layouts.md` | 页面类型、适用页、区域结构和布局约束 |
| `ppt-content/design/template-analysis.md` | 参考 PPT 路径、可复用规律和明确不复用项 |
| `production/slide-production-plan.md` | 每页的上游引用、独立文字对象、元素清单、坐标、尺寸、容量和裁剪方式 |
| `production/layout-review.md` | 每页施工图、SVG 预览和最终 PPTX 的文字与图文碰撞验收 |

按 `P01`、`P02` 的固定页码键组织所有逐页记录。页码调整时同步更新所有相关文件和素材目录。

---

## 5. 三库一图

进入 SVG 实现前，必须存在三个已锁定内容库和一份组装施工图：

```text
narrative/                  为什么讲、完整讲稿怎么说
ppt-content/text/           页面最终显示什么文字
ppt-content/visuals/        页面使用什么视觉素材
ppt-content/design/         页面采用什么背景与结构
             \                 |                 /
              production/slide-production-plan.md
                              ↓
                       svg_output/PXX.svg
```

**Validation**：`slide-production-plan.md` 中每个文字、图片、矢量和布局引用都能解析到已有项目文件；进入 Stage 9 后不再搜索、生成、替换或改写这些内容。

进入 Stage 9 前还必须满足：每页 `speaker-notes.md` 已完成；`visual_asset_cli.py validate-manifest` 通过；所有 `real-evidence` 和 `real-scene` slot 在 `asset-manifest.json` 中具有选中资产或明确失败状态；`mixed` 页面同时完成真实图片与原创 SVG 路线；`layout-review.md` 的施工图检查已通过。

---

## 6. `spec_lock.md` 生成边界

在 `design-system.md` 与 `page-layouts.md` 定稿后创建 `spec_lock.md`。它是给检查器和转换器读取的机器稳定投影，不是第四个设计文件。

至少写入：

```markdown
# Execution Lock

## canvas
- viewBox: 0 0 1280 720
- format: PPT 16:9

## colors
- background: #F4F7FB
- primary: #175CD3
- body_text: #172B4D

## typography
- font_family: Microsoft YaHei, Arial, sans-serif
- title_family: Microsoft YaHei, Arial, sans-serif
- body_family: Microsoft YaHei, Arial, sans-serif
- title: 40
- body: 24
- slide_title: 40
- card_title: 26
- body_small: 20
- caption: 14
- attribution: 11

## pptx_structure
- mode: flat
```

当前 Skill 的参考 PPT 路线只学习视觉风格，仍使用 `mode: flat`。不得仅因用户提供参考 PPT 就改成 `structured`。

`ppt-content/design/design-system.md` 的“字体与字号”表必须使用 lower_snake_case role 与精确 unitless px 数值。Stage 8 运行 `stage8_contract_cli.py compile-typography` 确定性投影全部实际角色；生产计划文字表用“字号角色/typography_role”引用这些 token，不写字号范围。`production_plan` Gate 会在 Stage 9 前检查所有角色、稀疏例外和 manifest 素材决策继承。

---

## 7. 修订规则

| 用户修改 | 更新所有者 | 随后重建 |
|---|---|---|
| 板块、页数、页面目的 | `narrative/` | 受影响的全部下游文件 |
| 逐页讲稿 | `narrative/speaker-notes.md` | 图片搜索、上屏文字、施工图、SVG 和 PPTX |
| 页面上屏文字 | `ppt-content/text/slide-copy.md` | 施工图、对应 SVG 和 PPTX |
| 页面图片或示意图 | `ppt-content/visuals/` | 索引、施工图、对应 SVG 和 PPTX |
| 风格、背景或布局 | `ppt-content/design/` | `spec_lock.md`、施工图、受影响 SVG 和 PPTX |
| 坐标、尺寸或裁剪 | `production/slide-production-plan.md` | 对应 SVG 和 PPTX |

不得直接修改 `svg_final/` 或已发布 PPTX 作为项目源。

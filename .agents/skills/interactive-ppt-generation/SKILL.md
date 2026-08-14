---
name: interactive-ppt-generation
description: Create new editable PPTX presentations through staged user confirmation, evidence-backed per-slide speaker notes, speaker-note-driven image search, structured slide copy, layout collision review, optional reference-PPT learning, project-canonical SVG authoring, and SVG-to-DrawingML export. Use when a user asks a Coding Agent to create, revise, or regenerate a presentation from a topic, speech text, source material, reference PPT, or user-provided images.
---

# Interactive PPT Generation Skill

通过“需求确认 → 内容规划 → 素材与排版规划 → 逐页 SVG 实现 → DrawingML/PPTX 导出”生成原生可编辑演示文稿。

## 1. Mandatory Load Order

1. 读取本文件。
2. 读取 [`references/workflow.md`](./references/workflow.md)。
3. 新建或恢复具体 PPT 项目时，读取 [`references/project-workspace.md`](./references/project-workspace.md)。
4. 内容方案确认、即将编写逐页讲稿和搜索图片时，读取 [`references/speaker-notes-image-search.md`](./references/speaker-notes-image-search.md)。
5. 编写逐页施工图、页面 SVG 和视觉验收时，读取 [`references/layout-review.md`](./references/layout-review.md)。
6. 在开始编写页面 SVG 前，读取 [`references/svg-authoring.md`](./references/svg-authoring.md)。
7. 进入 Stage 7 的网络图片检索时，读取 [`references/source-policy-profiles.yaml`](./references/source-policy-profiles.yaml)，并调用内置 `scripts/visual_asset_cli.py`；不得由 Agent 自由浏览网页逐张下载。
8. 进入 Stage 8 时，调用 `scripts/stage8_contract_cli.py` 编译 typography tokens，并在进入 Stage 9 前运行其确定性 validator。
9. 进入 Stage 10 后，按该阶段的条件读取四份后端文档。
10. 按工作流顺序执行，不得跨过未关闭的用户确认门。

所有 Gate 必须通过内置状态控制器关闭；不得手工把 `project-state.yaml` 改成 `confirmed`：

```text
python scripts/workflow_state_cli.py close <project_path> <gate>
python scripts/workflow_state_cli.py status <project_path>
python scripts/workflow_state_cli.py resume <project_path>
python scripts/workflow_state_cli.py user-assets <project_path> <none|scan>
```

状态控制器同时验证 artifact 并保存指纹；artifact 变化会使旧 Gate 变为 stale。`run_backend.py` 会先执行同一套上游 Gate 校验。

---

## 2. Global Execution Discipline

1. **串行执行**：按工作流顺序完成各阶段。
2. **阻塞即停止**：标记为 `BLOCKING` 的阶段必须等待用户明确确认。
3. **不跨阶段准备**：内容定稿前，不搜索图片、不生成图片、不制作页面 SVG。
4. **讲稿先于视觉**：先用已核实资料写出逐页讲稿，再从讲稿提取具体视觉目标并搜索图片。
5. **规划先于实现**：每页讲稿、上屏文字、素材和排版方案就绪后，才允许生成该页 SVG。
6. **修改回到所有者**：先修改对应的内容或页面方案，再重新生成 SVG 和 PPTX；不对已导出文件进行不可追踪修改。
7. **不伪造信息**：不编造图片、数据、实验结果或引用来源。
8. **维持可追溯性**：对外部事实、网络图片和 AI 生成图片记录来源、时间和使用页码。

---

## 3. Route Boundary

首先询问用户是否有参考 PPT，然后选择且仅选择一条设计分支：

| 条件 | 分支 |
|---|---|
| 用户提供参考 PPT | 学习其背景、配色、字体、图文比例与页面组织方式 |
| 用户未提供参考 PPT | 根据 PPT 类型与受众创建统一设计系统 |

该分支只决定后续设计方式。两条分支都必须经过相同的内容确认流程。

模板路由只询问一次。用户已提供参考 PPT 后，该文件就是本次设计依据；后续不得再次要求用户选择 PPT Master 模板、风格模式或生成路线。

---

## 4. User Confirmation Gates

| Gate | 用户确认内容 | 关闭后允许进入 |
|---|---|---|
| Gate 1 | 是否使用模板 | 基本需求收集 |
| Gate 2 | 主题、受众、页数、演讲文本 | 文字内容规划 |
| Gate 3 | PPT 类型、大板块、逐页目的与内容 | 内容修改循环 |
| Gate 4 | 内容修改已完成 | 用户图片收集与视觉规划 |
| Gate 5 | 用户指定图片已收集或明确无图片 | 外部素材获取与逐页制作方案 |

**Hard rule**：不得用模型推测代替用户对 Gate 1–5 的明确回答。

---

## 5. Decision Ownership

| 层级 | 所有内容 |
|---|---|
| 用户 | 主题、受众、页数约束、演讲材料、参考 PPT、指定图片与修改要求 |
| `narrative/` | PPT 类型、大板块、逐页目的、核心观点、完整讲稿和页面衔接 |
| `ppt-content/` | 最终上屏文字、图片与矢量素材、背景和视觉结构 |
| `production/` | 每页如何引用并组合已经锁定的文字、视觉和设计 |
| SVG 实现 | 将已确认的逐页制作方案实现为符合转换后端规范的逐页 SVG |
| 转换后端 | 仅检查、预处理已完成的 SVG，并转换 DrawingML、导出 PPTX |

**Hard rule**：下游只能在上游未锁定的维度中进行判断。`production/` 只组合，不重新创作；SVG 实现阶段不得重新选择素材、改写上屏文字、改变页面主旨或调整页面顺序。

### Project Workspace

每次 PPT 使用一个独立 `<project>/`。主题与受众确认后初始化：

```text
python scripts/init_project.py <project_path>
```

页数确认后补齐逐页记录与素材目录：

```text
python scripts/init_project.py <project_path> --slides <page_count>
```

初始化脚本只创建缺失内容，不覆盖已有项目文件。项目文件的唯一所有者与固定格式见 [`references/project-workspace.md`](./references/project-workspace.md)。

---

## 6. Asset Priority

按视觉目标类型满足页面素材需求，不为整套 PPT 统一选择一种视觉路线：

```text
真实证据：用户图片 → 网络真实图片 → 搜索失败记录
现实场景：用户图片 → 网络真实图片 → 搜索失败记录 → AI 示意图
抽象关系：项目原创 SVG
混合页面：真实图片 + 原创 SVG 分别准备
```

用户提供图片存在时，不得无理由忽略。

网络图片搜索不得只使用页面标题或页面目的。依据逐页讲稿提取具体人物、设备、事件、年份、机构和场景，记录搜索词、候选及最终选择。具体规则见 [`references/speaker-notes-image-search.md`](./references/speaker-notes-image-search.md)。

**Hard rule**：真实人物、设备、事件或现实场景对应的目标如果没有搜索词、候选 URL 或明确失败记录，素材阶段未完成。不得用原创 SVG 绕过该搜索门。

### Built-in Visual Asset Pipeline

网络图片阶段的正式执行入口是：

```text
python scripts/visual_asset_cli.py preflight <project_path> --host-native-vision <available|unavailable|unknown>
python scripts/visual_asset_cli.py retrieve <project_path> --host-native-vision <available|unavailable|unknown>
python scripts/visual_asset_cli.py validate-manifest <project_path>
```

在开始正式 workflow 后先运行一次环境预检；`visual-requirements.json` 形成后，Stage 7 的 `retrieve` 会再次执行 deck-aware preflight。预检检测结构化搜索、网络、宿主原生视觉能力、可选外部视觉扩展、PowerPoint/最终渲染器和必需配置，并把机器结果写入 `<project>/validation/capability-preflight.json`。当前 Main Agent 必须依据自己是否能真正读取本地图片，如实传入 `available | unavailable | unknown`；不得根据某个厂商 API key 推断宿主视觉能力。

调用前，Agent 必须依据 `deck theme → slide topic → slot purpose` 写入 `<project>/research/visual-assets/visual-requirements.json`。内置 CLI 再选择 `references/source-policy-profiles.yaml` 中的 profile，执行 `query generation → structured search → filtering → bounded download → local validation → presentation-grade verification → ranking → selection`。profile 只影响候选排序、时效和署名处理；官方机构、大学、博物馆、政府、公司官网和权威媒体不是照片准入条件。

**最高优先级照片审核规则**：所有外部照片统一使用 `verification_risk: presentation-grade`、`verification_mode: presentation` 和 `verification_status: presentation-verified`。历史 manifest 中的 `evidence-critical`、`strict-*`、provenance 或 VLM 字段只可作为兼容记录，运行时不得据此改变 accept/reject、early stop、Stage 7 completion 或下游 Gate。

照片在以下条件同时满足时立即接受：文件可读取；分辨率满足实际 slot；来源页不是明显垃圾、恶意或无关页面；title、caption、surrounding context 或 metadata 与 slot 基本相关；没有已发现的明显错人、错物、错时代、错事件、错误图片类型或其他明确矛盾；没有明确禁止使用的许可声明。判断标准是 `no obvious contradiction`，不是证明所有历史细节达到档案或取证标准。

每个照片 slot 默认最多 2 条 query、保留 5 个候选、下载 2 个文件、进行 1 次可选视觉快检、每 URL 重试 1 次，并在 45 秒绝对 deadline 结束。第一张通过基本门槛的图片立即 early stop；预算或 deadline 耗尽时选择已经可用的 best-so-far。不得为了更权威来源、精确日期、档案编号、多来源交叉认证、作者或许可证全称继续扩展搜索。

VLM 与宿主视觉永远不是照片通过条件。宿主能看图时最多进行一次快速的明显错误检查；不能看图时直接使用 source page、title、caption/context、metadata 和来源可信度完成审核。无 VLM 不得导致 reject、unresolved、`capability-degraded` 或 Stage 7 阻塞。`capability-degraded` 只用于搜索/网络完全不可用，或限定预算内没有任何可下载、可读取、基本相关且未被明确禁止使用的照片。

`<project>/ppt-content/visuals/asset-manifest.json` 是图片选择、来源、许可证、文件路径、验证与显示署名策略的唯一机器真相。Stage 7 必须记录 selected asset ID、local path、source page、original image URL、domain、author/credit、license 或 `license_status: unknown`、query、verification method、confidence、完整已取得 provenance、`display_attribution_mode`、canonical `display_attribution` 和 `license_obligation`。显示模式只允许 `full-credit | compact-source | provenance-only | none`；明确许可要求仍必须执行，但缺失作者或精确许可证名称不得触发跨站调查。Stage 8、Stage 9 和 Checker 只能检查同一文件、正确页面、文件完整性、署名/许可执行与排版裁切，不得重新审核来源权威、人物身份、历史绑定、VLM 或 provenance 等级。`asset-manifest.md` 与 `image-search-log.md` 只能由 JSON 自动投影。

Stage 8 的正式机器入口是：

```text
python scripts/stage8_contract_cli.py sync-manifest <project_path>
python scripts/stage8_contract_cli.py compile-typography <project_path>
python scripts/stage8_contract_cli.py validate <project_path>
```

先从设计系统的具名字号角色编译 `spec_lock.md`，再让生产计划逐对象引用 `typography_role` 并逐素材原样记录 Stage 7 决策。`validate` 未通过时不得关闭 `production_plan` Gate 或进入 Stage 9。

**Hard rule**：Stage 7 的网络图片检索必须真正运行内置 CLI。不得运行时调用第三方图片 Skill，不得长期停留在 `Planning image downloads...`，不得用手工浏览器检索替代主路径；浏览器仅可在 CLI 返回 `unresolved` 后作为有界 fallback。

---

## 7. SVG and PPTX Boundary

- 每页 PPT 对应一个 `svg_output/P<NN>.svg`。
- 只生成符合 PPT Master 转换规范的 SVG，不使用仅浏览器可解析的未支持效果。
- 本 Skill 自行完成交互、内容规划、模板学习、素材准备、排版规划与 SVG 生成。到达转换阶段后，直接调用项目内集成的转换脚本；不得再次进入 PPT Master 的路由、规划、模板选择、确认网页或完整 Generate 工作流。
- 本 Skill 已在 `scripts/` 内集成 `svg_quality_checker.py`、`finalize_svg.py`、`svg_to_pptx.py` 及转换所必需的依赖，不得从外部 PPT Master 仓库解析脚本路径。
- 对每页 SVG 运行质量检查；发现错误时，修复对应 SVG 或上游制作方案。
- `finalize_svg.py` 生成 `svg_final/` 预览文件。
- `svg_to_pptx.py` 从 `svg_output/` 转换并导出原生 PPTX。
- 不得把 `svg_final/` 误作为 PPTX 转换输入。

### Backend Reference Load Map

| 即将执行的动作 | 必须读取 |
|---|---|
| 最终 SVG 质量检查 | [`references/svg-quality-checker.md`](./references/svg-quality-checker.md) |
| 生成 `svg_final/` | [`references/svg-finalization.md`](./references/svg-finalization.md) |
| SVG→DrawingML/PPTX 转换 | [`references/svg-to-drawingml.md`](./references/svg-to-drawingml.md) |
| Postflight、版本发布与交付验证 | [`references/pptx-export.md`](./references/pptx-export.md) |

只在到达对应动作时加载其文档。四份文档共同约束 Stage 10，但不引入第二套内容规划或模板工作流。

### Portable Backend Command

首次使用时，在 Skill 根目录安装依赖：

```text
python -m pip install -r requirements.txt
```

Windows 中若 `python` 未注册但 Python Launcher 可用，将本文命令的 `python` 替换为 `py -3`。

完成 `svg_output/` 和 `spec_lock.md` 后，使用统一入口完成检查、预览、DrawingML/PPTX 转换和版本发布：

```text
python scripts/run_backend.py <project_path> --title <title> --changed-pages <pages>
```

统一入口在发布后强制运行 `scripts/final_acceptance_validator.py`，从 `exports/latest.json` 读取 PPTX，检查 ZIP、页数、canonical speaker notes、manifest 图片、PowerPoint 渲染和最终布局。只有报告为 `passed` 且 `final_validation` Gate 关闭后，`current_stage` 才能成为 `completed`。

不得将转换器产生的 `_conversion_output.pptx` 直接交付；以 `exports/latest.json` 指向的版本为准。

### Browser Boundary

- 默认在 Coding Agent 对话中完成确认，以 SVG、PNG 或 PPTX 文件作为检查结果；第一版不依赖网页。
- 不得自动启动 PPT Master 的 `confirm_ui`、`svg_editor/server.py` 或其他网页交互流程。
- 只有用户明确要求浏览器预览时，才可临时打开本项目的只读预览；它不拥有规划、模板选择或发布权限。
- 网络检索图片与开发网页编辑器是两件事：前者属于素材获取，后者不在第一版范围内。

---

## 8. Revision Boundary

| 用户反馈 | 返回阶段 |
|---|---|
| 修改指定页文字 | 逐页内容方案 |
| 修改指定页演讲稿 | `narrative/speaker-notes.md`，并重新评估视觉搜索和上屏文字 |
| 替换指定页图片 | 逐页素材方案 |
| 修改指定页排版 | 逐页排版方案 |
| 调整页数、顺序或大板块 | 内容规划与 Gate 3/4 |

修改所有者文件后，重新生成受影响的 SVG，再运行完整质量检查和 PPTX 导出。

---

## 9. Version Publication

1. 每次导出必须发布为新的不可覆盖文件，例如 `V003_<title>_20260812_214500.pptx`。
2. 不得覆盖原始 PPTX，不得把同名 `editable.pptx` 或 PowerPoint“最近使用”的文件作为交付路径。
3. 使用 [`scripts/publish_version.py`](./scripts/publish_version.py) 将转换器临时输出发布到 `exports/`，并写入 `exports/latest.json` 和 `exports/publish_history.jsonl`。
4. 发布后必须按 `latest.json` 中的绝对路径重新读取或渲染该文件，并检查本轮修改页；不得通过同名旧文件判断修改是否生效。
5. 向用户返回新版本的准确文件名、绝对路径、SHA-256 和修改页码。

---

## 10. Scope

**第一版支持**：新建 PPTX、参考 PPT 风格学习、自由设计、用户图片、网络图片、AI 图片、逐页 SVG、DrawingML/PPTX 导出与按页修改。

**第一版不支持**：独立网页编辑器、HTML Presentation、音频旁白、动画定制、视频内容处理和运行时多 Skill 协同。

本仓库已内置从 PPT Master 复用的 SVG 质量检查与 SVG-to-DrawingML/PPTX 转换后端。第三方来源、固定 commit 和许可证见 [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md) 与 [`LICENSE`](./LICENSE)。不将其交互、规划或模板工作流声明为本项目自研内容。

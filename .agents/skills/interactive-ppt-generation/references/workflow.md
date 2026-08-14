# Interactive PPT Generation Workflow

本文件规定从用户请求到原生 PPTX 导出的串行工作流。

## Contents

0. 工作流所有权边界
1. 模板路线
2. 基本需求
3. 纯文字规划
4. 文字方案确认
5. 用户素材收集
6. 设计系统
7. 研究、逐页讲稿与素材准备
8. 逐页施工图
9. SVG 实现
10. 检查与导出
11. 用户修改

## 0. Workflow Ownership Boundary

本工作流是从需求确认到逐页 SVG 生成的唯一主流程。Stage 1–9 均由本项目负责；Stage 10 只把已经完成的 `svg_output/` 交给本 Skill `scripts/` 中已集成的转换后端。

新建或恢复项目时先读取 [`project-workspace.md`](./project-workspace.md)。所有阶段结果写入该规范指定的唯一所有者文件。

**Forbidden — second workflow**：不得在 Stage 9 后调用 PPT Master Skill、路由选择、Generate PPTX 工作流、模板选择器、确认网页或浏览器 SVG 编辑器。不得因转换后端来自 PPT Master 而重新询问模板、重新做内容规划或重新选择设计风格。

**Backend entry condition**：只有逐页内容、素材与排版方案已锁定，并且 `svg_output/P<NN>.svg` 已生成时，才允许进入 Stage 10。

---

## 1. Stage 1: Template Route

**BLOCKING**：询问用户是否有参考 PPT 或历史优秀 PPT。

| 状态 | 记录 |
|---|---|
| 有模板 | 记录模板路径，标记 `template_route: reference` |
| 无模板 | 标记 `template_route: free-design` |

本阶段只确定路由，不分析模板。

记录路线后运行 `workflow_state_cli.py close <project> template_route --route <reference|free-design>`；不得手工关闭 Gate。

本阶段是全流程唯一一次模板路由确认。若用户已上传参考 PPT，后续直接学习该文件，不得再次让用户选择模板或风格。

---

## 2. Stage 2: Presentation Brief

**BLOCKING**：收集并确认以下信息。

| 字段 | 必需 | 处理 |
|---|---|---|
| 主题 | 是 | 保留用户原始表述 |
| 受众 | 是 | 记录身份与专业背景 |
| 页数 | 否 | 有约束时遵守；无约束时由内容规划建议 |
| 演讲稿或文字材料 | 否 | 读取用户粘贴文本或指定文件 |

展示完整摘要。用户明确确认后才进入 Stage 3。

确认后初始化项目并写入实际内容：

```text
python scripts/init_project.py <project_path>
```

- 把用户原始文件保存到 `inputs/`。
- 把已确认需求写入 `narrative/presentation-brief.md`。
- 把当前阶段和 Gate 状态写入 `project-state.yaml`。

所有 Gate 用 `scripts/workflow_state_cli.py close <project> <gate>` 原子关闭。脚本先验证真实 artifact，再记录指纹；禁止手工写入 `confirmed`。恢复项目时运行 `scripts/workflow_state_cli.py resume <project>`，以最后一个仍通过 artifact 指纹校验的 Gate 为恢复点。

---

## 3. Stage 3: Text-Only Planning

按顺序完成：

1. 根据主题和受众判断 PPT 类型。
2. 规划整体表达逻辑和大板块。
3. 确定每个板块需要说明的内容。
4. 根据逻辑与页数约束将内容拆分为逐页计划。
5. 为每页编写标题、页面目的、核心观点和必须讲述的具体内容。

**Forbidden — premature design**：本阶段不选择图片，不确定图片数量，不设计视觉排版。

把 PPT 类型、整体逻辑和板块写入 `narrative/presentation-structure.md`；把每页目的、核心观点、讲述内容和衔接写入 `narrative/slide-intent.md`。

---

## 4. Stage 4: Text Plan Approval

**BLOCKING**：按以下结构展示完整文字方案：

1. PPT 类型与判断依据。
2. 大板块划分、顺序与板块逻辑。
3. 每页的页码、标题、页面目的和具体内容。

用户提出局部修改时，修改对应页；用户提出整体修改时，重新规划板块、页数、顺序和逐页内容。

重新展示完整方案，直到用户明确确认。

确认后更新 `narrative/` 中的所有者文件，再按确认页数补齐项目；`project-state.yaml` 只由状态控制器更新：

```text
python scripts/init_project.py <project_path> --slides <page_count>
```

---

## 5. Stage 5: User Asset Collection

**BLOCKING**：按页询问用户是否有指定照片、截图、Logo 或其他图片。

| 用户状态 | 处理 |
|---|---|
| 提供图片 | 保存到对应页面的用户素材目录 |
| 未提供图片 | 保留对应页面的空用户素材目录 |

等待用户确认所有页面的用户素材已提供完毕。

保存后运行 `workflow_state_cli.py user-assets <project> scan`；用户明确没有图片时运行 `workflow_state_cli.py user-assets <project> none`。该命令写入 `ppt-content/visuals/user-assets.json` 的文件清单与哈希，随后才能关闭 `user_assets` Gate。

用户图片只保存到 `ppt-content/visuals/slide-XX/user/`，并在 `ppt-content/visuals/asset-manifest.json` 中登记；Markdown 索引只能由 JSON 生成。

---

## 6. Stage 6: Design-System Planning

### 6.1 Reference-PPT Branch

分析参考 PPT 的背景、配色、字体、字号层级、图文比例、文本/图片区域与常见页面类型。

参考 PPT 是本次设计依据，不把它再次交给 PPT Master 的模板创建或模板选择流程。

把分析结果写入 `ppt-content/design/template-analysis.md`，把最终采用的设计规则写入 `design-system.md` 和 `page-layouts.md`。

### 6.2 Free-Design Branch

根据 PPT 类型、主题与受众确定整体背景、配色、字体、字号层级、留白、对齐与通用图文结构。可调研同类 PPT 的设计规律，不复制具体页面。

把最终设计规则写入 `ppt-content/design/design-system.md` 和 `page-layouts.md`；将 `template-analysis.md` 状态保留为 `not-applicable`。

---

## 7. Stage 7: Asset Preparation

读取 [`speaker-notes-image-search.md`](./speaker-notes-image-search.md)。本阶段先研究事实并完成逐页讲稿，再从讲稿准备视觉素材。

### 7.1 Research and Speaker Notes

- 把事实、数据、人物故事与来源写入 `research/research-notes.md`，保存的文字资料放入 `research/sources/`。
- 根据已确认的页面意图和研究依据，为每页编写 `narrative/speaker-notes.md`。
- 讲稿包含完整口语讲述、证据编号、与画面的对应关系和页面衔接。
- 若讲稿出现新的事实缺口，先补充研究，再完成该段讲稿。

完整讲稿默认不作为 Gate 4 的用户确认负担，但必须保存；用户要求查看或修改时提供。未来 Word 汇报稿从该文件导出，不从 PPT 页面反推。

### 7.2 Visual Requirement Compilation

本阶段的“视觉素材”包括用户图片、网络图片、AI 生成图片和项目原创 SVG。它们不是可任意互换的并列路线；先按 [`speaker-notes-image-search.md`](./speaker-notes-image-search.md) 给每个视觉目标分类，再执行对应路线。

原创 SVG 适用于流程图、时间线、结构图、数据链路、人体传感位置图等需要保持可编辑性的说明性视觉。其事实依据仍应来自已记录的资料来源，但原创几何本身记录为“项目自制”，不得伪装成外部图片。

从逐页讲稿提取具体人物、设备、事件、年份、机构、地点、场景和可视化关系，据此确定每页图片位的作用与来源。不得只用页面标题或空泛目的搜索。将所有需要网络真实图片的 slot 写入：

`research/visual-assets/visual-requirements.json`

每个 slot 必须包含 `slot_id`、`slide_number`、`deck_theme`、`slide_topic`、`purpose`、`subject`、`visual_type`、`required_subject`、`required_asset_type`、`required_relationship`、`forbidden_asset_types` 和 `authenticity_requirement`。CLI 对所有外部照片统一回写 `verification_risk: presentation-grade` 与 `verification_mode: presentation`；历史输入中的严格风险字段只保留为兼容记录，不参与运行时决策。可选字段包括 `entity_aliases`、`required`、`source_policy`、`queries`、`required_terms`、`negative_terms`、`preferred_domains`、`excluded_domains`、最小分辨率和期望宽高比。通常让 pipeline 自动选 profile；profile 只影响候选排序、时效和署名，不是准入 Gate。

### 7.3 Deterministic Visual Asset Pipeline

网络图片检索不再由 Agent 自由逐页浏览。固定执行：

```text
deck theme
→ slide topic
→ slot purpose
→ source policy selection
→ query generation
→ structured search scope
→ candidate filtering
→ bounded download
→ local validation
→ visual analysis
→ ranking
→ selection
→ asset-manifest.json
```

来源策略必须来自 [`source-policy-profiles.yaml`](./source-policy-profiles.yaml)。同一 pipeline 至少支持 scientific/academic、humanities/culture、historical evidence、public figure/biography、company/product/technology、geography/travel/landmark、artwork/museum object、news/current events、generic real-world photography、decorative/background。不得在核心代码中固定科研网站优先级。

在 Skill 根目录运行：

```text
python scripts/visual_asset_cli.py preflight <project_path> --host-native-vision <available|unavailable|unknown>
python scripts/visual_asset_cli.py retrieve <project_path> --host-native-vision <available|unavailable|unknown>
python scripts/visual_asset_cli.py validate-manifest <project_path>
```

`preflight` 在检索前检查 structured search backend、network、宿主原生视觉声明、可选外部视觉扩展、PowerPoint/最终 renderer 和必需配置，结果写入 `validation/capability-preflight.json`。Main Agent 必须如实声明 `host_native_vision`，但该声明只决定能否进行一次可选快检；`unavailable` 或没有任何外部 VLM key 均不得阻塞 Stage 7。

CLI 必须承担 timeout、bounded concurrency、retry、429 handling、domain circuit breaker、query/cache budgets、per-slot deadline、no-progress watchdog、best-so-far 与 good-enough early stop。每个照片 slot 默认上限为 2 条 query、5 个保留候选、2 次下载、1 次可选视觉快检、每 URL 1 次重试和 45 秒绝对 deadline。它把机器状态写入 `research/visual-assets/retrieval-state/`，候选下载写入 `research/visual-assets/candidates/`，最终资产写入 `ppt-content/visuals/assets/P<NN>/`。

所有外部照片只回答一个问题：`Is this image good enough for this presentation?`。合格条件是文件有效且分辨率够用、来源页不是明显垃圾/恶意/无关、title/caption/context/metadata 与 slot 基本相关、没有明确错人错物错时代错事件或图片类型冲突、没有明确禁止使用。metadata 可以与可信来源页和上下文共同完成最终审核；随机文件名、搜索结果标题或 URL 单独出现关键词仍不足以通过。

宿主视觉可用时最多快速检查 1 张候选是否存在明显错图；不可用时直接走 source page + title/caption/context + metadata。不得启动多轮 VLM 复审、交叉来源认证、精确日期/事件调查、档案编号核验或人物身份长时间搜索。官方来源只增加排序分；第一张达到上述门槛的候选立即 early stop。deadline 或数量预算耗尽时，有可用候选就选择 best-so-far；完全没有可读取且基本相关的候选才保留 unresolved/degraded 记录。

`asset-manifest.json` 对 selected asset 记录 `verification_status: presentation-verified`、`verification_risk: presentation-grade`、`verification_method`、`verification_evidence`、`confidence`、`verification_timestamp` 与已取得的 provenance。Stage 7 是这些决定的唯一所有者。

`ppt-content/visuals/asset-manifest.json` 是唯一机器真相。`asset-manifest.md` 和 `research/visual-assets/image-search-log.md` 是 CLI 自动生成的人类视图，不能反向编辑。网络资产必须记录图片 URL、来源页、domain、query、profile、来源等级、许可证/署名、哈希、分辨率、分数、选择原因和使用 slot。

真实图片目标按以下顺序获取：

```text
real-evidence：用户图片 → 内置 Visual Asset Pipeline → 搜索失败记录
real-scene：用户图片 → 内置 Visual Asset Pipeline → 搜索失败记录 → profile 允许时 AI 示意图
abstract：项目原创 SVG
mixed：真实图片 slot 走内置 pipeline；抽象关系单独制作原创 SVG
```

⛔ **BLOCKING**：每个 `real-evidence` 和 `real-scene` slot 必须在 `asset-manifest.json` 中为 `selected` 且 `presentation-verified`，或保留 pipeline 的 `unresolved/fallback-required/capability-degraded` 记录；必需 slot 只有全部 selected 时 `validate-manifest` 的 `stage7_ready` 才能为 `true`。没有 CLI 状态、必需 slot 未选中、用原创 SVG 绕过真实照片目标、手写 Markdown 冒充检索结果，或 `mixed` 页面只准备原创 SVG 时，不得关闭 `visual_assets` Gate。

Stage 7 在选择素材时同时确定 `verification_status`、`verification_risk`、`verification_method`、`evidence_strength`、`display_attribution_mode` 和 `display_attribution`。显示署名策略只允许：`full-credit`、`compact-source`、`provenance-only`、`none`。明确许可要求继续执行；一次来源页读取无法确定作者、精确许可证或历史 metadata 时写 `license_status: unknown`，采用 `compact-source` 或 `provenance-only`，不得跨站深挖。Stage 8、Stage 9 和 Checker 只继承同一文件、页面、署名、许可和排版决定，不得重新检查来源权威、历史绑定、人物身份置信度、VLM 或档案级 provenance。

---

## 8. Stage 8: Per-Slide Production Plan

读取 [`layout-review.md`](./layout-review.md)。根据已确认的 `narrative/` 与研究依据，把讲稿压缩为最终上屏文字并写入 `ppt-content/text/slide-copy.md`。不要把完整讲稿复制到页面。再为每页编写 `production/slide-production-plan.md`。

每页至少确定：

| 类别 | 内容 |
|---|---|
| 文字 | 独立文字对象、内容键、位置、安全区、`typography_role`、颜色、行距和最大行数 |
| 图片 | 路径、来源、作用、位置、大小、裁剪方式 |
| 背景 | 颜色、图形、图片或模板依据 |
| 排版 | 页面结构、对齐、间距、图文比例 |
| 视频占位 | 是否使用、位置、尺寸 |

生产计划不得使用 `19–42 px` 一类范围。设计系统先用表格定义本 deck 实际使用的具名字号角色和精确 unitless px 数值；每个重复结构文字必须引用一个 role。真正的稀疏展示例外使用 `sparse:<size>`，整套出现超过 2 次时必须改为具名 role。

对 manifest 中每个 selected asset，在对应页面的 `Stage 7 素材决策` 表中原样记录 `asset_id`、`verification_status`、`verification_risk`、`verification_method`、`evidence_strength`、`display_attribution_mode`、`display_attribution`、placement 和 typography role。`full-credit`/`compact-source` 必须使用 `typography_role = attribution`；`provenance-only`/`none` 不生成可见署名。Stage 8 只组合与定位素材，不重新审核图片。

按顺序运行：

```text
python scripts/stage8_contract_cli.py sync-manifest <project_path>
python scripts/stage8_contract_cli.py compile-typography <project_path>
python scripts/stage8_contract_cli.py validate <project_path>
```

**Validation**：本阶段结束时，每页文字、图片、背景和排版已完整；不存在未解析的图片引用。文字已按标题、段落、要点、图注和来源拆为独立对象；施工图坐标检查不存在非预期图文相交。`stage8_ready` 必须为 `true`，结果写入 `validation/stage8-contract-report.json` 与 `production/layout-review.md`。

从已定稿的 `ppt-content/design/` 投影生成项目根目录的 `spec_lock.md`。它保存画布、颜色锚点、完整具名 typography tokens 和 `pptx_structure.mode` 等后端契约，不替代设计文件。保留 backend 所需的 `title`/`body` 兼容锚点，但不得只生成这两个锚点。

---

## 9. Stage 9: SVG Realization

读取 [`svg-authoring.md`](./svg-authoring.md) 与 [`layout-review.md`](./layout-review.md)，按页顺序生成 `svg_output/P<NN>.svg`。每页只根据 `production/slide-production-plan.md` 引用的 `ppt-content/` 内容进行组装，不重新规划内容或素材。文字使用 production plan 引用的精确 typography token；需要显示来源时原样写入 manifest 的 canonical `display_attribution`，不得重新拼作者、license 或域名。

每页 SVG 必须符合 PPT Master 支持规范。转换器不支持的浏览器效果不得进入输出。

每页生成后渲染预览，检查文字越界、图文碰撞、段落结构和视觉层级，并更新 `production/layout-review.md`。失败页先修复施工图或上屏文字，再重新生成 SVG。

---

## 10. Stage 10: Validation and Export

按以下顺序读取并执行后端规范，不得并行运行相邻步骤：

1. 读取 [`svg-quality-checker.md`](./svg-quality-checker.md)，完成最终质量门。
2. 质量门通过后读取 [`svg-finalization.md`](./svg-finalization.md)，生成 `svg_final/` 预览。
3. 预览生成后读取 [`svg-to-drawingml.md`](./svg-to-drawingml.md)，生成临时原生 PPTX。
4. 转换完成后读取 [`pptx-export.md`](./pptx-export.md)，完成 postflight、版本发布和精确路径验证。

发布后由统一入口将 `layout-review.md` 的最终 PPTX 检查列投影为 `passed`，随后运行 Final PPT Acceptance Validator 复查文字换行、图片位置和修改页；存在遮挡或内容丢失时不得关闭 `final_validation` Gate。

```text
svg_output/*.svg
├─→ svg_quality_checker.py → 质量报告
├─→ finalize_svg.py → svg_final/*.svg
└─→ svg_to_pptx.py → 临时 PPTX
                         └─→ publish_version.py → exports/V<NNN>_<title>_<timestamp>.pptx
                                  └─→ final_acceptance_validator.py → final-acceptance-report.json
```

检查失败时，修复对应 SVG 或返回上游制作方案。不得绕过错误并宣布完成。

只调用已经集成到本 Skill `scripts/` 的上述转换脚本及其必要依赖。不得查找外部 PPT Master 安装目录。默认不得启动任何网页服务；预览应从 `svg_final/`、渲染图片或发布后的准确 PPTX 路径完成。

推荐直接运行统一入口：

```text
python scripts/run_backend.py <project_path> --title <title> --changed-pages <page_numbers>
```

该入口先用 workflow state controller 验证 Stage 1—9 artifact 与 Gate 指纹，再串行调用 Checker、Finalizer、SVG→DrawingML/PPTX 转换器、版本发布器和 Final PPT Acceptance Validator。后者从 `exports/latest.json` 读取最终文件，检查实际嵌入的逐页讲稿、图片、包结构、PowerPoint 渲染与最终页面布局；验证失败不得把项目标记为 `completed`。发布脚本必须生成唯一文件名、`exports/latest.json` 和追加式 `exports/publish_history.jsonl`。将 `latest.json` 视为本轮交付的唯一文件指针。

---

## 11. Stage 11: User Revision

用户指定页码与问题后，按修改类型返回对应所有者：

| 反馈 | 返回 |
|---|---|
| 页面目的、页数、顺序、大板块 | `narrative/` 与 Stage 3/4 |
| 逐页讲稿 | `narrative/speaker-notes.md`，随后重新检查研究、图片搜索和上屏文字 |
| 上屏文字 | `ppt-content/text/slide-copy.md` |
| 图片或示意图 | `ppt-content/visuals/` 与素材索引 |
| 风格、背景或布局 | `ppt-content/design/` 与 `spec_lock.md` |
| 坐标、尺寸或裁剪 | `production/slide-production-plan.md` |

更新所有者记录，重新生成受影响 SVG，然后运行完整检查与导出。

每轮修改都必须发布为新的版本号，不覆盖上一版。发布后按 `latest.json` 中记录的绝对路径读取或渲染新文件，并检查本轮修改页和页数；不得打开 PowerPoint“最近使用”项，也不得以相同文件名覆盖后再次打开。向用户明确返回新旧版本号、修改页码、准确路径和 SHA-256。

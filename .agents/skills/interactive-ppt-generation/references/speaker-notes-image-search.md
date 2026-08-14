# 逐页讲稿与图片检索规范

在文字内容方案确认、用户图片收集完成后读取本文。先建立有依据的逐页讲稿，再从讲稿提取视觉目标并搜索图片。

## 1. 输入与输出

| 类型 | 路径 | 内容 |
|---|---|---|
| 页面意图 | `narrative/slide-intent.md` | 页面目的、核心观点和衔接 |
| 研究依据 | `research/research-notes.md` | 事实、数据、人物故事和来源编号 |
| 逐页讲稿 | `narrative/speaker-notes.md` | 实际汇报时每页要说的完整内容 |
| 检索需求 | `research/visual-assets/visual-requirements.json` | deck theme、slide topic、slot purpose 与图片要求 |
| 搜索记录 | `research/visual-assets/image-search-log.md` | CLI 自动生成的查询、状态、预算与选择结果 |
| 最终素材 | `ppt-content/visuals/` | 下载图片、AI 图片和原创 SVG |
| 素材索引 | `ppt-content/visuals/asset-manifest.json` | 唯一机器真相：最终素材、来源、许可、哈希、评分和用途 |

`speaker-notes.md` 是未来导出 Word 汇报稿的唯一讲稿来源，但当前版本不声称已经实现 Word 导出。

---

## 2. 先研究再写讲稿

**Per-page research**：从已确认的页面意图列出完成讲述所需的具体事实。优先核实人物、设备、事件、年份、机构、数据和因果关系，再写入 `research-notes.md`。

**Hard rule**：讲稿中的外部事实必须能追溯到用户材料或 `research-notes.md` 的来源编号。依据不足时继续研究或明确标记待核实，不得用流畅叙述掩盖事实缺口。

每页讲稿至少包含：

| 字段 | 要求 |
|---|---|
| 开场句 | 说明本页为什么值得听 |
| 讲述正文 | 按口语顺序解释观点、事实、故事或案例 |
| 证据引用 | 标注对应的来源编号，不把 URL 混入口语正文 |
| 与画面关系 | 指出讲到哪一句时观众应看哪个人物、设备、场景或示意图 |
| 页面衔接 | 连接上一页并引向下一页 |

讲稿长度服从演讲总时长和本页分配时间。默认不把完整讲稿展示在方案确认界面，但用户要求时必须提供。

---

## 3. 从讲稿提取视觉目标

逐句扫描讲稿，提取可视觉化信息：

| 视觉类型 | 讲稿内容 | 固定获取路线 |
|---|---|---|
| `real-evidence` | 具体人物、设备、产品、机构、地点、历史事件 | 用户图片 → 网络真实图片 → 搜索失败记录；不得用 AI 或示意图替代证据 |
| `real-scene` | 课堂、科研、协作、校园活动、生活动作和社会情境 | 用户图片 → 网络真实图片 → 搜索失败记录 → 非证据性场景才可用 AI 图片 |
| `abstract` | 流程、时间线、对比、系统结构、机制和数据关系 | 项目原创 SVG；需要真实背景时另建 `real-scene` 目标 |
| `mixed` | 同一页同时需要现实场景和抽象解释 | 拆成至少一个真实图片目标和一个原创 SVG 目标，组合使用 |

**Hard rule — per-target routing**：逐个视觉目标分配类型，不得给整套 PPT 或整页统一标记“全原创 SVG”。讲稿中出现现实人物、设备、事件或可拍摄场景时，必须建立对应的 `real-evidence` 或 `real-scene` 目标。

**Forbidden — SVG bypass**：不得用原创 SVG、图标拼贴或抽象卡片替代尚未搜索的真实照片目标。原创 SVG 只直接满足 `abstract` 目标。

---

## 4. 生成精确搜索词

为每个视觉目标组合讲稿中的具体实体与语境，不只使用页面标题或页面目的。

```text
人物/设备正式名称 + photo + 必要语境
事件/地点名称 + photo + 必要年份
产品名称 + 使用场景 + photo
```

最多生成 2 条互补 query；优先实体全称与一个常用别名，不为追求官方、档案或精确许可证来源不断追加域名限定。若讲稿提到“Norman Holter 的早期便携心电设备”，搜索词应包含姓名、设备类型和必要年代，而不是只搜“可穿戴设备历史”。

---

## 5. 搜索与候选筛选

将每个真实图片目标编译成 slot，再由内置 Visual Asset Pipeline 按以下顺序执行：

```text
deck theme → slide topic → slot purpose
→ source policy selection → query generation → search scope
→ structured retrieval → local validation → analysis → ranking → selection
```

profile 配置位于 `source-policy-profiles.yaml`，只决定 preferred/excluded domains、时效、署名、排序权重与 AI fallback。所有外部照片都走同一 `presentation-grade` 审核：文件可读、分辨率够用、来源页不是明显垃圾或无关、title/caption/context/metadata 与 slot 基本相关、无明确矛盾、无明确禁止使用。官方或机构来源只加排序分，不是准入条件。

**Mandatory — real-image search evidence**：每个 `real-evidence` 和 `real-scene` 目标至少记录一个实际执行的搜索词和一个候选 URL，或记录多个查询均无结果的明确失败说明。只有计划中的关键词、没有候选/失败结果，视为未执行搜索。

**Missing suitable image**：在 2 条 query、5 个候选、2 次下载和 45 秒 deadline 内没有基本合格照片时保留缺失状态或调整讲述；`real-scene` 可按 profile 生成明确标为示意性的 AI 图片。不得跨大量官方网站或档案馆继续搜索，也不得用语义模糊的装饰图假装成功。

---

## 6. 搜索记录格式

Agent 只负责编写视觉语义要求；`visual_asset_cli.py` 对所有外部照片统一回写 `verification_risk: presentation-grade` 与 `verification_mode: presentation`，同时自动写 capability preflight、`asset-manifest.json`、retrieval state 和 Markdown 视图。旧 manifest 的严格风险、provenance 或 VLM 字段只作兼容记录，不得改变运行时结果。不得伪造搜索记录或调用第三方图片 Skill。

每个必需图片位同时写入 `required_subject`、`required_asset_type`、`required_relationship`、`forbidden_asset_types` 和 `authenticity_requirement`；需要中英文消歧时写入 `entity_aliases`。这些字段用于拒绝明显错人、错对象、错事件和错误图片类型，不要求证明精确拍摄日期、档案编号或最高等级历史绑定。

⛔ **BLOCKING — visual asset gate**：存在以下任一情况时，不得调用 `workflow_state_cli.py close <project> visual_assets`，不得进入 Stage 8：

- 讲稿中的现实实体或场景没有对应视觉目标；
- `real-evidence` 或 `real-scene` 目标没有搜索词，也没有实际候选或明确失败记录；
- `real-evidence` 被 AI 图片或原创 SVG 冒充；
- `mixed` 页面只准备了原创 SVG，没有真实图片目标的搜索记录。

**Validation**：先运行 `python scripts/visual_asset_cli.py preflight <project_path> --host-native-vision <available|unavailable|unknown>`，再运行 `python scripts/visual_asset_cli.py validate-manifest <project_path>`；每个最终网络图片必须能从 `asset-manifest.json` 追溯到 selected asset ID、query、来源页、原图 URL、domain、本地文件、author/credit（如有）、license 或 `license_status: unknown`、verification method/confidence、显示署名与 license obligation。完整记录实际取得的 provenance，但不得要求 provider record、档案编号或 exact historical binding 才能通过。

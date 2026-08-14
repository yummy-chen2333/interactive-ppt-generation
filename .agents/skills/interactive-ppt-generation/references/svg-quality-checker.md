# SVG 质量检查规范

## 1. 读取时机

在完整 `svg_output/` 已生成、即将进入最终导出时读取本文。修改任何页面 SVG 后，重新执行本阶段。

本阶段只判断项目 SVG 是否满足转换契约，不修改页面内容、素材或排版。

## 2. 输入与输出

| 类型 | 路径 | 要求 |
|---|---|---|
| 页面源文件 | `<project>/svg_output/*.svg` | 每页一个完整 SVG，文件顺序与页码一致 |
| 导出锁 | `<project>/spec_lock.md` | 包含画布、完整具名 typography tokens、颜色和 `pptx_structure.mode` |
| 素材 | 页面 SVG 引用的本地文件 | 路径可解析，文件真实存在 |
| 最终报告 | `<project>/validation/svg_quality_report.json` | 由 `--stage final --json` 写入 |

最终报告记录被检查文件的清单与 SHA-256 来源指纹。转换器只把与当前 `svg_output/` 指纹一致的最终报告视为有效质量门。

## 3. 执行命令

在 Skill 根目录执行：

```text
python scripts/svg_quality_checker.py <project_path> --stage final --json
```

需要锁定画布比例时追加 `--format ppt169` 或 `--format ppt43`。统一后端入口会自动运行上述最终检查：

```text
python scripts/run_backend.py <project_path> --title <title> --changed-pages <pages>
```

仅在制作第一张原型页时使用 `--stage first-page`。它允许页码清单尚未完成，并写入独立的首屏报告；不得把它当作发布质量门。

## 4. 检查范围

检查器至少验证以下契约：

| 类别 | 检查目标 |
|---|---|
| 文件与画布 | XML 可解析、`viewBox` 合法、画布比例匹配、最终页清单完整 |
| 转换兼容性 | 禁用元素、禁用属性、浏览器专用效果和不受支持的颜色写法 |
| 文字结构 | 换行、`text`/`tspan`、字号、字体与模块文本边界 |
| 几何边界 | 页面元素、语义模块和 `data-pptx-bounds` 未越界或失真 |
| 资源引用 | 图片、图标和静态 `<use>` 的目标可以解析 |
| Stage 7 图片继承 | `presentation-verified` 的同一文件位于 manifest 指定页面，文件存在且非空 |
| 导出契约 | `spec_lock.md` 与页面画布、页码和 PPTX 结构模式一致 |

字号检查与 Stage 8 使用同一 contract：实际结构字号应匹配具名 token；Checker 保留 `±2px` 容错，并允许未声明字号在整套最多出现 2 次。第 3 次起视为上游漏网错误，而不是 Checker 独有的隐藏设计规则。

图片检查只消费 Stage 7 manifest 的 canonical 决策：验证所用文件与 selected asset 相同、进入声明页面、文件存在且非空，以及 `full-credit`/`compact-source` 的完整 `display_attribution` 已显示；`provenance-only`/`none` 不要求可见署名。Checker 不判断来源是否足够官方、人物身份置信度、历史事件精确绑定、是否使用 VLM 或 provenance 是否达到档案级，也不重新提高 `verification_risk`。

SVG 的具体可用语法由 [`svg-authoring.md`](./svg-authoring.md) 负责；本文不重复元素级写法。

Checker 会估算文字是否越出根级模块的 `data-pptx-bounds`，但不会证明所有文字和图片都没有语义上不合理的碰撞。必须同时执行 [`layout-review.md`](./layout-review.md) 的预览检查。

## 5. 通过条件

- **Error**：阻断导出。修复对应 SVG、素材或上游制作方案后，重新执行最终检查。
- **Warning**：不改变命令的成功退出码，但必须在交付前判断是否影响视觉、字体或外部资源可移植性。
- **Passed**：最终报告存在、检查目标为完整项目、阻断错误为零，且报告来源指纹与当前 `svg_output/` 一致。

不得删除报告、改写退出码或跳过错误后继续发布。若 SVG 在检查后又被修改，旧报告立即失效，必须重跑。

## 6. 修复所有权

| 问题 | 返回位置 |
|---|---|
| 标签、属性、坐标或文本结构错误 | 对应 `svg_output/P<NN>.svg` |
| 图片缺失或路径错误 | 对应页面素材记录与 SVG 引用 |
| 画布、页码或结构模式冲突 | `spec_lock.md` 与受影响页面 |
| 页面内容本身错误 | Stage 8 逐页制作方案，再重新生成 SVG |
| 文字越界、进入图片或段落粘连 | `slide-copy.md`、施工图与受影响 SVG |

不得直接修改 `svg_final/` 解决质量问题；它是下一阶段的派生预览。

实现来源与许可见 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。

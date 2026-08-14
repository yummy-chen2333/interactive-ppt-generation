# PPTX 导出与版本发布规范

## 1. 读取时机

执行 SVG→DrawingML/PPTX 转换与版本发布前读取本文。每次用户修改后重新导出时再次应用本文。

本阶段负责验证转换结果、发布不可覆盖版本，并向用户返回唯一交付路径。

## 2. 标准产物

| 产物 | 路径 | 用途 |
|---|---|---|
| 转换临时文件 | `<project>/exports/_conversion_<timestamp>.pptx` | 本轮构建中间物，不交付 |
| Postflight 报告 | `<project>/validation/_conversion_<timestamp>.report.json` | PPTX 包与质量门审计 |
| 已发布版本 | `<project>/exports/V<NNN>_<title>_<timestamp>.pptx` | 唯一可交付 PPTX |
| 最新指针 | `<project>/exports/latest.json` | 本轮交付的唯一事实来源 |
| 发布历史 | `<project>/exports/publish_history.jsonl` | 追加式版本记录 |

不得覆盖上一版 PPTX，也不得使用 PowerPoint“最近使用”、固定同名文件或旧窗口判断本轮结果。

## 3. 统一执行命令

在 Skill 根目录运行：

```text
python scripts/run_backend.py <project_path> --title <title> --changed-pages <pages>
```

`--changed-pages` 接受 `11`、`3,7-9` 等页码表达。未修改现有版本而是首次生成时可以留空。

统一入口严格串行执行：

```text
最终 SVG 质量检查
→ 生成 svg_final 预览
→ SVG 转 DrawingML/PPTX 临时文件
→ PPTX postflight
→ 发布唯一版本
→ 写入 latest.json 与 publish_history.jsonl
→ 从 latest.json 运行 final_acceptance_validator.py
→ PowerPoint 渲染、备注/图片/布局验收
→ 原子关闭 final_validation Gate
```

任一步非零退出都停止后续步骤，不得宣布完成。

`narrative/speaker-notes.md` 是逐页讲稿唯一真相源。转换器直接按 `## P01`、`## P02` 读取并写入 PPTX notesSlides；不得另建 `notes/*.md` 作为第二份真相。

最终验收渲染在 Windows 上需要已安装 Microsoft PowerPoint；也可通过 `POWERPOINT_EXE` 指向 `POWERPNT.EXE`。该能力不可用时必须失败关闭 `final_validation` Gate，不得跳过渲染后宣称项目完成。

## 4. Postflight 判定

转换器在发布前至少检查：

| 类别 | 判定 |
|---|---|
| PPTX 包 | ZIP 可读取、内部关系可构建、幻灯片数量正确 |
| 质量门 | 最终质量报告存在，来源指纹与本轮 SVG 一致 |
| 资源 | 外部图片引用、未解析模板标记和媒体类型被记录 |
| 字体 | 字体栈可移植性问题被记录为警告 |
| 结构 | `flat` 或已声明结构与最终包一致 |

Postflight 失败时不发布。警告不会自动等于失败，但交付说明必须准确反映仍存在的字体或外部资源风险。

## 5. 不可覆盖发布

`publish_version.py` 必须完成以下动作：

1. 验证临时文件是包含至少一页的 PPTX ZIP 包。
2. 验证 `changed_pages` 未超过实际页数。
3. 计算下一个 `V<NNN>`，生成唯一文件名并拒绝覆盖。
4. 复制临时文件，比较复制前后的 SHA-256。
5. 写入 `latest.json`，并向 `publish_history.jsonl` 追加同一回执。

只有 `latest.json` 中的绝对 `path` 指向本轮交付物。临时文件和历史版本都不能代替该指针。

## 6. 发布后验证

发布成功后：

1. 读取 `exports/latest.json`。
2. 按其中的绝对路径重新打开、解析或渲染 PPTX。
3. 核对 `slide_count`、`changed_pages`、`sha256` 与磁盘文件。
4. 重点检查本轮修改页，确认内容、图片和排版已经更新。
5. 若看到旧内容，先确认打开路径与 SHA-256；不要再次打开同名旧文件。

验证失败时保留已发布版本作为审计记录，但不得交付；修复源 SVG 后发布下一个新版本。

## 7. 用户交付回执

最终回复至少提供：

- 版本号与准确文件名；
- `latest.json` 记录的绝对路径；
- SHA-256；
- 幻灯片总数；
- 本轮修改页码；
- postflight 状态与仍需说明的警告。

实现来源与许可见 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)；不可覆盖版本发布流程由本项目独立实现。

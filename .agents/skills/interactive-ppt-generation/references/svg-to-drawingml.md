# SVG 转 DrawingML 规范

## 1. 读取时机

最终质量检查通过且 `svg_final/` 预览已生成后、执行原生 PPTX 转换前读取本文。

本阶段只把已确认的页面 SVG 编译为 PowerPoint 对象，不重新规划内容、选择模板、获取素材或调整设计。

## 2. 转换输入契约

| 输入 | 要求 |
|---|---|
| `<project>/svg_output/*.svg` | 唯一原生转换源；每个文件包含一整页可见设计 |
| `<project>/spec_lock.md` | 声明画布、主题字段与 `pptx_structure.mode` |
| `<project>/validation/svg_quality_report.json` | 与当前 SVG 来源指纹一致的最终检查报告 |
| 页面素材 | 所有本地引用可解析，或已使用合法 Data URI |

不得使用 `svg_final/` 进行发布转换。转换器可以在内存中复用同类预处理逻辑，但页面所有权仍属于 `svg_output/`。

## 3. PPTX 结构模式

本 Skill 第一版默认使用平面结构：

```text
## pptx_structure
- mode: flat
```

参考 PPT 只用于学习背景、字体、颜色与版式时，仍使用 `flat`，不要因为存在参考文件就切换为 `structured`。

只有项目已经具备完整且经过验证的 Master、Layout、页面映射和结构元数据时才可使用 `structured`。不得在转换阶段临时创建、猜测或补齐结构化模板契约。

## 4. 原生对象映射

| SVG 内容 | PPTX 结果 |
|---|---|
| `text`、受支持的 `tspan` | 可编辑文本框、段落与文字运行 |
| `rect`、`circle`、`ellipse`、`line` | 对应的原生 PowerPoint 形状 |
| `polyline`、`polygon`、受支持的 `path` | 原生自由形状或路径几何 |
| `image` | PowerPoint 图片对象；位图不会变成可编辑矢量 |
| 受支持的填充、描边和透明度 | DrawingML 形状样式 |
| 语义 `g` 与元数据 | 用于转换遍历、边界与对象组织；不保证生成一个 PowerPoint 组合对象 |

“可编辑”表示文字、形状和路径进入原生 DrawingML。照片、截图和其他位图仍以图片对象存在；浏览器滤镜、脚本和未支持 SVG 元素不会自动获得等价 PowerPoint 效果。

## 5. 执行命令

独立转换命令：

```text
python scripts/svg_to_pptx.py <project_path> --output <temporary_pptx>
```

需要锁定画布时追加 `--format ppt169` 或 `--format ppt43`。正常使用统一入口，由它为本次构建创建唯一临时文件：

```text
python scripts/run_backend.py <project_path> --title <title> --changed-pages <pages>
```

不要手工指定同名 `editable.pptx` 作为长期输出，也不要把转换临时文件直接交付。

## 6. 严格失败规则

- 遇到无法表示或无法安全保留的可见 SVG 元素时停止转换，不得静默删除。
- `spec_lock.md` 缺失、结构模式未知或画布冲突时停止转换。
- 图片、字体、路径或包关系无法创建时停止转换，并报告具体页面或资源。
- 转换失败时不得覆盖上一版已发布 PPTX。

修复时返回对应 `svg_output/P<NN>.svg`、素材或 `spec_lock.md`，重新执行质量检查、Finalizer 和转换。不得直接修改临时或已发布 PPTX 来掩盖源文件问题。

## 7. 转换完成条件

转换命令成功退出，并产生一个可读取的临时 PPTX 与对应 postflight 报告。该临时文件仍不是交付物；必须继续执行 [`pptx-export.md`](./pptx-export.md) 的版本发布与精确路径验证。

实现来源与许可见 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。

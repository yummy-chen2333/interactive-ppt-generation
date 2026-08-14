# SVG Authoring Contract

在 Stage 9 编写任何页面 SVG 前读取本文件。它定义独立 Skill 可以稳定转换为原生 DrawingML 的最小作者规范。

## 1. Project Contract

- 将逐页源文件放在 `<project>/svg_output/`。
- 将页面命名为可自然排序的文件，例如 `01_cover.svg`、`02_agenda.svg`。
- 在 `<project>/spec_lock.md` 声明画布和导出模式。最小内容：

```text
## canvas
- viewBox: 0 0 1280 720
- format: PPT 16:9

## pptx_structure
- mode: flat
```

- 16:9 使用 `viewBox="0 0 1280 720"`；4:3 使用 `viewBox="0 0 1024 768"`。
- 所有页面必须使用同一个根 `viewBox`。

## 2. Required SVG Form

- 根元素必须是 `<svg xmlns="http://www.w3.org/2000/svg" viewBox="..." data-pptx-page-role="...">`。页面角色只使用 `cover | toc | section | content | ending`，并与页面用途一致。
- SVG 是严格 XML：原样写 Unicode；将 `&`、`<`、`>` 转义为 `&amp;`、`&lt;`、`&gt;`。
- 为需要单独修改或动画的对象设置稳定、唯一的 `id`。
- 使用 `<g>` 表示语义组；不要把整页内容栅格化为一张图片。
- 图片使用 `<image href="../images/file.png" ...>` 或 data URI；本地路径必须能从 SVG 或项目根解析。

## 3. Supported Core Elements

优先使用：

- `rect`、`circle`、`ellipse`、`line`、`polyline`、`polygon`、`path`
- `text`、受控的 `tspan`
- `g`
- `image`
- 受控的本地 `defs`、`linearGradient`、`radialGradient`、`clipPath`、marker 和静态 `<use>`

文字至少明确 `font-family`、`font-size`、`font-weight` 和 `fill`。新建纯色统一使用六位大写十六进制，例如 `#006C39`。

`font-size` 必须使用 production plan 引用的 `spec_lock.md` 具名 typography token 的精确值。仅允许显式 `sparse:<size>` 例外；整套重复超过 2 次的处理必须回到设计系统成为具名角色。

## 4. Forbidden Browser-Only Features

不得使用：

- `<style>`、`class`、外部 CSS、`@font-face`
- `<foreignObject>`、`textPath`
- `<animate*>`、`<set>`、`<script>`、事件属性、`<iframe>`
- CSS blend mode、backdrop filter、任意浏览器脚本交互
- 未经 Checker 支持确认的 SVG 标签、CSS 属性或滤镜

若效果无法映射为 DrawingML，将其预先制作成单独的图片素材，不要依赖浏览器渲染后再转换。

## 5. Geometry and Editability

- 使用绝对坐标完成页面排版。
- 文字、形状和路径保持独立对象；照片作为独立 `<image>`。
- 按标题、段落、要点、图注和来源拆分文字对象；不得把整页正文或演讲稿放入一个 `<text>`。
- 对 manifest 中 `display_attribution_mode = full-credit | compact-source` 的素材，原样渲染 canonical `display_attribution`；只允许 Unicode/空白层面的正常呈现，不重新措辞。`provenance-only | none` 不要求页内来源文字。
- 对 `verification_status = presentation-verified` 的素材只引用 manifest 锁定的同一 `local_path` 并放入声明页面；Stage 9 不重新检查来源权威、人物身份、历史绑定、VLM 或 provenance 等级。
- 不依赖浏览器自动换行；在 SVG 中明确分行和每行位置。
- 使用根级语义 `<g data-pptx-bounds="x y w h">` 声明模块安全区，确保其中的文字估算边界落在安全区内。
- 除非施工图明确声明设计性覆盖并提供可读性处理，否则文字安全区与图片区域不得相交。
- 避免负宽高、非有限数字、百分比画布尺寸和无法解析的单位。
- 不修改 `svg_final/`；所有修订回到 `svg_output/`。

具体的文字容量、图文碰撞和三阶段验收见 [`layout-review.md`](./layout-review.md)。

## 6. Mandatory Gate

每次导出都必须使用：

```text
python scripts/run_backend.py <project_path> --title <title> --changed-pages <pages>
```

Checker 报错时停止，修改对应 `svg_output/*.svg` 后重新执行。警告需要审阅，但只有错误会阻止导出。

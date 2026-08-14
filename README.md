# Interactive PPT Generation Skill

一套交给 Coding Agent 使用的交互式 PPT 生成 Skill。

你不需要学习项目脚本，也不需要自己编排生成流程。准备好 Coding Agent，把本项目交给它，然后用自然语言说出想制作的 PPT 主题即可。后续的需求确认、内容规划、逐页讲稿、图片搜索、版式设计、SVG 制作、质量检查和可编辑 PPTX 导出，都由 Agent 按照项目规范完成。

> **最短上手路径：安装 Agent → 把项目交给 Agent → 让 Agent 读取项目规范 → 说出 PPT 主题。**

## 你会得到什么

- 原生可编辑的 DrawingML/PPTX，而不是把整页做成一张图片。
- 先确认内容、再进入设计和制作，避免未经确认直接生成成品。
- 有研究依据的逐页讲稿，以及由讲稿驱动的图片搜索和上屏文案。
- 每页经过文字溢出、元素碰撞和最终导出检查。
- 每次导出发布为新版本，不覆盖之前的 PPTX。

## 第一次使用

### 第 1 步：选择并安装 Coding Agent

推荐选择下面任意一种 Agent：

| Agent | 使用方式 | 适合谁 |
|---|---|---|
| [Codex](https://openai.com/codex/get-started/)（推荐） | 桌面应用或 CLI | 希望直接把本地文件夹交给 Agent，并由它持续完成多阶段任务的用户 |
| [Claude Code](https://code.claude.com/docs/en/quickstart) | 桌面应用或 CLI | 经常处理长任务，或已经在使用 Claude 的用户 |
| [Cursor](https://cursor.com/download) | 图形化桌面编辑器 | 不熟悉命令行，希望在文件列表和聊天面板中操作的用户 |

> **质量提醒：最终 PPT 的质量与 Agent 所使用的模型能力直接相关。** 本项目可以约束工作流程、确认顺序和质量检查，但内容理解、叙事组织、视觉判断、复杂排版和问题修复仍由模型完成。正式制作时，建议选择 Agent 中能力更强的模型；快速或低成本模型更适合熟悉流程和制作简单测试。如果结果明显不理想，可以先换用更强的模型，再让 Agent 从对应阶段重新生成或修改。

无论选择哪一种，都需要确保它能够：

- 打开并读取本地文件夹；
- 在项目中创建和修改文件；
- 执行 Python 脚本和项目任务；
- 在搜索图片时访问网络。

只在普通网页聊天框中对话、但无法访问本地文件和执行任务的 AI，不能直接运行本项目。

本项目需要 Python 3.10 或更高版本。你不需要提前学习 Python 命令；把项目交给 Agent 后，让它检查环境即可。如果电脑尚未安装 Python，Agent 会根据实际情况告诉你需要完成的操作。

### 第 2 步：把项目交给 Agent

下面三种方式任选一种。第一次使用推荐方式一。

#### 方式一：下载 ZIP（最适合新手）

1. 打开本仓库页面：<https://github.com/yummy-chen2333/interactive-ppt-generation>。
2. 点击 **Code → Download ZIP**。
3. 下载完成后解压 ZIP。
4. 记住解压后的文件夹位置。通过 ZIP 下载时，文件夹名称通常是 `interactive-ppt-generation-main`。

这种方式不需要使用 PowerShell，也不需要安装 Git。

#### 方式二：使用 Git 克隆

如果电脑已经安装 Git，可以打开 PowerShell，执行：

```powershell
git clone https://github.com/yummy-chen2333/interactive-ppt-generation.git
cd interactive-ppt-generation
```

Git 方式更适合以后持续获取项目更新。

#### 方式三：让 Agent 下载项目

如果 Agent 当前已经打开了一个允许写入的文件夹，可以在聊天中告诉它：

```text
请把 https://github.com/yummy-chen2333/interactive-ppt-generation
克隆到当前文件夹。完成后告诉我新项目的位置。
```

下载完成后，让 Agent 切换到或重新打开新得到的 `interactive-ppt-generation` 文件夹。

### 第 3 步：在 Agent 中打开项目并检查环境

这里要区分两个概念：

- **Agent 工作区**：你下载的整个仓库，也就是直接包含 `AGENTS.md` 的文件夹。
- **PPT 项目**：以后每次制作 PPT 时，由 Agent 在仓库的 `projects/` 中自动创建的独立目录。

首次安装时，你只需要打开 **Agent 工作区**。不要先手动创建 PPT 项目，也不要仅仅把下载的仓库当成另一个空项目的参考文件夹，否则 Agent 可能无法自动发现根目录规则和 `.agents/` 中的文件。

#### 使用桌面应用或图形化 Agent

1. 在 Agent 中选择“打开文件夹”“打开项目”或“创建工作区”。
2. 选择刚才下载或克隆的仓库文件夹。ZIP 下载的文件夹通常叫 `interactive-ppt-generation-main`，Git 克隆的文件夹通常叫 `interactive-ppt-generation`。
3. 在这个工作区中新建一个聊天窗口。

不同 Agent 对“项目”或“工作区”的叫法可能不同。关键是：工作区根目录必须就是下载后的仓库根目录。

#### 使用 CLI Agent

CLI 不需要另外创建项目。打开 PowerShell 或终端，进入仓库根目录后，再启动你安装的 CLI Agent。下面以 ZIP 下载到 Windows“下载”文件夹、使用 Codex CLI 为例：

```powershell
cd "C:\Users\你的用户名\Downloads\interactive-ppt-generation-main"
codex
```

如果使用 Git 克隆，或者把文件夹放在了其他位置，请把第一行替换成真实路径；路径中包含空格时保留双引号。如果使用 Claude Code CLI，把第二行的 `codex` 换成 `claude`。

启动后，CLI Agent 会把当前目录作为工作区，因此不需要再在 CLI 中创建一次项目。

#### 检查是否打开正确

Agent 应该能够在当前工作区看到：

```text
AGENTS.md
README.md
.agents/
projects/
```

然后在新聊天窗口发送下面这段检查指令：

```text
请先阅读根目录的 AGENTS.md，以及
.agents/skills/interactive-ppt-generation/SKILL.md。
按照项目说明检查 Python 版本和运行依赖，安装缺失的依赖，
并确认当前环境是否已经可以生成 PPT。
完成后只告诉我检查结果，暂时不要开始制作 PPT。
```

从这里开始，由 Agent 负责读取项目规范、检查 Python、安装依赖并报告准备结果。只有当某一步必须由你本人操作时，Agent 才会请你处理。

### 第 4 步：说出你想制作的 PPT 主题

环境准备完成后，只需要在聊天中描述主题。例如：

```text
我想制作一份关于“大学生如何做好时间管理”的 PPT。
```

这就足够开始了。是否使用参考 PPT、面向什么受众、需要多少页、演讲多长时间、内容如何组织等问题，Agent 会在后续对话中逐项与你确认。你也可以随时补充资料或提出修改，不需要在第一句话里把所有要求一次写完。

在后续对话中，你可以提供或修改：

| 可以提供或修改的内容 | 示例 |
|---|---|
| 参考 PPT | 提供一份喜欢的 PPT，让 Agent 学习其配色、字体和页面组织方式 |
| 受众与使用场景 | 课堂汇报、项目路演、工作总结、公开演讲等 |
| 页数与演讲时长 | 大约 10 页、用于 15 分钟演讲等 |
| 演讲稿或研究资料 | 粘贴文字，或者告诉 Agent 本地文件的位置 |
| 整体结构 | 增加、删除、合并、拆分或调整章节顺序 |
| 每页内容 | 修改观点、讲稿、标题、上屏文字或页面目的 |
| 图片与图形 | 提供自己的图片，或者要求替换 Agent 找到的图片 |
| 视觉与排版 | 调整配色、字体、布局、留白和图文比例 |

Agent 会在关键阶段停下来等待你的明确确认。内容确认完成后，它才会进入图片获取、逐页排版、SVG 制作和 PPTX 导出。

### 第 5 步：获取成品

完成质量检查后，Agent 会告诉你最新 PPTX 的准确位置。成品通常位于本次 PPT 项目的 `exports/` 目录中，并以独立版本号保存。

首次使用成功应同时满足：

- Agent 明确报告环境准备完成；
- 最终 PPTX 已通过项目验收检查；
- 文件能够在 PowerPoint 等兼容软件中打开；
- 文字和图形可以继续编辑。

## 常见问题

### 普通用户需要手动运行 Python 命令吗？

通常不需要。正常使用时，由 Coding Agent 根据项目规范执行环境检查、项目初始化、素材处理和导出。后面的命令只提供给开发者或需要手动排查问题的用户。

### 为什么按照同样流程生成，PPT 质量仍然不同？

不同模型在长内容理解、逻辑规划、审美判断、SVG 排版和多步骤执行方面的能力不同，因此即使使用相同项目和材料，最终结果也可能有明显差异。项目规范能够减少漏步骤和技术错误，但不能让所有模型产生相同质量。重要或正式的 PPT 建议使用当前 Agent 中能力更强的模型，并在每个确认阶段认真提供反馈。

### 使用 CLI 时也要先创建项目吗？

不需要。CLI 只要在仓库根目录中启动，就会把当前目录作为工作区。具体的 PPT 项目会在后续确认主题和受众后，由 Agent 自动创建。

### 可以把仓库作为另一个项目的参考文件夹吗？

不建议。最可靠的方式是直接把仓库根目录作为 Agent 工作区。这样 Agent 才能稳定读取 `AGENTS.md` 和 `.agents/` 中的项目规范。

### 为什么 Agent 不会立刻开始制作页面？

这是预期行为。项目设有多道用户确认门，先锁定内容和素材，再进行设计与制作，避免在错误方向上生成整套 PPT。

### 可以修改已经生成的 PPT 吗？

可以。告诉 Agent 需要修改哪一页的文字、讲稿、图片或排版。Agent 会回到对应阶段修改源文件，再重新生成受影响页面并发布新版本。

### 会覆盖上一版 PPT 吗？

不会。每次导出都会生成新的版本号和文件名。

## 开发者与手动运行

以下内容不是普通用户首次使用的必经步骤。

### 安装依赖

在仓库根目录安装运行依赖：

```text
python -m pip install -r requirements.txt
```

Windows 中若 `python` 命令不可用但 Python Launcher 可用，可以使用 `py -3`。

### 初始化 PPT 项目

主题与受众确认后初始化项目：

```text
python .agents/skills/interactive-ppt-generation/scripts/init_project.py projects/<project-name>
```

页数确认后补齐逐页记录和素材目录：

```text
python .agents/skills/interactive-ppt-generation/scripts/init_project.py projects/<project-name> --slides 15
```

初始化工具只创建缺失内容，不覆盖已有文件。

### PPT 项目目录

```text
narrative/       演讲逻辑、板块、逐页目的和完整讲稿
research/        事实来源、图片搜索词、候选和选择记录
ppt-content/     最终上屏文字、视觉素材、背景和页面结构
production/      逐页组装施工图和三阶段布局验收
spec_lock.md     设计定稿后的后端技术契约
svg_output/      按施工图生成的逐页 SVG
exports/         经过验证并按版本发布的 PPTX
```

具体目录所有权和文件格式以 `.agents/skills/interactive-ppt-generation/references/project-workspace.md` 为准。

### 素材流水线

内容确认后，Agent 根据逐页讲稿填写 `research/visual-assets/visual-requirements.json`，再调用内置的 Visual Asset Pipeline。它负责结构化检索、限时下载、验证、分析、排序、缓存、熔断、best-so-far 和 good-enough early stop。

`ppt-content/visuals/asset-manifest.json` 是图片选择、来源和许可记录的唯一机器真相。

### 后端导出

项目完成 `spec_lock.md` 与 `svg_output/` 后，统一后端会执行 SVG 质量检查、预处理、DrawingML/PPTX 转换、版本发布和最终验收。最终交付版本以项目 `exports/latest.json` 指向的 PPTX 为准，不应直接交付中间转换文件。

### 测试

```text
py -3 -m unittest discover -s .agents/skills/interactive-ppt-generation/tests -v
```

真实四主题验收样例位于 `examples/visual-asset-e2e/`，覆盖科学、历史、产品和地理图片位。

## 项目结构

```text
.agents/skills/interactive-ppt-generation/
├── SKILL.md
├── agents/openai.yaml
├── references/
├── scripts/
├── assets/project-skeleton/
├── requirements.txt
├── LICENSE
└── THIRD_PARTY_NOTICES.md
```

## 致谢

- [PPT Master](https://github.com/hugohe3/ppt-master)：本项目的 SVG 转换后端参考并复用了其中的 MIT 许可代码。固定来源、commit 和许可证说明见 `.agents/skills/interactive-ppt-generation/THIRD_PARTY_NOTICES.md`。
- SenseNova `sn-search-image`、`sn-ppt-standard` 和 `sn-image-base`：为 Visual Asset Pipeline 的研究与实现提供了参考；相关能力已在本仓库中重新实现。

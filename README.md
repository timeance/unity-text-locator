# Translation Skills

![Translation Skills 项目横幅，手持书本的译者，Games / Novels / More](docs/assets/readme_banner.png)

在 Codex 或 Claude Code 中翻译游戏、小说和文档。翻译 Unity 游戏时，用 `unity-text-locator` 找到文本并写回游戏资源。翻译 EPUB、字幕或已有的游戏文本表时，直接用 `ainiee-translate`，由当前 agent 按术语表分批翻译、校验并导出。

## 仓库内容与支持范围

| Skill | 定位 | 核心能力 |
| --- | --- | --- |
| [`unity-text-locator`](unity-text-locator/SKILL.md) | Unity 游戏本地化 | 识别 Mono / IL2CPP 和文本来源，按出现位置提取 CSV，校验后带备份写回。支持 TMP 字体诊断、AutoTranslator 译表导出和二进制补丁打包。 |
| [`ainiee-translate`](ainiee-translate/SKILL.md) | 电子书、文档和游戏文本翻译 | 内置 AiNiee 格式读写模块。支持锁定术语表、先看样稿或逐章确认、断点续翻、润色，以及对照原文检查标签和占位符。 |

`ainiee-translate` 开箱支持的格式：

- **电子书与文档**：`epub`、`txt`、`md`、`docx`、`xlsx`、`pptx`、`csv`、`po`
- **字幕与歌词**：`srt`、`vtt`、`ass`、`lrc`
- **游戏文本与本地化工程**：MTool / ParaTranz / i18next / VNT（`.json`）、Ren'Py 翻译脚本（`.rpy`）、Translator++ 工程或导出表（`.trans` / `.xlsx`）、Wolf RPG 翻译表（`.xlsx`），以及现有 AiNiee 工程缓存（`AinieeCacheData.json`）
- **Unity 游戏**：通过 `unity-text-locator` 桥接至 `ainiee-translate` 完成全自动翻译

PDF 和依赖 Windows Office 的格式转换需要另行配置外部 AiNiee 环境（`AINIEE_REPO`）。上表中的 `.docx`、`.xlsx` 和 `.pptx` 由内置模块读写。

## 快速开始

### 1. 安装技能

在 Codex 或 Claude Code 中直接输入：

```text
从 https://github.com/timeance/unity-text-locator.git 拉取并安装 unity-text-locator 和 ainiee-translate，同时安装依赖；如果本机没有 Git，请改用 ZIP 下载。
```

- 仓库地址：[timeance/unity-text-locator.git](https://github.com/timeance/unity-text-locator.git)
- 无 Git 时可直接下载 [ZIP 压缩包](https://github.com/timeance/unity-text-locator/archive/refs/heads/main.zip)

### 2. 按场景直接使用

**场景 A：电子书 / 文档 / 字幕 / 其他游戏翻译（独立使用 `ainiee-translate`）**

```text
用 $ainiee-translate 翻译 <文件或目录路径> 为简体中文；先构建并确认词汇表与前 1 章样稿，再完成整本翻译、verify/scan 质检并导出成品。
```

翻译 MTool 扁平 JSON 字典时（保留左侧原文 key、仅写右侧译文 value）：

```text
用 $ainiee-translate 翻译这个 MTool JSON：<ManualTransFile.json 路径>；按 MTool 规范执行分组翻译、源感知审计和全键验证。
```

续翻或润色已有的 AiNiee 缓存工程：

```text
用 $ainiee-translate 导入我的 AiNiee 工程缓存 <AinieeCacheData.json 路径>，从断点继续翻译并导出成品。
```

**场景 B：Unity 游戏全自动汉化（`unity-text-locator` + `ainiee-translate`）**

```text
汉化游戏：<游戏目录>。用 $unity-text-locator Full-Automatic 串联 $ainiee-translate；先确认样稿，再完成回写、字体和运行时验证。允许子代理。
```

完全无人值守时，将“先确认样稿”替换为“采用模式 C，不中途确认”。

**场景 C：Unity 游戏半自动汉化（仅提取与写回，译文在外部完成）**

```text
提取游戏文本：<游戏目录>。用 $unity-text-locator Semi-Automatic 生成原文 CSV 和 manifest；等待我提供 *_translation.csv 后，再校验、回写、处理字体并做运行时验证。
```

### 3. 手动安装与环境依赖

将所需 skill 目录复制到对应 Agent 的 skills 目录（Codex 默认为 `~/.codex/skills`，Claude Code 默认为 `~/.claude/skills`）：

```powershell
$repo = "path\to\unity-text-locator"
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$skills = Join-Path $codexHome "skills"
New-Item -ItemType Directory -Force -Path $skills | Out-Null
Copy-Item (Join-Path $repo "unity-text-locator") $skills -Recurse -Force
Copy-Item (Join-Path $repo "ainiee-translate") $skills -Recurse -Force
```

为 `ainiee-translate` 创建独立的 Python 3.12+ 虚拟环境并安装依赖：

```powershell
py -3.12 -m venv "$HOME\.venvs\ainiee-translate"
& "$HOME\.venvs\ainiee-translate\Scripts\python.exe" -m pip install -r "$repo\ainiee-translate\requirements.txt"
```

如需运行 Unity 资产扫描、写回与字体检查，另行安装 Unity 依赖（`UnityPy`）：

```powershell
py -3.12 -m pip install -r "$repo\unity-text-locator\requirements.txt"
```

Unity 桥接所需的环境变量和字体安装示例见[安装说明](docs/install.md)。

## 核心工作流

### 通用翻译流程（`ainiee-translate`）

```text
解析源文件（或导入 AiNiee 缓存）
→ 构建并锁定词汇表（可选加载可复用风格模块 / 自定义提示词）
→ Agent 分批或子代理并发翻译（模式 A 抽样 / B 逐章 / C 全自动）
→ 可选润色 pass（TRANSLATED → POLISHED）
→ verify 规则校验 + scan 盲区发现 + audit_translation 结构审计
→ 按原格式导出译文，支持双语输出的格式另生成对照版
```

### Unity 本地化流程（`unity-text-locator`）

```text
扫描 Unity 版本、资源结构与 Mono / IL2CPP 运行时
→ 提取按出现位置映射的单列原文 CSV 与 manifest
→ 外部翻译，或桥接为 AiNiee cache 交由 ainiee-translate 翻译
→ 校验行数、占位符、标签与源文件 SHA-256
→ dry-run 预检 → 带回滚备份写回
→ TMP 字体诊断与最小界面 runtime canary
→ 残留文本审计与 SHA-256 二进制补丁打包
```

具体适配器由 skill 根据目标游戏实际结构自动选择，不预设单一资源布局。

## TMP 字体诊断

仓库不捆绑或指定默认字体二进制。字体需由使用者合法获取并针对目标游戏验证：

```powershell
python unity-text-locator\scripts\inspect_tmp_font_bundle.py path\to\font.bundle --translation-root path\to\translation-output
```

排查时重点确认：

- 字体包内部名称、TMP FontAsset 身份、字形覆盖率与目标 Unity 版本兼容性；
- 显示异常属于缺字方框（tofu），还是 atlas / 材质 / 采样不兼容导致的碎框；
- 目标游戏是 Mono 还是 IL2CPP：兼容的 Mono 游戏优先用自带的 app-local 运行时 fallback 注入，IL2CPP 不可套用 Mono 注入方案；
- 先在单一字体、单一界面完成 runtime canary，再决定是否大范围替换。

详细替换流程与字体发布规范见 [font-asset-replacement.md](unity-text-locator/references/font-asset-replacement.md) 与 [docs/release-assets.md](docs/release-assets.md)。

## 仓库边界与许可

[独立项目 Logo（PNG）](docs/assets/logo_main.png)

- 不提交游戏原文件、翻译产物、备份、日志、凭证、字体二进制或本机专用路径；`.gitignore` 默认排除常见字体与本地缓存。
- GitHub Releases 仅用于版本说明及合规资产发布，不重新分发授权不明的字体。
- 本仓库采用 [GNU AGPL-3.0-only](LICENSE) 许可。`ainiee-translate/scripts/ainiee_translate/_vendor/` 包含改编自 [NEKOparapa/AiNiee](https://github.com/NEKOparapa/AiNiee) 的代码，上游来源与修改说明见 [NOTICE](NOTICE)。

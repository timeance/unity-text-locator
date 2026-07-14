# unity-text-locator

面向 Unity 游戏本地化的可移植 Codex skills 仓库。

仓库包含：

- `unity-text-locator`：识别 Unity 文本布局，提取和校验单列 CSV，安全写回 Unity 资源，并诊断 TextMeshPro 字体问题。
- `ainiee-translate`：在 coding agent 内执行 AiNiee 风格的术语表、分批翻译、校验和导出流程。

## 安装

将所需 skill 复制到 Codex skills 目录：

```powershell
$repo = "path\to\unity-text-locator"
$skills = Join-Path ($env:CODEX_HOME ?? (Join-Path $HOME ".codex")) "skills"
Copy-Item (Join-Path $repo "unity-text-locator") $skills -Recurse -Force
Copy-Item (Join-Path $repo "ainiee-translate") $skills -Recurse -Force
```

使用全自动翻译前，另行准备 `ainiee-translate` 的 Python 环境：

```powershell
py -3.12 -m venv "$HOME\.venvs\ainiee-translate"
& "$HOME\.venvs\ainiee-translate\Scripts\python.exe" -m pip install -r "$repo\ainiee-translate\requirements.txt"
```

## 工作流

```text
扫描 Unity 结构和运行时
→ 提取按项目命名的单列原文 CSV
→ 外部翻译，或转换为 AiNiee cache 交给 agent 翻译
→ 校验行数、占位符、标签和源文件哈希
→ dry-run 写回
→ 应用写回并保留回滚备份
→ 检查 TMP 字体包并做最小界面 canary
→ 运行时巡查和补丁打包
```

详细流程由 skill 根据游戏实际结构选择；不要假设所有 Unity 游戏使用相同的资源布局。

## 建议提示词

需要让 Codex 端到端处理 Unity 游戏翻译项目时，可以使用下面的提示词，并把 `xxx` 替换为游戏工作目录或根目录：

```text
请翻译工作目录里的游戏：xxx。

先使用 skill：unity-text-locator 检查 Unity 游戏结构，定位实际显示文本源，生成按项目命名的一列原文 CSV 和 manifest；如果发现多个文本源，请先报告候选源和推荐适配器。

然后使用 skill：ainiee-translate 进行全自动翻译。翻译时保留 Unity/TMP 标签、占位符、换行标记和 manifest 行对齐关系。

最后把翻译结果转回 *_translation.csv，运行 CSV 结构校验和 dry-run 写回预检；只有校验通过后再写回 Unity 资源。写回前备份，写回后做运行时、字体和残留检查，并报告校验结果、写回数量、跳过行、备份路径和未覆盖文本源。
```

## TMP 字体诊断

仓库不捆绑或指定默认字体二进制。字体必须由使用者合法取得，并针对目标游戏验证：

```powershell
python unity-text-locator\scripts\inspect_tmp_font_bundle.py path\to\font.bundle --translation-csv path\to\game_translation.csv
```

检查结果应同时关注：

- 字体包内部名称、TMP FontAsset 身份和字形覆盖；
- 字体包构建 Unity 版本与目标游戏版本是否兼容；
- 方框来自真正缺字，还是 atlas、material 或采样不兼容；
- 游戏是 Mono 还是 IL2CPP，运行时 fallback 能否安全使用。

不要仅凭“中文覆盖率高”就全局替换。先在单一字体、单一界面做 runtime canary；对话框正常而菜单出现碎框时，优先检查 atlas 与材质兼容，而不是继续堆叠 fallback 字体。

字体替换说明见 [font-asset-replacement.md](unity-text-locator/references/font-asset-replacement.md)。

## 安全与发布边界

- 不提交游戏文件、翻译产物、备份、日志、报告、凭证或机器专用路径。
- 不提交字体二进制；`.gitignore` 默认排除常见字体格式和本地缓存。
- 不写死用户名、盘符、Unity 项目 PathID、项目哈希或系统安装位置。
- Mono fallback 需要目标游戏可加载兼容程序集；IL2CPP 不应套用 Mono 注入方案。
- GitHub Releases 用于版本说明，不用于重新分发来源或授权不明确的字体。

## 许可

本仓库使用 [GNU AGPL-3.0-only](LICENSE)。

`ainiee-translate/scripts/ainiee_translate/_vendor/` 包含来自 [NEKOparapa/AiNiee](https://github.com/NEKOparapa/AiNiee) 的适配代码；来源与修改说明见 [NOTICE](NOTICE) 及 vendored notice。

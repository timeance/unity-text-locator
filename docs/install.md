# 安装

## 仓库结构

```text
unity-text-locator/
  unity-text-locator/
  ainiee-translate/
  docs/
```

如果需要全自动 Unity 翻译，请同时安装两个 skill 目录。
如果只需要半自动的提取、校验、写回流程，只安装 `unity-text-locator` 即可。

Unity 资源脚本依赖 Python 3.12 和 `UnityPy`：

```powershell
$repo = "path\to\unity-text-locator"
py -3.12 -m pip install -r "$repo\unity-text-locator\requirements.txt"
```

## 安装到 Codex

PowerShell:

```powershell
$repo = "path\to\unity-text-locator"
$codexSkills = "$env:USERPROFILE\.codex\skills"

Copy-Item "$repo\unity-text-locator" $codexSkills -Recurse -Force
Copy-Item "$repo\ainiee-translate" $codexSkills -Recurse -Force
```

## AiNiee Python 环境

全自动模式需要为 `ainiee-translate` 准备运行依赖。

```powershell
$repo = "path\to\unity-text-locator"
py -3.12 -m venv "$env:USERPROFILE\.venvs\ainiee-translate"
& "$env:USERPROFILE\.venvs\ainiee-translate\Scripts\python.exe" -m pip install -r "$repo\ainiee-translate\requirements.txt"
```

运行全自动桥接命令前，在当前终端设置：

```powershell
$env:AINIEE_SKILL_DIR = "$repo\ainiee-translate"
$env:AINIEE_PY = "$env:USERPROFILE\.venvs\ainiee-translate\Scripts\python.exe"
```

`AINIEE_REPO` 是可选项，只在 vendored `ainiee-translate` reader 尚不支持某些格式时使用。

## 字体

不要从本仓库下载或发布 `arialuni_sdf_u2019`；Arial Unicode MS 及其 SDF 衍生资产需要单独、明确的再分发授权。

Mono/TMP 游戏优先使用自己合法取得的 Noto CJK/SC、TTF、OTF 或 TTC 文件作为 app-local 运行时 fallback：

```powershell
python unity-text-locator\scripts\install_tmp_chinese_font_fix.py "path\to\GameFolder" `
  --runtime-font-file "path\to\NotoSansCJKsc-Regular.otf" `
  --out-dir "path\to\GameFolder\_translation\unity-text-report\font-fix" `
  --dry-run
```

检查 dry-run 报告后，移除 `--dry-run` 应用。安装器把字体复制到游戏 `_Data/ChineseFontFixer/Fonts/` 私有目录，与 DLL 和注入 JSON 一起备份、提交和失败回滚。报告会记录字体来源、目标、大小与 SHA-256。

静态 TMP font bundle 不是通用替代品。只有目标 Unity Editor 和 TMP package 版本精确匹配时才可作为候选，并且必须先做单字体、单界面 canary。发布 Noto 字体包的许可和 manifest 要求见 [release-assets.md](release-assets.md)。

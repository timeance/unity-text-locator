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

## 字体 Release 资产

安装 `unity-text-locator` skill 后，把 release 里的 `arialuni_sdf_u2019` 下载到 skill 的 `assets` 目录：

```powershell
$skillDir = "$env:USERPROFILE\.codex\skills\unity-text-locator"
New-Item -ItemType Directory "$skillDir\assets" -Force
Invoke-WebRequest `
  "https://github.com/timeance/unity-text-locator/releases/latest/download/arialuni_sdf_u2019" `
  -OutFile "$skillDir\assets\arialuni_sdf_u2019"
```

校验下载结果：

```powershell
Get-FileHash "$skillDir\assets\arialuni_sdf_u2019" -Algorithm SHA256
```

不要把字体资产提交进 git；它只需要留在本机的 skill 目录里。

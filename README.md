# unity-text-locator

用于 Unity 游戏翻译工作流的 Codex skills 仓库。

本仓库包含两个 skill：

- `unity-text-locator`：检查 Unity 游戏结构，定位文本源，导出行映射 CSV，校验译文，安全写回 Unity 资源，处理 TMP 字体替换，并构建可验证的补丁包。
- `ainiee-translate`：用于全自动流程的 AiNiee 风格 agent 翻译管线。该 skill vendors 并适配了 [NEKOparapa/AiNiee](https://github.com/NEKOparapa/AiNiee) 的 reader/export/cache 组件。

## 工作模式

### 半自动翻译

适合由人工译者、AiNiee App 或其他翻译流程产出译文 CSV 的场景。

```text
Unity 扫描/提取
-> 按项目命名的一列原文 CSV
-> 外部翻译为 *_translation.csv
-> CSV 结构校验
-> dry-run 写回预检
-> 应用写回
-> 按需用 arialuni_sdf_u2019 替换 TMP 字体
-> 运行时检查并构建补丁包
```

### 全自动翻译

适合希望 Codex 主动调用本仓库内置 `ainiee-translate` skill 完成翻译的场景。

```text
Unity 扫描/提取
-> 按项目命名的一列原文 CSV
-> 将 Unity CSV 转为 AiNiee cache.json
-> agent 按 ainiee-translate 规则批量翻译
-> 将 cache.json 转回 *_translation.csv
-> CSV 结构校验
-> dry-run 写回预检
-> 应用写回
-> 按需用 arialuni_sdf_u2019 替换 TMP 字体
-> 运行时检查并构建补丁包
```

全自动只自动化“翻译阶段”。Unity 的行对齐、占位符、标签、源文件哈希、dry-run 写回、备份和运行时检查仍然是最终写回前的硬门禁。

## 字体资产

`arialuni_sdf_u2019` 不放在 git 里，文件太大，也不适合混进源码历史。需要用到字体替换时，从本仓库的 GitHub Release 下载它，然后放进已安装的 `unity-text-locator` skill 文件夹：

```powershell
$skillDir = "$env:USERPROFILE\.codex\skills\unity-text-locator"
New-Item -ItemType Directory "$skillDir\assets" -Force
Invoke-WebRequest `
  "https://github.com/timeance/unity-text-locator/releases/latest/download/arialuni_sdf_u2019" `
  -OutFile "$skillDir\assets\arialuni_sdf_u2019"
```

下载后可以顺手校验一下：

```powershell
Get-FileHash "$skillDir\assets\arialuni_sdf_u2019" -Algorithm SHA256
```

当前 release 附件的参考信息：

```text
name: arialuni_sdf_u2019
size: 30986431 bytes
sha256: 11B47CAE3262648DD9C8B8A29DC25D04309A18790E4130E94FD230791E55C037
```

字体文件放好之后，翻译 skill 在处理 TMP 字体替换时会按这个本地文件来做。更多细节见 [docs/release-assets.md](docs/release-assets.md) 和 [unity-text-locator/references/font-asset-replacement.md](unity-text-locator/references/font-asset-replacement.md)。

## 许可证

本仓库使用 **GNU AGPL-3.0-only**，见 [LICENSE](LICENSE)。

`ainiee-translate/scripts/ainiee_translate/_vendor/` 中包含来自 [NEKOparapa/AiNiee](https://github.com/NEKOparapa/AiNiee) 的 AGPL-3.0 代码，顶层 [NOTICE](NOTICE) 和 vendored notice 中保留了上游来源、commit 与修改说明。

`arialuni_sdf_u2019` 是单独发布的 release 附件，不属于本仓库源码许可证覆盖范围。若你 fork 后重新分发自己的 release，请自行确认该字体资产的授权条件。

## 安装

将两个 skill 目录复制到 Codex skills 目录：

```powershell
$repo = "path\to\unity-text-locator"
Copy-Item "$repo\unity-text-locator" "$env:USERPROFILE\.codex\skills\" -Recurse -Force
Copy-Item "$repo\ainiee-translate" "$env:USERPROFILE\.codex\skills\" -Recurse -Force
```

如果要使用全自动翻译，需要为 `ainiee-translate` 准备 Python 环境：

```powershell
py -3.12 -m venv "$env:USERPROFILE\.venvs\ainiee-translate"
& "$env:USERPROFILE\.venvs\ainiee-translate\Scripts\python.exe" -m pip install -r "$repo\ainiee-translate\requirements.txt"
$env:AINIEE_SKILL_DIR = "$repo\ainiee-translate"
$env:AINIEE_PY = "$env:USERPROFILE\.venvs\ainiee-translate\Scripts\python.exe"
```

## 仓库说明

- 本仓库在 `ainiee-translate/scripts/ainiee_translate/_vendor/` 下包含来自 [NEKOparapa/AiNiee](https://github.com/NEKOparapa/AiNiee) 的适配组件；发布时请保留 vendored notice 和上游署名。
- `CHANGELOG.md` 不放入仓库。公开更新记录使用 GitHub Releases。
- 游戏文件、生成的翻译报告、写回备份不要提交。字体二进制只作为 Release 附件下载到本地 skill 目录。
- 这两个 skill 是可移植源码层。venv、缓存、本地字体路径和具体游戏路径都属于机器本地状态，不应提交。

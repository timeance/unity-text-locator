# Release 资产

## arialuni_sdf_u2019

`arialuni_sdf_u2019` 是给 TMP 字体替换流程使用的本地资产。它不提交到 git，用户从 GitHub Release 下载后放进 `unity-text-locator` skill 的 `assets` 目录。

安装后的推荐路径：

```text
%USERPROFILE%\.codex\skills\unity-text-locator\assets\arialuni_sdf_u2019
```

准备本仓库时使用的本地参考资产信息：

```text
file: arialuni_sdf_u2019
size: 30986431 bytes
sha256: 11B47CAE3262648DD9C8B8A29DC25D04309A18790E4130E94FD230791E55C037
```

release 结构：

```text
Release vX.Y.Z
  unity-text-locator source archive
  arialuni_sdf_u2019
```

下载到 skill 目录后的校验命令：

```powershell
Get-FileHash "$env:USERPROFILE\.codex\skills\unity-text-locator\assets\arialuni_sdf_u2019" -Algorithm SHA256
```

哈希应匹配：

```text
11B47CAE3262648DD9C8B8A29DC25D04309A18790E4130E94FD230791E55C037
```

## 发布策略

- `arialuni_sdf_u2019` 不进入 git 历史。
- release notes 说明它是单独附件，不属于源码许可证覆盖范围。
- 用户下载后放进 `unity-text-locator/assets/`。
- 对无法安全替换 TMP 资产的游戏，运行时字体 fallback 仍作为兼容方案保留。

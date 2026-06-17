# Release 资产

## arialuni_sdf_u2019

`arialuni_sdf_u2019` 是推荐的 TMP 字体替换资产，适用于 TMP font asset 或 bundle 结构与该流程匹配的 Unity 游戏。

该文件应上传到 GitHub Releases，不要提交到 git。

准备本仓库时使用的本地参考资产信息：

```text
file: arialuni_sdf_u2019
size: 30986431 bytes
sha256: 11B47CAE3262648DD9C8B8A29DC25D04309A18790E4130E94FD230791E55C037
```

推荐的 release 结构：

```text
Release vX.Y.Z
  unity-text-locator source archive
  arialuni_sdf_u2019
```

下载后的推荐校验命令：

```powershell
Get-FileHash .\font-assets\arialuni_sdf_u2019 -Algorithm SHA256
```

哈希应匹配：

```text
11B47CAE3262648DD9C8B8A29DC25D04309A18790E4130E94FD230791E55C037
```

## 发布策略

- `arialuni_sdf_u2019` 不进入 git 历史。
- 在 release notes 中说明字体来源与再分发假设。
- 如果无法确认公开再分发授权，不要附加该文件，改为说明用户如何自行提供本地资产。
- 对无法安全替换 TMP 资产的游戏，运行时字体 fallback 仍作为兼容方案保留。

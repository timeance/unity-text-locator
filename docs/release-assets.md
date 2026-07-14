# 字体 Release 资产

字体不是通用的“修复文件”。运行时字体与静态 TMP FontAsset 应分别发布和验证。

## 允许发布的资产

仓库可以发布基于 Noto CJK/SC 制作的字体包，但发布者必须核对所用字体版本的 SIL Open Font License 1.1，并在压缩包中保留许可证、版权声明、来源和修改/子集化说明，同时遵守 Reserved Font Name 等适用条款。不得仅凭字体名称推断许可。

建议结构：

```text
noto-cjk-sc-font-pack-v1.zip
  LICENSES/OFL-1.1.txt
  font-manifest.json
  runtime/NotoSansCJKsc-Regular.otf
  tmp/unity-6000.0.x-tmp-x.y.z/font.bundle
  SHA256SUMS
```

运行时字体供 Mono 注入方案使用，通常比静态 TMP 包通用。静态包必须按精确 Unity Editor 与 TMP package 版本隔离；`NotoSansCJK_sdf_unity6000` 之类的名称只能表示候选构建版本，不能证明与所有 Unity 6 游戏兼容。

## 禁止默认发布的资产

`arialuni_sdf_u2019` 源自 Arial Unicode MS。它是专有字体的衍生资源，转换成 SDF、atlas 或 AssetBundle 不会自动产生再分发权。除非仓库维护者持有并公开记录涵盖该二进制资产的明确再分发授权，否则：

- 不提交到 git；
- 不附加到 GitHub Release；
- 不提供自动下载链接；
- 仅允许用户从其合法取得的本地字体自行生成或指定。

代码仓库的 AGPL-3.0 许可证不覆盖第三方字体。

## `font-manifest.json`

每个发布字体包至少记录：

```json
{
  "schema_version": 1,
  "font_family": "Noto Sans CJK SC",
  "font_version": "verified upstream version",
  "source_url": "official upstream URL",
  "source_sha256": "...",
  "license": "OFL-1.1",
  "modified_or_subsetted": false,
  "unity_editor_version": "6000.0.x",
  "tmp_package_version": "x.y.z",
  "character_set_sha256": "...",
  "atlas": {"width": 4096, "height": 4096, "format": "...", "population_mode": "Static"},
  "files": [{"path": "...", "sha256": "...", "size": 0}]
}
```

运行时字体包可将 Unity/TMP/atlas 字段设为 `null`；静态 TMP 包不得省略这些字段。

## 使用门槛

1. 先检测目标游戏 Unity 版本、Mono/IL2CPP、TMP 版本和实际缺字集合。
2. Mono 注入可用时，优先使用 `--runtime-font-file` 安装 app-local Noto 字体。
3. 静态 TMP 替换只允许精确版本候选，并先做单字体、单界面 canary。
4. 校验 manifest 与每个文件的 SHA-256；哈希只证明身份，不证明授权或运行时兼容。
5. 出现 atlas 碎片、材质异常、版本未知或加载错误时立即回滚。

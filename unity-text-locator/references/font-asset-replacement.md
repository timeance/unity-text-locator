# Font Asset Replacement

Preferred published font workflow: ask the user to download the GitHub Release asset `arialuni_sdf_u2019` into the installed `unity-text-locator/assets/` directory, then replace the reviewed target TMP font asset/bundle with that local file.

## Release Asset

Expected release asset:

```text
name: arialuni_sdf_u2019
size: 30986431 bytes
sha256: 11B47CAE3262648DD9C8B8A29DC25D04309A18790E4130E94FD230791E55C037
```

The asset is intentionally not committed to git. Download it from GitHub Releases into the installed skill folder:

```text
%USERPROFILE%\.codex\skills\unity-text-locator\assets\arialuni_sdf_u2019
```

Verify it before use:

```powershell
Get-FileHash "$env:USERPROFILE\.codex\skills\unity-text-locator\assets\arialuni_sdf_u2019" -Algorithm SHA256
```

## When To Use It

Use direct `arialuni_sdf_u2019` replacement when:

- the game uses TextMeshPro;
- the target font asset or font assetbundle has been identified;
- replacing that asset preserves the game container's loading/integrity rules;
- a backup of the original target file exists;
- runtime smoke testing is possible.

Do not claim the replacement is universal. Addressables, packed bundles, custom loaders, or integrity checks can make direct replacement unsafe.

## Required Procedure

1. Identify the exact TMP font asset/bundle used by visible text.
2. Back up the original target file.
3. Verify the release `arialuni_sdf_u2019` SHA256.
4. Replace only the reviewed target asset/bundle.
5. Launch the game.
6. Confirm Chinese text no longer renders as tofu/boxes.
7. Inspect `Player.log` for asset load errors, missing font errors, and repeated missing glyph errors.
8. Record the target file, backup path, replacement asset hash, and runtime result in the final report.

## Fallback

If direct replacement is not compatible, use the runtime TMP/UGUI fallback workflow in `install_tmp_chinese_font_fix.py` after its dry run passes. Keep the fallback path documented as compatibility work, not the default published font flow.

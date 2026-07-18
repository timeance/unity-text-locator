# Unity Text Location Map

## Common Locations

| Location | What it may contain | First action |
| --- | --- | --- |
| `*_Data/StreamingAssets` | JSON, CSV, XML, TXT, scripts, custom archives, Addressables | Inspect loose text, then catalog/bundles |
| `*_Data/StreamingAssets/aa` | Addressables catalog and bundles | Open `catalog*.json`, map keys to bundles |
| `catalog.json`, `catalog_*.json` | Addressables resource keys, bundle names, sometimes localized labels | Search for Japanese and asset names |
| `resources.assets` | TextAsset, MonoBehaviour, ScriptableObject, UI data | Export with AssetRipper/UABEA/UnityPy |
| `sharedassets*.assets` | Scene/prefab-linked text, TextMeshPro, MonoBehaviour | Export and inspect MonoBehaviours/TextAssets |
| `level*.assets` | Scene text, UI objects, dialogue objects | Export scene objects |
| `.bundle`, `.unity3d`, `.ab`, extensionless bundles | Packed assets and text tables | Detect/load with UnityPy, AssetRipper, or UABEA |
| `*_Data/Managed/*.dll` | Mono hardcoded strings and localization managers | Inspect with ILSpy/dnSpy or strings |
| `GameAssembly.dll` + `global-metadata.dat` | IL2CPP code strings and metadata | Run Il2CppDumper/Cpp2IL |
| `globalgamemanagers`, `boot.config` | Unity version and build config | Use version to select compatible tools |

## Middleware Clues

| Clue | Likely system | Text strategy |
| --- | --- | --- |
| `Fungus`, `Flowchart`, `SayDialog` | Fungus | Export MonoBehaviours and scene objects |
| `.yarn`, `YarnProject`, `YarnSpinner` | Yarn Spinner | Extract Yarn files or TextAssets |
| `.ink`, `Ink`, `InkJSON` | ink | Extract JSON/TextAssets |
| `scenario`, `script`, `message`, `dialog`, `talk`, `quest` | Custom VN/RPG tables | Search loose files and TextAssets first |
| `.lua`, `xlua`, `slua`, `MoonSharp` | Lua scripting | Extract scripts from StreamingAssets/TextAssets |
| `I2Languages`, `I2Loc`, `LanguageSource` | I2 Localization | Export ScriptableObjects/MonoBehaviours |
| `LocalizeStringEvent`, `StringTableCollection` | Unity Localization package | Export string tables and Addressables |
| `TextMeshPro`, `TMP_Text` | TextMeshPro UI text | Inspect prefabs/scenes/MonoBehaviours |
| `DMSL` near `0x06 + uint32 LE + UTF-8` operands | Custom length-prefixed scenario bytecode | Use the fixed-byte DMSL extractor and retain every occurrence/byte budget |
| `bansheegz_database`, `BGDatabase.dll`, `BGRepo` | BansheeGz BGDatabase | Use a game-runtime load/export/save probe; do not raw-patch payload bytes |
| `ES3`, `ES3Defaults`, `SaveFile.es3`, `AutoSave.es3` | Easy Save 3 persistence | Inspect defaults resource and confirm actual save path/timestamps |

## Tool Selection

- AssetRipper: best first broad export when the user wants a browsable project-like dump.
- UABEA: useful for manual inspection/editing of `.assets` and bundles.
- UnityPy: useful when Codex should automate extraction, filtering, or rebuilding supported bundles.
- ILSpy/dnSpy: use for Mono `Assembly-CSharp.dll` strings and localization code paths.
- Il2CppDumper/Cpp2IL: use for IL2CPP `GameAssembly.dll` plus `global-metadata.dat`.
- `strings`/PowerShell byte scans: use only as a quick hint; they miss UTF-16, compressed bundles, and serialized Unity objects.

## Reporting Heuristics

High confidence:
- Readable JSON/CSV/XML/TXT/Yarn/Ink/Lua files with Japanese samples.
- Addressables catalogs referencing scenario/localization assets.
- TextAsset exports with dialogue-like names.

Medium confidence:
- Unity asset containers with Japanese byte samples.
- Bundles near a catalog with names like `localization`, `scenario`, `script`, `message`, `story`, `talk`, `quest`, `ja`, or `jp`.

Low confidence:
- CJK-only samples with no kana.
- Random samples from large binary files without nearby names or catalog evidence.
- Code strings unless the game has very little asset text.

## Pipeline Outputs

Default extraction keeps `extracted_text.csv` as a broad audit output and also
writes occurrence-preserving translator files per Unity source under `text/`:

```csv
original_flat
...
```

For example:

```text
GameTitle_text.csv
GameTitle_text.manifest.json
GameTitle_text_translation.csv
```

Keep each sidecar manifest next to its source CSV. It stores one mapping row per
occurrence, source path, offset/method metadata, and source SHA256. Do not ask
the translator to edit manifests. Do not deduplicate a CSV intended for
row-mapped writeback.

## Translation CSV Contract

When the user returns a translation, expect one translation column aligned with
the associated source file:

```csv
zh_cn
...
```

Use the source CSV plus its manifest as the canonical mapping. Each row maps to
one occurrence, so equal original strings in different runtime roles may be
handled differently.

Recommended behavior:

- Empty `zh_cn`: skip this row and keep the original game text.
- Non-empty `zh_cn`: replace only the occurrence mapped to that row.
- Never change the manifest by hand.
- Normalize the translation CSV with `validate_translation_csv.py` before writeback; the normalized output is UTF-8 with BOM and uses the `zh_cn` header.
- Block placeholder, rich-text tag, sprite tag, and literal newline mismatches
  unless a project-specific verified override is intended.

## Writeback Notes

Use UnityPy for supported Unity serialized assets and bundles. For text extracted
as `unity-len-utf8`, patch inside the serialized object data and let UnityPy
reserialize the file. Old file offsets can move after save, so validate by
searching for translated bytes after the write rather than trusting old offsets.

For raw fallback byte spans, write back only when the UTF-8 translated byte length
does not exceed the original byte span; pad shorter replacements with spaces.

For visible UI missed by scenario extraction, inspect MonoBehaviour TypeTrees for `m_text`, label/caption/title-like fields, and Unity Localization `m_TableData[*].m_Localized`. Preserve serialized-file identity, PathID, a typed field path, source field hash, and duplicate occurrence metadata; PathID alone is not unique across serialized files inside a bundle.

Before writing:

- create timestamped backups under the report `writeback/<source>/backups/` directory, or pass an explicit `--backup-dir` when a project needs a different backup root;
- run a dry run first when possible;
- write a Chinese writeback report listing patched, skipped, and failed rows.

For Addressables localization bundles, reopening a generated bundle with UnityPy
is only a structural check. Preserve compression/integrity metadata where
applicable and require a game-runtime bundle/Addressables load test before
applying the bundle or its catalog changes.

For a binary catalog, derive the lookup key from `AssetBundle.m_Name`, require one plausible record, and audit an exact four-byte CRC field change. Do not infer a record from the external bundle filename, and do not guess the catalog hash or stored bundle size.

## Chinese Font Fix

If translated Chinese renders as square boxes, the likely cause is that the game
uses embedded Japanese pixel fonts or a TMP font asset without Chinese glyphs.

If the serialized translation contains the correct Unicode but the screen shows a different valid Han character, this is not tofu and usually is not a translation error. Trace the visible component to its actual TMP FontAsset and check for a populated dynamic character/glyph table paired with a replaced source Font.

Preferred runtime font files on Windows, in order of availability:

- Microsoft YaHei (`msyh.ttc`/`msyh.ttf`)
- Noto Sans SC, if installed
- SimHei
- SimSun
- DengXian

Preferred fix order:

1. Run `install_tmp_chinese_font_fix.py --dry-run` to confirm Mono/TMP assembly
   and JSON injection compatibility without modifying the game.
2. Add `ChineseFontFixer.dll` as a dynamic TMP fallback while keeping original
   TMP font assets active for glyphs they already provide.
3. Use `--patch-embedded-fonts` only after identifying legacy `Font` assets that
   require replacement; back up every modified JSON, DLL, and asset file.
4. Start the game and require successful initialization in `Player.log` with no
   missing-method or font-face-load errors.

This font fix assumes a Mono Unity build with writable `*_Data/Managed` files.
For IL2CPP builds, use the extraction/writeback parts of the skill, then inspect
the font pipeline separately.

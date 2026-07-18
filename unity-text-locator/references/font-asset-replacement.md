# TMP Font Compatibility And Replacement

Treat every font package as untrusted until its internal Unity version, TMP objects, atlas, and glyph coverage are inspected. A filename such as `arialuni`, `noto`, or `unity6000` is not evidence of the font family or serialization version inside the package.

Keep inspection reports in the game workspace, not in the shared skill repository, because reports echo user-supplied local paths. Do not commit font binaries unless their redistribution license has been reviewed and repository inclusion is intentional.

## Preflight And Usage Tracing

Start from a text component that visibly reproduces the problem. Run `trace_tmp_font_usage.py` to follow its `m_fontAsset` and material PPtrs through External FileIDs/CAB names to the real bundle and PathIDs. Do not infer the "main font" from bundle filenames, object names, character coverage, or discovery order. A title screen, dialogue view, and settings page may use different TMP assets even when their styling looks similar.

Run the read-only inspector before selecting a target or donor:

```bash
python scripts/inspect_tmp_font_bundle.py "FontBundle" \
  --target-asset "GameFolder/Game_Data/sharedassets0.assets" \
  --translation-root "GameFolder/_translation/unity-text-report/validation" \
  --out "CandidateRoot/font_inspection.json"
```

Verify:

- the internal TMP face family, source Font identity, material, and atlas names;
- the serialized Unity version inside the bundle and target asset;
- static/dynamic population mode and existing character/glyph table counts;
- source Font PPtr and any family-identity conflict;
- atlas dimensions, format, stream path, platform blob, and color space;
- coverage of characters actually used by the validated translation;
- the package SHA-256 and size.

Prefer an exact engine version. Treat the same Unity major/minor as a candidate requiring a runtime canary. Treat cross-version raw MonoBehaviour replacement as high risk even if UnityPy can parse both files.

## Diagnose The Symptom First

| Runtime symptom | Likely layer | Required response |
| --- | --- | --- |
| Uniform tofu squares replacing specific characters | Missing glyph or fallback | Measure required-character coverage; add a compatible fallback or choose a broader font |
| Repeated glyph fragments, tiled text, or boxed atlas snippets | GlyphRect/atlas payload mismatch | Roll back immediately; inspect font table, texture payload, stream handling, and platform fields |
| Pink/magenta text or rectangles | Missing/incompatible shader or material | Restore the target shader and validate material properties and atlas reference |
| `corrupted`, `Position out of bounds`, or load failure | Serialization/container mismatch | Roll back; do not continue from the candidate |
| Dialogue is correct but menus are not | Multiple font assets or UI systems | Map each visible component to its actual TMP or legacy font before changing more assets |
| Correct CSV/Unicode displays as another valid CJK character | Stored character/glyph index now points into a different source font | Compare CSV bytes and code points first; treat this as a glyph-index conflict, not a translation error |

Do not call glyph fragments "missing characters." Do not infer runtime texture orientation from exported-image coordinates; Unity runtime rendering is the authority.

## Dynamic TMP Source-Font Trap

Inspect `m_AtlasPopulationMode`, `m_CharacterTable`, `m_GlyphTable`, and `m_SourceFontFile` together. A dynamic TMP FontAsset can still serialize populated character and glyph tables. If its tables are populated, replacing only the referenced TTF/Font object is forbidden: character entries retain old glyph indices, while the new source font can assign those indices to unrelated glyphs. The result can be plausible but wrong Chinese, with one valid Han character consistently rendering as another.

First prove the serialized CSV contains the intended Unicode code point. If it does, preserve the translation and repair the font asset. Do not add character aliases to hide missing donor coverage; aliases deliberately render a different glyph and make coverage reports misleading.

## Choose The Least Invasive Adapter

1. For Mono/TMP games with supported injection tables, prefer the preflighted runtime fallback workflow. It preserves original materials and atlases.
2. For IL2CPP games, do not claim the Mono fallback installer applies. Use a compatible IL2CPP runtime plugin only when its loader and interop generation are independently verified.
3. Use resource-level TMP replacement only when the visible component, exact target objects, container rules, and runtime test path are known.
4. If no compatible package exists, generate a TMP FontAsset with the exact target Unity/TMP version. Treat editor installation as a build-time dependency, not a game runtime dependency.

## Static Resource Replacement Contract

Before writing:

1. Identify the exact visible text component and its FontAsset, Material, and Texture2D PathIDs. Never replace every discovered font as the first experiment.
2. Back up every file that will eventually be applied and record its SHA-256. Build the candidate outside the game directory.
3. Inspect both target and donor and save the reports.
4. Prove required-character coverage against approved translation CSVs. Missing characters and implicit glyph aliases are blocking failures.
5. Preserve the target MonoBehaviour script, object name, FontAsset/Material/Texture PathIDs, and target shader.
6. Copy the donor character table, glyph table, font metrics, matching SDF atlas, and material parameters as one matched set. Never combine a donor table with a target atlas or retain target glyph indices against a donor source font.
7. When materializing a streamed atlas, copy the full payload and all Texture2D fields, including platform blob, format, dimensions, mip settings, preprocessing flags, color space, and stream state. A correct byte count alone is insufficient.
8. Require exact serialized Unity versions by default. A mismatch needs explicit opt-in and produces a canary-only candidate; it is not release evidence.
9. Reopen the candidate with UnityPy and validate names, PathIDs, script/shader identity, FontAsset material/atlas references, glyph/character counts, atlas dimensions, inline payload, and required-character coverage.
10. Apply one-font, one-screen canary first. Do not broaden the replacement until the canary renders correctly.

Use `build_static_tmp_fontasset.py` only after all explicit target PathIDs are known. UnityPy reopening proves structural readability only; it does not prove GPU texture upload, shader compatibility, glyph coordinates, bundle integrity, or game-runtime loading.

## Runtime Canary

Use a screen containing representative simplified Chinese, punctuation, Latin text, symbols, and any retained Japanese names. Fully exit and restart the game between candidates so an old atlas cannot remain in memory.

Require:

- correct glyph shapes without atlas fragments or wrong valid Han characters;
- acceptable outline, spacing, clipping, and alignment;
- no new asset corruption, missing font, shader, or repeated missing-glyph errors in `Player.log`;
- coverage checks for menus, settings, shop/tutorial screens, dialogue, and at least one late-game scene when available.

On the first visual regression, stop, preserve the screenshot and candidate report, and restore the backup. Do not stack another speculative transformation on the broken candidate.

## Completion

Keep the final candidate report, applied write report, one rollback backup per modified file, and runtime evidence. Remove failed candidates, duplicate backups, temporary extraction roots, isolation assets, and stale logs only after the user confirms the final build works and approves the deletion list.

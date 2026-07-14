# TMP Font Compatibility And Replacement

Treat every font package as untrusted until its internal Unity version, TMP objects, atlas, and glyph coverage are inspected. A filename such as `arialuni`, `noto`, or `unity6000` is not evidence of the font family or serialization version inside the package.

Keep inspection reports in the game workspace, not in the shared skill repository, because reports echo user-supplied local paths. Do not commit font binaries unless their redistribution license has been reviewed and repository inclusion is intentional.

## Preflight

Run the read-only inspector before selecting a candidate:

```bash
python scripts/inspect_tmp_font_bundle.py "FontBundle" \
  --target-asset "GameFolder/Game_Data/sharedassets0.assets" \
  --translation-root "GameFolder/_translation/unity-text-report/validation" \
  --out "GameFolder/_translation/unity-text-report/font-fix/font_inspection.json"
```

Verify:

- the internal TMP font, material, and atlas names;
- the serialized Unity version inside the bundle and target asset;
- character and glyph counts;
- atlas dimensions, format, stream path, platform blob, and color space;
- coverage of characters actually used by the validated translation;
- the package SHA-256 and size.

Prefer an exact engine version. Treat the same Unity major/minor as a candidate requiring a runtime canary. Treat cross-major/minor raw MonoBehaviour replacement as high risk even if UnityPy can parse both files.

## Diagnose The Symptom First

| Runtime symptom | Likely layer | Required response |
| --- | --- | --- |
| Uniform tofu squares replacing specific characters | Missing glyph or fallback | Measure required-character coverage; add a compatible fallback or choose a broader font |
| Repeated glyph fragments, tiled text, or boxed atlas snippets | GlyphRect/atlas payload mismatch | Roll back immediately; inspect font table, texture payload, stream handling, and platform fields |
| Pink/magenta text or rectangles | Missing/incompatible shader or material | Restore the target shader and validate material properties and atlas reference |
| `corrupted`, `Position out of bounds`, or load failure | Serialization/container mismatch | Roll back; do not continue from the candidate |
| Dialogue is correct but menus are not | Multiple font assets or UI systems | Map each visible component to its actual TMP or legacy font before changing more assets |

Do not call glyph fragments “missing characters.” Do not infer runtime texture orientation from PIL or exported-image coordinates; Unity runtime rendering is the authority.

## Choose The Least Invasive Adapter

1. For Mono/TMP games with supported injection tables, prefer the preflighted runtime fallback workflow. It preserves original materials and atlases.
2. For IL2CPP games, do not claim the Mono fallback installer applies. Use a compatible IL2CPP runtime plugin only when its loader and interop generation are independently verified.
3. Use resource-level TMP replacement only when the target asset, container rules, and runtime test path are known.
4. If no compatible package exists, generate a TMP FontAsset with the exact target Unity/TMP version. Treat editor installation as a build-time dependency, not a game runtime dependency.

## Resource-Level Replacement Contract

Before writing:

1. Identify the exact visible text component and its font asset. Do not replace every discovered font as the first experiment.
2. Back up every modified asset and record its SHA-256.
3. Inspect the candidate with `inspect_tmp_font_bundle.py` and save the report.
4. Prove required-character coverage against validated translations.
5. Preserve the target MonoBehaviour script, object name, material and atlas PathIDs, and target shader.
6. When materializing a streamed atlas, copy the full payload and all relevant Texture2D fields, including platform blob, format, dimensions, mip settings, preprocessing flags, color space, and stream state. A correct byte count alone is insufficient.
7. Reopen the candidate with UnityPy and validate object names, references, glyph/character counts, atlas dimensions, and payload length.
8. Apply one-font, one-screen canary first. Do not broaden the replacement until the canary renders correctly.

UnityPy reopening proves structural readability only. It does not prove GPU texture upload, shader compatibility, glyph coordinates, bundle integrity, or game-runtime loading.

## Runtime Canary

Use a screen containing representative simplified Chinese, punctuation, Latin text, heart/music symbols, and any retained Japanese names. Fully exit and restart the game between candidates so an old atlas cannot remain in memory.

Require:

- correct glyph shapes without atlas fragments;
- acceptable outline, spacing, clipping, and alignment;
- no new asset corruption, missing font, shader, or repeated missing-glyph errors in the log;
- coverage checks for menus, settings, shop/tutorial screens, dialogue, and at least one late-game scene when available.

On the first visual regression, stop, preserve the screenshot and candidate report, and restore the backup. Do not stack another speculative transformation on the broken candidate.

## Completion

Keep the final candidate report, applied write report, one rollback backup per modified file, and runtime evidence. Remove failed candidates, duplicate backups, temporary extraction roots, isolation assets, and stale logs only after the user confirms the final build works and approves the deletion list.

# Runtime Text Adapters

Use a runtime adapter only after static discovery leaves confirmed visible text unresolved. Keep static, occurrence-mapped asset writeback as the default because runtime translators match text values globally and cannot preserve per-occurrence choices.

## Selection

| Evidence | Decision |
| --- | --- |
| Mono or IL2CPP game displays ordinary UGUI, TMP, IMGUI, or TextMesh strings that are absent from static sources | Consider a separately obtained, version-compatible runtime loader and XUnity.AutoTranslator plugin. Generate only a translation-text candidate with this skill. |
| Naninovel text appears during reveal animation or changes after the hook runs | Test reveal, instant display, skip, backlog, choices, and save/load. Prefer Naninovel's static localization data when available. Stop if the hook causes flicker, partial lines, or repeated translation. |
| GameCreator `TextReference` or another localization wrapper resolves keys at runtime | Test the final resolved string, variable substitution, plural/state variants, choices, and scene changes. A translated key or one successful label is not coverage proof. |
| Custom renderer, mesh atlas, native plugin, or direct texture drawing bypasses supported text components | Treat AutoTranslator as unsupported for that surface. Build a project-specific observer/adapter only with runtime evidence. |
| Text is baked into Texture2D, Sprite, video, or other pixels | Stop the text adapter. Use a separately reviewed image-localization workflow; string hooks and font fallback cannot translate pixels. |

## Candidate Generation

Run `validate_translation_csv.py` first and pass its normalized UTF-8 `zh_cn` output. Generate the audit report without a text file:

```bash
python scripts/generate_autotranslator_txt.py \
  --source-csv "GameFolder/_translation/unity-text-report/text/GameTitle_ui_text.csv" \
  --translation-csv "GameFolder/_translation/unity-text-report/validation/ui/GameTitle_ui_text_translation_utf8.csv" \
  --out-dir "GameFolder/_translation/unity-text-report/runtime/autotranslator"
```

Inspect `autotranslator_export_report.json`. Add `--write` only when its status is `ready` and every selected row is appropriate for global text-value matching. The command then writes a UTF-8 BOM `_PreTranslated.txt` candidate beside the report. It never installs a loader, plugin, config, font, or file into the game.

The exporter preserves placeholders and tags exactly. It does not guess XUnity placeholder rewrites or regex rules. It blocks:

- source/translation row-count differences;
- duplicate originals with different translations;
- duplicate originals where some occurrences are blank and others selected;
- keys or values whose delimiter/comment/control characters are ambiguous in the plain-text format;
- distinct source strings that collapse to the same serialized key.

Identical duplicate key/value rows are deduplicated and recorded. Blank rows are skipped. This conversion is a separate runtime artifact; never use its deduplicated output as a source for occurrence-mapped asset writeback.

## Loader And Plugin Preflight

Do not bundle, download, or automatically deploy BepInEx, XUnity.AutoTranslator, proxy DLLs, or font bundles from this skill. Before any manual canary installation:

1. Confirm Mono versus IL2CPP, process architecture, Unity version, and whether the game has anti-cheat or integrity protection.
2. Verify loader and plugin compatibility from their exact release documentation. Do not infer compatibility from filenames or another game.
3. Use a disposable game copy or record every added file and its SHA256. Keep removal of the entire overlay as the rollback path.
4. Confirm the plugin's configured translation directory and parser behavior. A commonly used `_PreTranslated.txt` name is not proof of the active path or format for every release.
5. Keep online translation endpoints and credentials disabled unless the user explicitly requests and reviews them.

## Runtime Canary

Require log evidence that both loader and translation plugin initialized. Then verify at least:

- one known source string and its exact Chinese result;
- dialogue reveal, skip/fast-forward, history/backlog, choices, variables, and save/load;
- scene changes and repeated visits, watching for recursion or stale translations;
- TMP and legacy UI glyph coverage, wrapping, clipping, and fallback behavior;
- startup and shutdown without loader/plugin exceptions;
- a known untranslated control string so overbroad matching is detectable.

Stop and remove the overlay when initialization is unproven, a selected occurrence must remain untranslated, custom-rendered text is still invisible, text flickers or reverts, placeholders break, or a font canary fails. A runtime log showing some captured strings does not prove complete coverage.

## Reporting

Record the candidate/report paths and SHA256 values, loader/plugin versions and architecture, configured destination path, initialization log path and findings, canary screens/workflows, residual text categories, font result, and exact rollback/removal scope. Keep runtime output separate from static manifests, writeback reports, and binary patch payloads.

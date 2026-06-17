# AiNiee Integration

This repository ships two sibling skills:

```text
unity-text-locator/
ainiee-translate/
```

`unity-text-locator` owns Unity extraction, validation, writeback, font replacement, and patch packaging. `ainiee-translate` owns agent-side translation rules, batching, status, and verification.

## Environment

For full-auto mode, install `ainiee-translate` dependencies and set:

```powershell
$env:AINIEE_SKILL_DIR = "path\to\unity-text-locator\ainiee-translate"
$env:AINIEE_PY = "$env:USERPROFILE\.venvs\ainiee-translate\Scripts\python.exe"
```

The bridge scripts default to the sibling repo layout. Pass `--ainiee-scripts` only when the skills are installed separately.

Run bridge scripts with `$AINIEE_PY`, not a random system `python`. They import `ainiee-translate` cache classes and runtime dependencies.

## Unity CSV To AiNiee Cache

```bash
"$AINIEE_PY" unity-text-locator/scripts/unity_csv_to_ainiee_cache.py \
  --source-csv "GameFolder/_translation/unity-text-report/text/GameTitle_text.csv" \
  --out-cache "GameFolder/_translation/unity-text-report/ainiee/GameTitle_text/cache.json"
```

The bridge uses the Unity CSV data row as AiNiee `text_index`. This preserves row order when converting back.

## Translation Loop

Use the `ainiee-translate` skill against the generated cache:

```bash
PYTHONPATH="path/to/ainiee-translate/scripts" "$AINIEE_PY" \
  -m ainiee_translate.batch read \
  "GameFolder/_translation/unity-text-report/ainiee/GameTitle_text/cache.json" \
  --size 100
```

The agent translates the returned JSON and writes a batch result:

```json
[
  {"text_index": 1, "translated_text": "译文"}
]
```

Then write it back:

```bash
PYTHONPATH="path/to/ainiee-translate/scripts" "$AINIEE_PY" \
  -m ainiee_translate.batch write \
  "GameFolder/_translation/unity-text-report/ainiee/GameTitle_text/cache.json" \
  "GameFolder/_translation/unity-text-report/ainiee/GameTitle_text/translations_001.json"
```

Repeat until `batch read` returns `[]`.

## AiNiee Cache To Unity CSV

```bash
"$AINIEE_PY" unity-text-locator/scripts/ainiee_cache_to_unity_translation.py \
  --cache "GameFolder/_translation/unity-text-report/ainiee/GameTitle_text/cache.json" \
  --source-csv "GameFolder/_translation/unity-text-report/text/GameTitle_text.csv" \
  --out-csv "GameFolder/_translation/unity-text-report/text/GameTitle_text_translation.csv"
```

Then validate:

```bash
python unity-text-locator/scripts/validate_translation_csv.py \
  --source-csv "GameFolder/_translation/unity-text-report/text/GameTitle_text.csv" \
  --translation-csv "GameFolder/_translation/unity-text-report/text/GameTitle_text_translation.csv" \
  --out-dir "GameFolder/_translation/unity-text-report/validation/text"
```

## Guardrails

- Do not let subagents write the same `cache.json` concurrently.
- Do not use AiNiee output directly for Unity writeback; always convert to `zh_cn` CSV and run Unity validation.
- Blank translations remain intentional skips.
- If the translated cache has fewer rows than the source CSV, conversion emits blank rows for missing indexes and validation/reporting should catch the gap before writeback.

# Workflow Modes

## Semi-Automatic Mode

Use semi-automatic mode when translation is produced outside the agent.

```text
scan/extract
-> audit TypeTree failures and raw MonoBehaviour residual candidates
-> source CSVs in text/
-> user supplies matching *_translation.csv files
-> validate_translation_csv.py
-> optional filter_approved_translation.py
-> writeback dry-run
-> writeback apply
-> font replacement
-> rerun source-specific residual audit until zero unreviewed visible occurrences
-> runtime check
-> rebuild patch from pristine originals to final candidate and clean-install test
```

Rules:

- Keep one translated row per source row.
- Leave `zh_cn` blank to preserve a row.
- Do not edit manifests.
- Do not write a file until validation and dry-run reports are reviewed.

## Full-Automatic Mode

Use full-automatic mode when the agent should translate proactively through the sibling `ainiee-translate` skill.

```text
scan/extract
-> audit TypeTree failures and raw MonoBehaviour residual candidates
-> source CSVs in text/
-> unity_csv_to_ainiee_cache.py
-> ainiee-translate batch read/write loop
-> independent terminology/missed-translation review
-> ainiee_cache_to_unity_translation.py
-> validate_translation_csv.py
-> writeback dry-run
-> writeback apply
-> font replacement
-> rerun source-specific residual audit until zero unreviewed visible occurrences
-> runtime check
-> rebuild patch from pristine originals to final candidate and clean-install test
```

Full-auto only changes who fills `zh_cn`. It does not change Unity safety gates.

Subagents may translate independent batches only into separate result JSON files. The primary agent serializes those files into `cache.json`, converts the complete cache back to Unity CSV, validates it, and applies review corrections through another serialized cache write. Never let concurrent workers edit the shared cache.

## Choosing A Mode

- Choose semi-automatic when the user wants to inspect or manually translate CSVs.
- Choose full-automatic when the user explicitly wants agent-side translation and accepts that the agent will produce the first translation pass.
- If the game has unsupported containers, Addressables integrity risk, or uncertain parser contracts, use full-auto for translation only after extraction boundaries are already proven.

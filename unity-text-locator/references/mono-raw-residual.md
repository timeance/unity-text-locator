# MonoBehaviour Raw UTF-8 Residual Workflow

Use this workflow when `read_typetree()` fails, embedded middleware data is only partly extracted, or visible Japanese survives normal writeback. It audits object-relative `uint32 little-endian length + UTF-8 + align4 zero padding` records. It does not prove that every candidate is visible.

## 1. Audit

Run against the composed candidate, not an obsolete source asset:

```bash
python scripts/extract_mono_raw_utf8.py \
  --asset "CandidateRoot/Game_Data/sharedassets0.assets" \
  --path-id 126 \
  --out-dir "ReportRoot/residual/mono_raw"
```

Without `--path-id`, the tool scans MonoBehaviours whose TypeTree cannot be read. It records asset SHA256, object raw SHA256, PathID, object-relative prefix offset, exact UTF-8 payload hash, aligned span, ordered newline events, and a stable occurrence ID.

The first run creates:

- `mono_raw_utf8_audit.csv`: every structural Japanese hit and its suggested classification.
- `mono_raw_utf8_approval.csv`: occurrence-level review surface.
- `mono_raw_residual_text.manifest.json`: audit evidence with `coverage_complete=false` until reviewed.

Internal rules run before the Japanese signal. They suggest preservation for `BGM_`, `SE_`, `SFX_`, `AM_`, voice/CG/image keys, asterisk route labels, and resource-name suffixes such as `画像`. Suggestions are not approval. Review every occurrence because identical text can serve different runtime roles.

## 2. Approve

Set every approval row to exactly one action:

- `translate`: expose this occurrence in the one-column translator CSV.
- `preserve`: keep this occurrence byte-for-byte and document why.

Do not leave blank actions. Do not approve by source text alone. Rerun:

```bash
python scripts/extract_mono_raw_utf8.py \
  --asset "CandidateRoot/Game_Data/sharedassets0.assets" \
  --path-id 126 \
  --approval-csv "ReportRoot/residual/mono_raw/mono_raw_utf8_approval.csv" \
  --out-dir "ReportRoot/residual/mono_raw"
```

Unknown, duplicate, stale, or unresolved occurrence IDs block completion. Only a complete approval emits `mono_raw_residual_text.csv` and a writeback manifest with `coverage_complete=true`.

## 3. Exact Controls

The translator CSV uses `backslash-controls-exact-v1`:

- `\\` represents one literal backslash.
- `\N` represents CRLF.
- `\n` represents LF.
- `\r` represents a bare CR.
- `\t` represents TAB.

Preserve every marker in order. The writer decodes the translation and compares the ordered newline signature, so mixed CR, CRLF, and LF records remain exact.

## 4. Translate And Validate

Translate through the normal AiNiee bridge or a reviewed one-column CSV. Run `validate_translation_csv.py`; its placeholder matcher recognizes the exact control markers. Independently review terminology and check for Japanese kana in the Chinese column before writeback.

## 5. Write An External Candidate

Dry-run performs real serialization to a temporary candidate, reopens it, verifies target payloads, and checks that untargeted object raw hashes did not change:

```bash
python scripts/writeback_mono_raw_utf8.py \
  --manifest "ReportRoot/residual/mono_raw/mono_raw_residual_text.manifest.json" \
  --source-csv "ReportRoot/residual/mono_raw/mono_raw_residual_text.csv" \
  --translation-csv "ReportRoot/validation/mono_raw_residual_text_translation_utf8.csv" \
  --report "ReportRoot/writeback/mono_raw_dryrun.json"
```

If any translation changes its aligned span, the default blocks. After confirming the container can resize, rerun with `--allow-object-resize`; this makes runtime canary evidence mandatory. Write only outside the game root:

```bash
python scripts/writeback_mono_raw_utf8.py \
  --manifest "...manifest.json" \
  --source-csv "...csv" \
  --translation-csv "...translation_utf8.csv" \
  --report "ReportRoot/writeback/mono_raw_write.json" \
  --out-asset "NextCandidateRoot/Game_Data/sharedassets0.assets" \
  --allow-object-resize --write
```

Treat `size_amplification_warning=true` as a release warning. UnityPy may resave most of an asset even when only one object changed. Record source/candidate sizes and hashes and require a runtime canary.

## 6. Residual Gate

Run the audit again on the final candidate. Release requires:

- zero `suggest_visible` or otherwise unreviewed occurrences;
- every remaining Japanese internal key preserved by occurrence and explained;
- the preserved-internal count and identities reconciled with the first audit;
- UnityPy reopen success, font verification, and a clean game-runtime canary.

Do not claim complete coverage or rebuild the patch ZIP before this gate passes.

# Source-Aware Translation QA

Use `audit_translation.py` after batch validation and before export. It compares
each translated row with its exact source occurrence and reports control/tag
tokens, placeholders, newline sequence, Japanese dialogue quotes, and locked
term omissions. The audit is fail-closed for structural mismatches.

The output is a review manifest, not an automatic repair. Add a `replacement`
only after confirming the intended text. `apply_repairs.py` requires the exact
`expected_before` value, writes a separate output file, and refuses stale rows.
Run the audit again after repair. Do not use this workflow to bypass Unity
occurrence manifests or MTool key immutability.

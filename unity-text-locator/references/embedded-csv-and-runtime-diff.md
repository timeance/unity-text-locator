# Embedded CSV and Runtime Diff Adapters

`extract_embedded_csv.py` is a discovery adapter for Naninovel-like or custom
CSV data stored inside `sharedassets*.assets` and AssetBundles. It emits
occurrence rows with the game-relative source path, source SHA-256, byte
offset, record number, row id, and column. Keep duplicates. Treat the result
as an audit source until the game's serializer or runtime API proves a matching
writeback path; never use a global `original_flat -> translation` map for
occurrence-specific edits.

`diff_autotranslator_files.py` compares two `_PreTranslated.txt` candidates and
reports added, removed, and changed keys. Use it after a new runtime capture or
before a manual deployment review. Duplicate keys in either input block the
report. The script does not merge files, install loaders, or claim that a
runtime candidate is safe. A removal or broad change requires manual review
and a runtime canary.

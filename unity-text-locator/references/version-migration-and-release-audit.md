# Version Migration and Release Audit

Use this reference when moving a completed translation to another game version or when visible story text, choices, or UI remain untranslated after an apparently successful writeback.

## Contents

- [Freeze Both Baselines](#freeze-both-baselines)
- [Separate Durable Evidence from Scratch](#separate-durable-evidence-from-scratch)
- [Map Resources by Internal Identity](#map-resources-by-internal-identity)
- [Migrate Objects Conservatively](#migrate-objects-conservatively)
- [Audit Runtime Locale Tables by Key](#audit-runtime-locale-tables-by-key)
- [Audit Bytecode and Short Choices](#audit-bytecode-and-short-choices)
- [Prove Residual Coverage](#prove-residual-coverage)
- [Choose Static Font Coverage Deliberately](#choose-static-font-coverage-deliberately)
- [Use Layered Runtime Smoke Tests](#use-layered-runtime-smoke-tests)
- [Build a Release Against One Baseline](#build-a-release-against-one-baseline)
- [Clean Up Without Losing Evidence](#clean-up-without-losing-evidence)

## Freeze Both Baselines

Keep four roots distinct:

1. Untouched old-version original.
2. Tested old-version translation.
3. Untouched new-version original.
4. External new-version candidate.

Hash every file that may be migrated. Never use the currently edited game as the only source of the old original. Record which files match the old original, old translation, or neither.

Do not apply candidates to the new game until object migration, catalog integrity, and residual audits are reproducible from these roots.

## Separate Durable Evidence from Scratch

Use three storage classes instead of scattering project-named directories across the workspace:

1. Durable project evidence under `GameFolder/_translation/unity-text-report/`: source and translation CSVs, manifests, normalized validation, applied writeback reports, original backups, release reports, and the final tested patch.
2. One external run root such as `Workspace/_unity-text-work/<project>/<run-id>/`: rebuilt bundles, catalogs, font candidates, extracted-object probes, compile output, and patch-test mirrors.
3. The live game root: only original game files plus the currently applied, verified runtime files and required launcher/plugin artifacts.

Keep rebuilt runtime candidates outside the game root until application. A broad scanner may rediscover candidates under the game, and a custom loader may enumerate unexpected files even when Unity Addressables normally uses explicit paths.

At completion, copy only durable reports and rollback evidence into `_translation`; delete the entire external run root after verifying its resolved path. Do not archive failed binary candidates merely because they were expensive to produce. Never leave ad hoc scripts or duplicate release archives at the workspace root.

## Map Resources by Internal Identity

Treat Addressables disk names as opaque. A content update may rename a bundle even when it represents the same logical resource.

For each old and new bundle:

1. Record the external relative path, SHA-256, size, and internal `AssetBundle.m_Name`.
2. Match versions by a unique internal name, not by the external hash-like filename.
3. Classify each match as byte-identical, changed, new, deleted, or ambiguous.
4. Stop on duplicate internal names or unreadable candidates instead of guessing.
5. Record catalog entries separately; a renamed bundle may also require a new external catalog location.

Directly reuse a translated old bundle only when the corresponding new original is byte-identical to the old original. Rebuild changed bundles from the new original at object level.

## Migrate Objects Conservatively

Within a uniquely mapped bundle, prefer stable serialized identity:

- serialized file name
- object type
- PathID
- owning MonoBehaviour or ScriptableObject identity
- field path or fixed-record offset contract

For each translated old occurrence, verify that the new object still contains the expected old source value before replacing it. Treat a stable PathID as evidence, not proof; confirm the object type and field.

Produce separate reports for:

- migrated occurrences
- unchanged translations reused from identical resources
- old translated occurrences with no new target
- new objects or strings absent from the old version
- type, field, or source-value mismatches

Scan new and changed objects even when every old occurrence mapped successfully. New game content cannot be found by projecting only the old translation manifest.

## Audit Runtime Locale Tables by Key

Do not select a Unity Localization table because its locale name looks convenient or because it already contains a few translated rows. Determine the actual runtime locale and prove that its table is key-complete.

For each localized table collection:

1. Enumerate available `Locale` assets and locale identifiers.
2. Read the shared table data and record every stable key ID and key name.
3. Record each locale table's key-ID set, blank values, Smart String metadata, and placeholders.
4. Compare key sets, not row positions. Missing IDs and blank values are separate failures.
5. Determine the selected runtime locale from project settings, startup code, persisted user settings, logs, or a runtime canary. A bundle or object suffix such as `_en` or `_ja` is not runtime evidence.
6. Trace fallback behavior. A missing entry may fall through to another locale, emit a `No translation found` message, or remain blank depending on project configuration.

When reusing an existing locale as the Chinese delivery locale, populate it by stable shared-table key ID. Do not copy another table by ordinal index: locale tables may be incomplete, differently ordered, or contain only a small subset of late-added keys.

If the runtime can select more than one locale and the patch intentionally supports them, mirror translations only after verifying the same shared key identity in each table. Otherwise force or preserve one documented locale selection and leave unrelated locale resources untouched.

Treat a runtime `No translation found` message as structured evidence. Capture its table/key identity, inspect the active locale table for a missing or blank key, and verify the repaired lookup through the same runtime route.

## Audit Bytecode and Short Choices

Some games store dialogue and choices inside MonoBehaviour byte arrays rather than ordinary `m_text` or string fields. A common record is:

```text
0x06 + uint32 little-endian byte length + UTF-8 payload
```

Use two extraction views when a fixed-record or DMSL-style adapter supports them:

1. Conservative translator view: exclude likely identifiers and control operands to reduce translation noise.
2. Exhaustive UTF-8 audit view: include every structurally valid UTF-8 operand, including short strings without punctuation.

Never use the conservative view as proof of coverage. It can omit visible choices such as a short verb, `yes`/`no`, or a punctuationless command because those strings resemble identifiers.

In the exhaustive view:

- preserve every occurrence and its byte budget
- classify identifiers and resource keys explicitly instead of deleting rows
- leave confirmed internal operands blank in the translation column
- translate visible short choices and prompts
- validate placeholders, tags, control tokens, and UTF-8 byte lengths
- reject the whole write batch on any over-budget row; shorten the translation and rerun
- never truncate a UTF-8 payload to fit

Write to an external candidate first. Re-extract the candidate with the same exhaustive mode before application.

## Prove Residual Coverage

After all candidates are composed, rescan the complete effective game root, not only files changed in the last pass.

Run at least these checks:

- exhaustive fixed-record or bytecode operand extraction
- TypeTree UI and Unity Localization fields
- adapter-specific story, profile, evidence, and database fields
- new or changed objects from the version migration report
- source values intentionally preserved as identity, credit, or resource data

Kana detection is a useful Japanese residual signal, but `zero kana` is not proof that every Japanese string is gone. All-kanji text, Latin-script labels, and missed unsupported containers require source-manifest comparison and adapter coverage reporting.

Report residuals as occurrences and unique values. Keep an explicit allowlist with reasons for developer credits, product identity, or deliberate untranslated material. Never silently subtract allowlisted rows from the audit totals.

Classify residuals by runtime reachability without silently discarding them:

- active runtime source: blocking until translated or intentionally preserved
- alternate locale or fallback source: blocking when that locale is selectable
- sample/test/development asset: report as non-runtime only with container or loading evidence
- developer credit or product identity: explicit allowlist
- unknown reachability: unresolved, not complete

## Choose Static Font Coverage Deliberately

Fix the actual FontAsset chain, not merely the apparent font filename. For each visible TMP family, trace the component to the FontAsset, Material, atlas Texture2D, serialized file, CAB references, and bundle.

If a dynamic FontAsset already contains populated character and glyph tables, replacing only its source font can make valid Unicode render as a different valid Han glyph. Treat that symptom as a glyph-index/table mismatch, not a translation typo.

For a static replacement, move a matched set together:

- character table and glyph table
- face metrics and atlas population mode
- Material and shader atlas parameters
- SDF atlas Texture2D data and dimensions
- FontAsset-to-Material/Texture references

Preserve the target objects' serialized identities when existing components reference them. Reopen the result, verify every required code point resolves to a valid glyph, and require a runtime canary.

Choose coverage explicitly:

1. Prefer a required-character closure when all translated and runtime-composed strings are known. Include UI symbols, names, placeholders, and deliberately preserved identity text.
2. Use a broader or complete CJK static set only when runtime text is open-ended, unsupported containers remain, future content must render, or repeated fallback would produce inconsistent metrics.
3. Before accepting a full set, check Unity/platform maximum texture size, atlas memory, bundle growth, startup/load cost, and target GPU support. An 8192 x 8192 single-channel atlas is about 64 MiB before container overhead.
4. When replacing several stylistically different fonts with one donor family, verify layout, line height, baseline, weight, and intentional typography on every affected screen. Coverage does not prove visual equivalence.

Do not call a font fix complete from atlas coverage alone. Verify that fallback is no longer unexpectedly mixing metrics, tofu is absent, correct code points render as correct glyphs, and the modified bundle still loads through Addressables.

## Use Layered Runtime Smoke Tests

Use the cheapest conclusive layer first:

1. Structural layer: reopen rebuilt assets, verify candidate hashes, catalog edits, and full residual reports.
2. Process layer: launch the intended executable and confirm that the game process remains alive long enough to initialize.
3. Log layer: inspect the newly modified `Player.log` when one exists. Search for CRC mismatch, `Will not load AssetBundle`, Addressables/catalog exceptions, missing dependencies, font initialization failures, and missing glyphs.
4. Visual layer: inspect the smallest screen or route that exercises each changed container.
5. Player layer: accept a user's explicit successful playthrough of the affected route as stronger interaction evidence than prolonged passive observation.

Do not wait indefinitely for a Unity log that the build does not create. Record that no fresh log exists, then use a visual canary or player confirmation. Do not claim paths that were not exercised.

After binary writeback, rerun at least the process layer even if the same candidate passed before it was copied into the formal game root.

## Build a Release Against One Baseline

Use the untouched new-version original backup as the default patch source. Add alternate source hashes only when the user explicitly requests compatibility with an earlier patch or version.

For an original-only release:

- include exactly one original hash and one final hash per modified resource
- omit alternate sources
- verify the launcher or auxiliary executable by hash
- keep target version and compatibility policy in the manifest
- use an ASCII-safe manifest when Windows PowerShell 5.1 may read it with its default encoding
- give the final archive a real `.zip` extension

Acceptance-test the extracted final archive, not only its staging directory:

1. Install onto a minimal copy of the untouched supported original.
2. Verify every installed resource and launcher hash.
3. Run the installer again and prove idempotence.
4. Tamper with one source file and prove rejection occurs before any write.
5. Confirm unsupported old versions or prior translations are rejected when compatibility was not requested.

Keep the last tested archive immutable. Replace an older release only after all acceptance tests pass.

## Clean Up Without Losing Evidence

Keep:

- final source and translation CSVs plus manifests
- normalized validation and residual reports
- applied writeback report
- one untouched original backup for every released file
- final patch archive and acceptance-test report
- rollback-critical font or catalog evidence

Remove external candidates, extracted patch-test roots, failed experiments, duplicate archives, ad hoc migration scripts, logs with no diagnostic value, compile output, and staged temporary files after the release is verified.

Before recursive cleanup, resolve and verify every exact target remains inside the intended workspace. Do not remove another version's baseline or another worktree.

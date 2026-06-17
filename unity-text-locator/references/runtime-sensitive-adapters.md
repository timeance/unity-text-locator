# Runtime-Sensitive Unity Adapters

Use this reference only when the build contains text containers or runtime behaviors that ordinary serialized-asset writeback cannot prove safe.

## BansheeGz / BGDatabase TextAsset

Evidence:

- A `TextAsset` named like `bansheegz_database`.
- `BGDatabase.dll`, `BGDatabase.Addressables.dll`, or types such as `BansheeGz.BGDatabase` / `BGRepo`.

Safe procedure:

1. Extract the database TextAsset payload and hash both its containing asset file and payload.
2. Compile a temporary probe against the game's own managed assemblies.
3. In the running game, load the database through its managed repository API and export row identities as meta, entity index, and field name.
4. Create the one-column translator source and a manifest carrying those identities and hashes.
5. For selected translated rows, compare current structured field text with `original_flat` before setting a value.
6. Save through the managed database API and capture the patched payload hash.
7. Reinsert only the runtime-produced payload into the containing Unity asset with a backup and payload re-read verification.
8. Restore startup-registration JSON and remove temporary probe DLLs before a normal game smoke test.

Do not patch a BGDatabase payload as arbitrary UTF-8 spans. Its internal serialization is owned by the game/plugin API.

## Addressables Localization Bundles

Text-table rows inside a bundle may be readable and writable while the resulting bundle remains unusable by the game.

Required gates:

1. Preserve original bundle packing/compression when rebuilding where possible.
2. Determine whether catalog entries carry CRC, hash, size, cache, or local-load options.
3. Treat catalog edits as integrity changes: keep an untouched original and write a candidate first.
4. Validate the rebuilt bundle in the Unity game runtime, ideally through the same loading route used by Addressables.
5. If runtime load fails or cannot be proven, do not apply the translated bundle or catalog even when translation CSV validation passed.

Translation structure validation answers whether rows are safe text. It does not prove resource-container integrity.

## Runtime Font Fallback Compatibility

Mono/TMP builds can expose a smaller API surface than the Unity editor assemblies suggest:

- `Font.CreateDynamicFontFromOSFont` may not compile against shipped modules.
- Older Mono profiles may throw when code calls `ParameterInfo.HasDefaultValue`.
- A `Font(string)` overload may require an actual font file path rather than a font family name.

Prefer a preflight-compiled fallback DLL that selects an existing CJK font file path and uses conservative reflection argument construction. After applying it, require `Player.log` proof of initialization and no font-load or missing-method errors.

## Easy Save 3

Clues include `ES3`, `ES3Defaults`, `SaveFile.es3`, `AutoSave.es3`, and `ES3_Save`/`ES3_Load` strings.

To locate a save:

1. Inspect serialized `ES3Defaults` settings for the configured filename and location.
2. Search the game directory and the Unity company/product `AppData\LocalLow` directory.
3. Use file timestamps to distinguish an active save from bundled defaults or logs.
4. Do not claim `AppData\LocalLow` is the save location when only `Player.log` or PlayerPrefs values exist there.

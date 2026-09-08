# xcode-clr

Single-file Python 3 CLI that finds & deletes stale Xcode DerivedData, worktree `build/` directories, and long-unused iOS Simulator devices. macOS only, stdlib only.

## Architecture

One file: `xcode-clr`. Sections in order: dataclass `Entry`, scanners (DerivedData: `folder_hash`, `derived_data_hash`, `read_info_plist`, `local_package_paths`, `last_activity`, `find_derived_data`, `xcodebuild_targets`, `resolve_derived_sources`; worktrees: `git_worktree_paths`, `find_git_root`, `resolve_worktree_roots`, `list_worktrees`, `find_stale_builds`; simulators: `parse_iso`, `runtime_label`, `simctl_devices`, `find_simulators`), `source_missing`, `mark_stale`, sizing (`du_bytes`, `compute_sizes` via `ThreadPoolExecutor`), renderers (`render_table`, `render_json`), `delete_entries`, config loaders (`load_config`, `env_worktree_roots`), `main`.

Keep it one file. No deps. No `pip install`.

## Worktree-root resolution

`resolve_worktree_roots` merges (in this order, deduped by `.resolve()`):
1. `worktree_roots` from `~/.config/xcode-clr/config.json` (respects `XDG_CONFIG_HOME`).
2. `XCODE_CLR_WORKTREE_ROOTS` env (colon-separated).
3. `--worktree-root PATH` (repeatable on CLI).
4. Auto-discovery: walk up from each DerivedData `WorkspacePath` and from each local package path in `SourcePackages/workspace-state.json` to the nearest `.git` (file or dir → handles linked worktrees). The package paths are what bootstraps roots when no folder has `info.plist`. Disabled by `--no-auto` or `auto_discover: false` in config.

`list_worktrees` runs `git worktree list --porcelain` on each root and dedupes (multiple roots can yield the same worktree); `find_stale_builds` and `resolve_derived_sources` both consume that list.

## DerivedData without `info.plist`

Command-line `xcodebuild` never writes `info.plist`, so those folders carry no `WorkspacePath`. `find_derived_data` keeps any folder whose name ends in a 28-letter hash. `resolve_derived_sources` recovers the source by hashing candidate paths from every known worktree (`xcodebuild_targets`: top-level `*.xcworkspace` / `*.xcodeproj`, plus dirs holding `Package.swift` up to two levels deep) with Xcode's scheme (`derived_data_hash`: MD5 of the absolute path, each 8-byte half rendered as 14 base-26 letters). No match → `source_path` stays `None`, SOURCE column shows `-`. `source_missing` then fires only when `workspace-state.json` lists local packages and none exist; otherwise staleness is age-only.

## Conventions

- Shebang is `/usr/bin/python3` (system 3.9). The homebrew Python 3.14 on this host has a broken `libexpat` link that crashes `plistlib`. Don't switch back to `env python3` until that's fixed.
- All datetimes are timezone-aware UTC.
- Sizes via `du -sk` (KB blocks → bytes ×1024). Matches Finder. Simulators reuse simctl's `dataPathSize` (no `du`); `compute_sizes` only `du`s entries with `size_bytes == 0`.
- Staleness ts = `max(LastAccessedDate from info.plist, folder mtime, Logs/*/LogStoreManifest.plist mtimes)` for DerivedData (`last_activity`; the manifests are the only activity signal for `xcodebuild` folders); folder mtime for worktree builds; `lastBootedAt` for simulators (folder mtime is noise — bulk-touched by background activity, never use it as the sim signal).
- Skip any DerivedData child whose name ends `.noindex` or matches the cache set; skip any folder with neither a `WorkspacePath` nor a hash-suffixed name.

## Simulators

`find_simulators` parses `xcrun simctl list devices --json` (grouped by runtime id). Per device it reads `udid`, `dataPath` (parent = device dir = `Entry.path`), `lastBootedAt`, `dataPathSize`, `state`, `isAvailable`, `name`. `mark_stale` rules, in order:
1. `not available` (runtime uninstalled) → `runtime unavailable`, always removable.
2. `state != "Shutdown"` (booted / in use) → never touched.
3. `last_accessed is None` (never booted — Xcode default template, ~17 MB) → left alone.
4. else stale if `lastBootedAt` older than `--simulator-days` (own threshold, default 14 — passed separately from `--days` into `mark_stale`).

Deletion is `xcrun simctl delete <udid>` (NOT `rmtree` — keeps CoreSimulator's registry consistent). `delete_entries` branches on `kind == "simulator"`. JSON sim items carry extra `name`, `udid`, `state`, `available` keys; top-level JSON carries `simulator_threshold_days`. Toggle scanning with `--no-simulators` / `scan_simulators` (default on).

## Adding flags

Append to `argparse` block in `main()`. Flags that suppress deletion (`--dry-run`, `--json`) must short-circuit before the prompt. `--json` includes **all** scanned items (not just stale) with `to_be_removed` flag — agents need full state.

## Testing manually

```sh
./xcode-clr --dry-run               # baseline list
./xcode-clr --json --days 3650      # only "source missing" should be stale
./xcode-clr --days 0 --dry-run      # every entry stale
```

No automated tests — surface area is small and side-effects are filesystem-wide. Run dry-run + json before any change to a scanner or renderer.

## Skill

Project ships a Claude skill at `.claude/skills/xcode-clr/SKILL.md`. Install instructions in `README.md` and the skill's own header.

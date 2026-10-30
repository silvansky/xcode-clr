---
name: xcode-clr
description: Use this skill when the user wants to free disk space by cleaning Xcode build artifacts — stale DerivedData folders, orphaned `build/` directories in git worktrees, long-unused iOS Simulator devices, purgeable caches inside shut-down simulators, or any mention of "Xcode taking too much space", "clean DerivedData", "purge build cache", "delete old simulators", or running low on disk space on a Mac that runs Xcode.
---

# xcode-clr — stale Xcode build artifact cleaner

`xcode-clr` lists & deletes:
- DerivedData folders whose source workspace is gone or untouched >2 days.
- `build/` folders inside `git worktree list` entries untouched >2 days.
- iOS Simulator devices unused for >3 days (`--simulator-days`), or whose runtime is uninstalled. Supports Xcode 26.x (`lastBootedAt`) and Xcode 27 / Device Hub (`lastUsedAt`); uses the newer valid timestamp if both exist.
- Purgeable caches inside shut-down simulators (abandoned `nsurlsessiond` downloads, `coresymbolicationd`, dead app containers, `tmp`, per-app caches). Contents only; the device stays.
- Large Finance database WALs (`finance_cloud.db-wal`), checkpointed through SQLite on shut-down devices without deleting database data. Skip with `--no-simulator-wals`.
- Warns about simulators over 2 GB. Explicit `--reset-idle` offers idle large devices for reset, erasing all apps, app data and settings while keeping the device. Preview and obtain confirmation for this destructive reset separately from ordinary cache cleanup.

Worktree roots are **auto-discovered** from DerivedData (`WorkspacePath`, or SwiftPM local package paths for `xcodebuild`-made folders; walks up to nearest `.git`). Extra roots via `--worktree-root PATH` (repeatable), env `XCODE_CLR_WORKTREE_ROOTS=a:b`, or `~/.config/xcode-clr/config.json`. Skips shared caches (`ModuleCache.noindex`, `SDKStatCaches.noindex`, `CompilationCache.noindex`). Simulators come from `xcrun simctl`; skip them with `--no-simulators`, and their caches with `--no-simulator-caches`.

## Usage from an agent

**Always start with `--json`** — it's the structured form, suppresses prompts, and never deletes:

```sh
xcode-clr --json
```

Parse `items[]`. Each item: `path`, `kind` (`derived_data`|`worktree_build`|`simulator`|`simulator_cache`), `source_path`, `size_bytes`, `last_accessed`, `mtime`, `age_days`, `reason`, `to_be_removed`. Simulator and simulator-cache items add `name`, `udid`, `state`, `available` (and `last_accessed` is the last activity from `lastBootedAt` / `lastUsedAt`); simulator-cache items also list `purge_paths`. Top-level `disk` carries `free_bytes`/`total_bytes`/`used_bytes`.

To delete after the user confirms:

```sh
xcode-clr --yes              # non-interactive: deletes all to_be_removed
xcode-clr --dry-run          # preview only (human table)
xcode-clr --all              # show every folder, not only stale (deletion still stale-only)
xcode-clr --days 30          # raise DerivedData/build threshold to 30 days
xcode-clr --simulator-days 30 # raise simulator threshold (default 3) to 30 days
xcode-clr --worktree-root ~/work/repo-a --worktree-root ~/work/repo-b
xcode-clr --no-auto          # only scan explicit/config roots, skip auto-discovery
xcode-clr --no-simulator-caches # leave simulator caches alone
xcode-clr --reset-idle --idle-minutes 60 --dry-run # preview resets of large idle devices
```

## Recipes

**"How much would I free?"**

```sh
xcode-clr --json | jq '[.items[] | select(.to_be_removed) | .size_bytes] | add'
```

**"Which projects have orphaned DerivedData?"**

```sh
xcode-clr --json | jq '.items[] | select(.reason=="source missing") | {path, source_path, size_bytes}'
```

**"Which simulators can I delete and how much would they free?"**

```sh
xcode-clr --json | jq '[.items[] | select(.kind=="simulator" and .to_be_removed)] | {count: length, bytes: (map(.size_bytes) | add)}'
```

**"Clean everything older than 14 days"**

```sh
xcode-clr --days 14 --dry-run   # preview first
xcode-clr --days 14 --yes
```

## Gotchas

- `--json` and `--dry-run` never delete. `--yes` does. Always show the user the preview before invoking with `--yes`.
- Worktree roots are auto-discovered from DerivedData. Add untracked repos via `--worktree-root`, env, or config — disable with `--no-auto`.
- Simulators are deleted via `xcrun simctl delete <udid>`. Currently-booted and never-booted (default template) devices are never removed; runtime-unavailable ones always are. Skip the whole scan with `--no-simulators`.
- Simulator caches are only cleared on devices verified `Shutdown` at cleanup time; booted entries need explicit `--shutdown-idle` selection first; booted devices hold those files open. Clearing keeps the directories and deletes only their contents. Skip with `--no-simulator-caches`.
- Booted devices' caches and WALs appear in the default table under "Available after simulator shutdown". JSON exposes `requires_shutdown`, `shutdown_then_clean`, `idle_blocker`, and top-level `after_shutdown_bytes`. Normal cleanup excludes them. `--shutdown-idle --idle-minutes N` offers conditional shutdown and cleanup after no recent app-container activity and no detected running apps/test runners (default 30 minutes). Preview with `--dry-run` or `--json`; these never shut down devices. Idle checks are heuristic, fail closed, and repeat before shutdown.
- `simulator_wal` items expose `action: "checkpoint"`, the WAL `path`, and database `source_path`. Running devices are visible but not selected. Shut them down separately before cleanup; never unlink a WAL directly. WAL sizes estimate potential savings; checkpointing can grow the main database. Table totals deduplicate devices and their contents.
- Simulator items expose `oversized`, `to_be_reset`, `action`, `requires_shutdown` and `idle_blocker`. `--reset-idle` offers available devices strictly over 2 GiB after the shared idle checks (default 30 minutes, `--idle-minutes N`), including shut-down devices. Selected booted devices are checked again, shut down, then rechecked before `simctl erase`. Reset candidates have `to_be_removed: false` and `to_be_reset: true`; a blocked reset never falls back to device deletion. `--json` and `--dry-run` only preview. Never add `--reset-idle` to a cleanup command unless the user explicitly approved erasing simulator apps, data and settings; `--yes` alone does not enable reset. System assets may download again. Report reset savings as estimates until execution completes.
- If `xcode-clr` is not on PATH, the install/setup question is out of scope for this skill — point the user at the project README.

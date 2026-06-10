---
name: xcode-clr
description: Use this skill when the user wants to free disk space by cleaning Xcode build artifacts — stale DerivedData folders, orphaned `build/` directories in git worktrees, long-unused iOS Simulator devices, or any mention of "Xcode taking too much space", "clean DerivedData", "purge build cache", "delete old simulators", or running low on disk space on a Mac that runs Xcode.
---

# xcode-clr — stale Xcode build artifact cleaner

`xcode-clr` lists & deletes:
- DerivedData folders whose source workspace is gone or untouched >7 days.
- `build/` folders inside `git worktree list` entries untouched >7 days.
- iOS Simulator devices not booted in >14 days (`--simulator-days`), or whose runtime is uninstalled.

Worktree roots are **auto-discovered** from DerivedData `WorkspacePath` (walks up to nearest `.git`). Extra roots via `--worktree-root PATH` (repeatable), env `XCODE_CLR_WORKTREE_ROOTS=a:b`, or `~/.config/xcode-clr/config.json`. Skips shared caches (`ModuleCache.noindex`, `SDKStatCaches.noindex`, `CompilationCache.noindex`). Simulators come from `xcrun simctl`; skip them with `--no-simulators`.

## Usage from an agent

**Always start with `--json`** — it's the structured form, suppresses prompts, and never deletes:

```sh
xcode-clr --json
```

Parse `items[]`. Each item: `path`, `kind` (`derived_data`|`worktree_build`|`simulator`), `source_path`, `size_bytes`, `last_accessed`, `mtime`, `age_days`, `reason`, `to_be_removed`. Simulator items add `name`, `udid`, `state`, `available` (and `last_accessed` is the last boot time). Top-level `disk` carries `free_bytes`/`total_bytes`/`used_bytes`.

To delete after the user confirms:

```sh
xcode-clr --yes              # non-interactive: deletes all to_be_removed
xcode-clr --dry-run          # preview only (human table)
xcode-clr --all              # show every folder, not only stale (deletion still stale-only)
xcode-clr --days 30          # raise DerivedData/build threshold to 30 days
xcode-clr --simulator-days 30 # raise simulator threshold (default 14) to 30 days
xcode-clr --worktree-root ~/work/repo-a --worktree-root ~/work/repo-b
xcode-clr --no-auto          # only scan explicit/config roots, skip auto-discovery
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
- If `xcode-clr` is not on PATH, the install/setup question is out of scope for this skill — point the user at the project README.

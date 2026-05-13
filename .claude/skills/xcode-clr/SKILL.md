---
name: xcode-clr
description: Use this skill when the user wants to free disk space by cleaning Xcode build artifacts — stale DerivedData folders, orphaned `build/` directories in git worktrees, or any mention of "Xcode taking too much space", "clean DerivedData", "purge build cache", or running low on disk space on a Mac that runs Xcode.
---

# xcode-clr — stale Xcode build artifact cleaner

CLI at `xcode-clr` (or `~/Projects/xcode-clr/xcode-clr`) that lists & deletes:
- DerivedData folders whose source workspace is gone or untouched >7 days.
- `build/` folders inside `git worktree list` entries untouched >7 days.

Worktree roots are **auto-discovered** from DerivedData `WorkspacePath` (walks up to nearest `.git`). Extra roots via `--worktree-root PATH` (repeatable), env `XCODE_CLR_WORKTREE_ROOTS=a:b`, or `~/.config/xcode-clr/config.json`. Skips shared caches (`ModuleCache.noindex`, `SDKStatCaches.noindex`, `CompilationCache.noindex`).

## Install (one-time)

```sh
chmod +x ~/Projects/xcode-clr/xcode-clr
ln -s ~/Projects/xcode-clr/xcode-clr /usr/local/bin/xcode-clr
```

## Usage from an agent

**Always start with `--json`** — it's the structured form, suppresses prompts, and never deletes:

```sh
xcode-clr --json
```

Parse `items[]`. Each item: `path`, `kind` (`derived_data`|`worktree_build`), `source_path`, `size_bytes`, `last_accessed`, `mtime`, `age_days`, `reason`, `to_be_removed`.

To delete after the user confirms:

```sh
xcode-clr --yes              # non-interactive: deletes all to_be_removed
xcode-clr --dry-run          # preview only (human table)
xcode-clr --all              # show every folder, not only stale (deletion still stale-only)
xcode-clr --days 30          # raise threshold to 30 days
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

**"Clean everything older than 14 days"**

```sh
xcode-clr --days 14 --dry-run   # preview first
xcode-clr --days 14 --yes
```

## Gotchas

- Shebang is `/usr/bin/python3` (system 3.9) on purpose — don't switch to `env python3` if user has homebrew 3.14.
- `--json` and `--dry-run` never delete. `--yes` does. Always show the user the preview before invoking with `--yes`.
- Worktree roots are auto-discovered from DerivedData. Add untracked repos via `--worktree-root`, env, or config — disable with `--no-auto`.

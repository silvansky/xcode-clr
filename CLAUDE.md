# xcode-clr

Single-file Python 3 CLI that finds & deletes stale Xcode DerivedData, worktree `build/` directories, and long-unused iOS Simulator devices. macOS only, stdlib only.

## Architecture

One file: `xcode-clr`. Sections in order: dataclass `Entry`, scanners (`find_derived_data`, `git_worktree_paths`, `find_git_root`, `resolve_worktree_roots`, `find_stale_builds`, simulators: `parse_iso`, `runtime_label`, `simctl_devices`, `find_simulators`), `mark_stale`, sizing (`du_bytes`, `compute_sizes` via `ThreadPoolExecutor`), renderers (`render_table`, `render_json`), `delete_entries`, config loaders (`load_config`, `env_worktree_roots`), `main`.

Keep it one file. No deps. No `pip install`.

## Worktree-root resolution

`resolve_worktree_roots` merges (in this order, deduped by `.resolve()`):
1. `worktree_roots` from `~/.config/xcode-clr/config.json` (respects `XDG_CONFIG_HOME`).
2. `XCODE_CLR_WORKTREE_ROOTS` env (colon-separated).
3. `--worktree-root PATH` (repeatable on CLI).
4. Auto-discovery: walk up each DerivedData `WorkspacePath`'s parents to the nearest `.git` (file or dir → handles linked worktrees). Disabled by `--no-auto` or `auto_discover: false` in config.

`find_stale_builds` runs `git worktree list --porcelain` on each root and dedupes worktree paths (multiple roots can yield the same worktree).

## Conventions

- Shebang is `/usr/bin/python3` (system 3.9). The homebrew Python 3.14 on this host has a broken `libexpat` link that crashes `plistlib`. Don't switch back to `env python3` until that's fixed.
- All datetimes are timezone-aware UTC.
- Sizes via `du -sk` (KB blocks → bytes ×1024). Matches Finder. Simulators reuse simctl's `dataPathSize` (no `du`); `compute_sizes` only `du`s entries with `size_bytes == 0`.
- Staleness ts = `max(LastAccessedDate from info.plist, folder mtime)` for DerivedData; folder mtime for worktree builds; `lastBootedAt` for simulators (folder mtime is noise — bulk-touched by background activity, never use it as the sim signal).
- Skip any DerivedData child whose name ends `.noindex` or matches the cache set; skip any folder without `info.plist` + `WorkspacePath`.

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

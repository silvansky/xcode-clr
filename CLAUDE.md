# xcode-clr

Single-file Python 3 CLI that finds & deletes stale Xcode DerivedData and worktree `build/` directories. macOS only, stdlib only.

## Architecture

One file: `xcode-clr`. Sections in order: dataclass `Entry`, scanners (`find_derived_data`, `find_stale_builds`), `mark_stale`, sizing (`du_bytes`, `compute_sizes` via `ThreadPoolExecutor`), renderers (`render_table`, `render_json`), `delete_entries`, `main`.

Keep it one file. No deps. No `pip install`.

## Conventions

- Shebang is `/usr/bin/python3` (system 3.9). The homebrew Python 3.14 on this host has a broken `libexpat` link that crashes `plistlib`. Don't switch back to `env python3` until that's fixed.
- All datetimes are timezone-aware UTC.
- Sizes via `du -sk` (KB blocks → bytes ×1024). Matches Finder.
- Staleness ts = `max(LastAccessedDate from info.plist, folder mtime)` for DerivedData; folder mtime for worktree builds.
- Skip any DerivedData child whose name ends `.noindex` or matches the cache set; skip any folder without `info.plist` + `WorkspacePath`.

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

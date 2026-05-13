# xcode-clr

Delete stale Xcode DerivedData and worktree `build/` directories.

Single-file Python 3 CLI. No dependencies. macOS only.

## What it scans

- `~/Library/Developer/Xcode/DerivedData/*` — each project folder. Skips shared caches (`ModuleCache.noindex`, `SDKStatCaches.noindex`, `CompilationCache.noindex`).
- `build/` inside every `git worktree list` entry of `~/Projects/SongsterrPhone` (override with `--worktree-root`).

## Staleness rules

A folder is marked `to_be_removed` if **any** apply:

- DerivedData's `WorkspacePath` (from `info.plist`) no longer exists → `source missing`.
- `max(LastAccessedDate, folder mtime)` is older than 7 days → `stale >7d` (override with `--days`).
- Worktree `build/` folder mtime older than 7 days → `stale >7d`.

## Usage

```
xcode-clr [--dry-run] [--json] [--yes] [--days N] [--worktree-root PATH]
```

| Flag | Effect |
|---|---|
| *(none)* | Print table, prompt once, delete on `y`. |
| `--dry-run` | Print table, never delete. |
| `--json` | Emit JSON to stdout (all scanned items, `to_be_removed` flag). No prompts, no delete. |
| `--yes` / `-y` | Skip confirmation; delete everything marked. |
| `--days N` | Staleness threshold (default `7`). |
| `--worktree-root PATH` | Git worktree root (default `~/Projects/SongsterrPhone`). |
| `--all` | List every folder found (stale and fresh). Deletion still targets only stale ones. |

## Install

```sh
git clone <repo> ~/Projects/xcode-clr
chmod +x ~/Projects/xcode-clr/xcode-clr
ln -s ~/Projects/xcode-clr/xcode-clr /usr/local/bin/xcode-clr   # optional
```

The shebang is `/usr/bin/python3` (system Python 3.9+, stdlib only — no `pip install` needed).

## JSON output

```json
{
  "scanned_at": "2026-05-13T08:13:09Z",
  "threshold_days": 7,
  "items": [
    {
      "path": "/Users/.../DerivedData/Foo-abc",
      "kind": "derived_data",
      "source_path": "/Users/.../Foo.xcworkspace",
      "size_bytes": 1234567,
      "last_accessed": "2026-04-13T06:55:31Z",
      "mtime": "2026-04-13T06:55:31Z",
      "age_days": 30.05,
      "reason": "stale >7d",
      "to_be_removed": true
    }
  ]
}
```

`kind` is `"derived_data"` or `"worktree_build"`. `reason` is `null` for kept items.

## Examples

```sh
xcode-clr --dry-run                 # preview
xcode-clr --json | jq '.items[] | select(.to_be_removed)'
xcode-clr --yes                     # non-interactive cleanup
xcode-clr --days 30                 # only purge things older than 30d
xcode-clr --all --dry-run           # full inventory, stale and fresh
```

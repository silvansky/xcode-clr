# xcode-clr

Delete stale Xcode DerivedData, worktree `build/` directories, and long-unused iOS Simulators.

Single-file Python 3 CLI. No dependencies. macOS only.

## What it scans

- `~/Library/Developer/Xcode/DerivedData/*` — each project folder. Skips shared caches (`ModuleCache.noindex`, `SDKStatCaches.noindex`, `CompilationCache.noindex`).
- `build/` inside every git worktree of every project Xcode has built (auto-derived from DerivedData `WorkspacePath`). Add extras with `--worktree-root`, disable auto with `--no-auto`.
- iOS Simulator devices (`xcrun simctl list devices`) not booted in a long time. Disable with `--no-simulators`.

## Staleness rules

An item is marked `to_be_removed` if **any** apply:

- DerivedData's `WorkspacePath` (from `info.plist`) no longer exists → `source missing`.
- `max(LastAccessedDate, folder mtime)` is older than 7 days → `stale >7d` (override with `--days`).
- Worktree `build/` folder mtime older than 7 days → `stale >7d`.
- Simulator last booted (`lastBootedAt`) older than 14 days → `stale >14d` (override with `--simulator-days`).
- Simulator whose runtime is no longer installed → `runtime unavailable`.

Simulators that are **currently booted** or were **never booted** (Xcode's default templates) are never removed.

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
| `--days N` | Staleness threshold for DerivedData & worktree builds (default `7`). |
| `--simulator-days N` | Staleness threshold for simulators (default `14`). |
| `--worktree-root PATH` | Extra git worktree root to scan. Repeatable. |
| `--no-auto` | Disable auto-discovery of worktree roots from DerivedData. |
| `--no-simulators` | Skip scanning iOS Simulator devices. |
| `--all` | List every item found (stale and fresh). Deletion still targets only stale ones. |

## Install

Clone anywhere, make the script executable, then symlink it into any directory on your `PATH`:

```sh
git clone https://github.com/silvansky/xcode-clr.git
chmod +x xcode-clr/xcode-clr
# pick whichever PATH dir you use:
ln -s "$PWD/xcode-clr/xcode-clr" /opt/homebrew/bin/xcode-clr   # Apple Silicon Homebrew
ln -s "$PWD/xcode-clr/xcode-clr" /usr/local/bin/xcode-clr      # Intel Homebrew
ln -s "$PWD/xcode-clr/xcode-clr" "$HOME/.local/bin/xcode-clr"  # user-local
```

Verify with `which xcode-clr && xcode-clr --dry-run`.

The shebang is `/usr/bin/python3` (system Python 3.9+, stdlib only — no `pip install` needed).

## Claude skill

The repo ships a [Claude Code](https://docs.claude.com/en/docs/claude-code) skill at `.claude/skills/xcode-clr/SKILL.md`. When loaded, Claude will reach for `xcode-clr` automatically on prompts like "free up disk space", "clean DerivedData", "Xcode is eating my drive". It defaults to `xcode-clr --json` for safe, structured inventory and only invokes destructive commands after you confirm.

Install once, globally:

```sh
ln -s "$PWD/xcode-clr/.claude/skills/xcode-clr" "$HOME/.claude/skills/xcode-clr"
```

(or run `claude` from inside this repo — project-local `.claude/skills` is picked up automatically).

Uninstall: `rm "$HOME/.claude/skills/xcode-clr"`.

## Configuration

Optional config at `~/.config/xcode-clr/config.json` (or `$XDG_CONFIG_HOME/xcode-clr/config.json`):

```json
{
  "worktree_roots": ["~/code/my-ios-app", "~/work/other-repo"],
  "threshold_days": 14,
  "auto_discover": true,
  "scan_simulators": true,
  "simulator_threshold_days": 14
}
```

Paths support `~` expansion.

Environment override: `XCODE_CLR_WORKTREE_ROOTS=/path/a:/path/b` (colon-separated).

Precedence: CLI flags > env > config file > built-in defaults. `worktree_roots` from all sources are merged & deduped.

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

`kind` is `"derived_data"`, `"worktree_build"`, or `"simulator"`. `reason` is `null` for kept items. Simulator items add `name`, `udid`, `state`, and `available`; their `last_accessed` is the device's last boot time.

## Examples

```sh
xcode-clr --dry-run                 # preview
xcode-clr --json | jq '.items[] | select(.to_be_removed)'
xcode-clr --yes                     # non-interactive cleanup
xcode-clr --days 30                 # only purge things older than 30d
xcode-clr --all --dry-run           # full inventory, stale and fresh
xcode-clr --worktree-root ~/work/repo-a --worktree-root ~/work/repo-b
xcode-clr --no-auto                 # only scan explicit/config roots
xcode-clr --no-simulators           # skip iOS Simulator devices
xcode-clr --simulator-days 30       # only purge sims unbooted >30d
xcode-clr --json | jq '.items[] | select(.kind=="simulator" and .to_be_removed)'
```

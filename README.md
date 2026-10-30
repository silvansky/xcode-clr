# xcode-clr

Delete stale Xcode DerivedData, worktree `build/` directories, long-unused iOS Simulators, and purgeable caches inside shut-down simulators.

Single-file Python 3 CLI. No dependencies. macOS only.

Supports Xcode 26.x and Xcode 27 (Device Hub) through `xcrun simctl`. Simulator activity uses `lastBootedAt` on Xcode 26.x and `lastUsedAt` on Xcode 27; if both are present, the newer valid timestamp wins. Available devices without a valid activity timestamp are kept and their caches/WALs are skipped.

## What it scans

- `~/Library/Developer/Xcode/DerivedData/*` — each project folder. Skips shared caches (`ModuleCache.noindex`, `SDKStatCaches.noindex`, `CompilationCache.noindex`).
- `build/` inside every git worktree of every project Xcode has built (auto-derived from DerivedData: `WorkspacePath` in `info.plist`, or local package paths in `SourcePackages/workspace-state.json`). Add extras with `--worktree-root`, disable auto with `--no-auto`.
- iOS Simulator devices (`xcrun simctl list devices`) not booted in a long time. Disable with `--no-simulators`.
- Purgeable simulator caches: abandoned `nsurlsessiond` downloads, the `coresymbolicationd` cache, dead app containers, `tmp`, and per-app `Library/Caches` / `tmp`. Only shut-down devices can be cleaned; booted devices are listed as available after shutdown. Only the contents are cleared, the device and its apps stay. Disable with `--no-simulator-caches`.
- Finance database WALs (`data/Library/Finance/finance_cloud.db-wal`) of at least 50 MB. Shut-down devices offer a SQLite `wal_checkpoint(TRUNCATE)`, preserving committed data while shrinking the WAL. Booted devices appear in the default output and JSON as available after shutdown. Disable with `--no-simulator-wals`.

## Staleness rules

An item is marked `to_be_removed` if **any** apply:

- DerivedData source no longer exists → `source missing`. Source is `WorkspacePath` from `info.plist`. Folders made by command-line `xcodebuild` have no `info.plist`, so the folder-name hash is matched against `.xcworkspace` / `.xcodeproj` / `Package.swift` paths in known worktrees; if nothing matches, `source missing` needs every local package path in `SourcePackages/workspace-state.json` to be gone.
- `max(LastAccessedDate, folder mtime, Logs/*/LogStoreManifest.plist mtime)` is older than 2 days → `stale >2d` (override with `--days`).
- Worktree `build/` folder mtime older than 2 days → `stale >2d`.
- Simulator last activity (`lastBootedAt` / `lastUsedAt`) older than 3 days → `stale >3d` (override with `--simulator-days`).
- Simulator whose runtime is no longer installed → `runtime unavailable`.
- Caches inside a shut-down simulator → `purgeable` (always; entries under 50 MB are hidden). The device's state is re-checked right before clearing.
- Finance WAL inside a shut-down simulator → `checkpoint`, regardless of age. Device state is re-checked before each checkpoint; locked databases report failure. WAL size is an upper estimate of savings because checkpointing can grow the database. WALs are never directly deleted.

Simulators that are **currently booted** or were **never booted** (Xcode's default templates) are excluded from normal device deletion. Booted devices need explicit idle shutdown before cleanup or reset; devices without a valid activity timestamp cannot be automatically reset.

## Usage

```
xcode-clr [--dry-run] [--json] [--yes] [--days N] [--worktree-root PATH]
```

| Flag | Effect |
|---|---|
| *(none)* | Print numbered table, then prompt: `a`ll, `n`one, or a selection like `1,3,5-7`. |
| `--dry-run` | Print table, never delete. |
| `--json` | Emit JSON to stdout (all scanned items, `to_be_removed` flag). No prompts, no delete. |
| `--yes` / `-y` | Skip confirmation; clean everything eligible, including resets only with `--reset-idle`. |
| `--days N` | Staleness threshold for DerivedData & worktree builds (default `2`). |
| `--simulator-days N` | Staleness threshold for simulators (default `3`). |
| `--worktree-root PATH` | Extra git worktree root to scan. Repeatable. |
| `--no-auto` | Disable auto-discovery of worktree roots from DerivedData. |
| `--no-simulators` | Skip scanning iOS Simulator devices. |
| `--no-simulator-caches` | Skip purgeable caches inside shut-down simulators. |
| `--no-simulator-wals` | Skip Finance database WAL checkpointing. |
| `--shutdown-idle` | Include idle booted devices in the cleanup selection: shut down, then clear caches/checkpoint WALs. |
| `--reset-idle` | Offer reset of idle simulators over 2 GB. Erases all installed apps, app data and settings; keeps the device. |
| `--idle-minutes N` | Minimum app inactivity for `--shutdown-idle` and `--reset-idle` (default `30`, positive integer). |
| `--all` | List every item found; cleanup still targets only eligible entries. |

The default output includes a separate **Available after simulator shutdown** table with device names, cache/WAL sizes, and a total estimate. These entries are excluded from normal cleanup, including `--yes`. The existing 50 MB minimum applies to each cache/WAL entry.

With `--shutdown-idle`, a device is considered idle when its simulator activity timestamp and all app-container modification/change times are older than `--idle-minutes`, and no app or test process is detected. Checks cover app data, shared app groups, and installed app bundles. Running UIKit apps block shutdown, except passive Spotlight and widget-rendering services. Any detected host `xcodebuild`, `xctest`, or test runner blocks automatic shutdown conservatively. System daemon writes (including Finance WAL growth) are not an activity signal.

This is a heuristic, not a record of user interaction. Unknown states, failed process checks, and unreadable containers block shutdown. Checks run again immediately before shutdown; cleanup requires confirmed `Shutdown` state. A process can start between checks. `--dry-run` and `--json` only preview, even with `--yes`; actual shutdown uses the normal selection prompt or `--yes`. Only devices with listed cache/WAL cleanup candidates are considered, and this option does not select them for device deletion.

```sh
xcode-clr --shutdown-idle --idle-minutes 60 --dry-run
xcode-clr --shutdown-idle --idle-minutes 60
```

## Large simulator resets

The default output warns about every simulator **over 2 GB** (2 GiB, matching the CLI's size units), even when it is recent and has no purgeable caches. These devices can contain gigabytes of Siri/language models, wallpaper assets and diagnostic data that ordinary cache cleanup leaves alone.

Use `--reset-idle` to include eligible large devices in the normal numbered selection. Reset runs `xcrun simctl erase <udid>`: it keeps the simulator device and its runtime, but **erases all installed apps, app data and settings**. System assets may download again after use. Sizes in the preview estimate savings; completed resets report measured allocation reduction.

Both shut-down and booted devices must pass the same conservative inactivity, app-container and host process checks described above. Shut-down devices do not need the in-device process inventory. Available devices need a valid activity timestamp older than `--idle-minutes`; unknown states, missing data, unreadable containers, running apps/test runners and failed checks block reset. Booted devices shut down only after a fresh idle check, followed by another activity check and confirmed `Shutdown` before erasing.

The reset flag replaces normal deletion for available large devices, including stale ones. A blocked reset does not fall back to device deletion. Cache/WAL operations for a selected reset are covered by that reset and skipped, including if it fails. Other eligible cleanup items remain selectable. `--no-simulators` / `scan_simulators: false` also disable reset candidates. Reset is never enabled by configuration or `--yes` alone.

```sh
xcode-clr --reset-idle --idle-minutes 60 --dry-run  # preview only
xcode-clr --reset-idle --idle-minutes 60            # select devices to reset
xcode-clr --reset-idle --idle-minutes 60 --json     # structured preview only
```

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
  "simulator_threshold_days": 14,
  "scan_simulator_caches": true,
  "scan_simulator_wals": true
}
```

Paths support `~` expansion.

Environment override: `XCODE_CLR_WORKTREE_ROOTS=/path/a:/path/b` (colon-separated).

Precedence: CLI flags > env > config file > built-in defaults. `worktree_roots` from all sources are merged & deduped.

## JSON output

```json
{
  "scanned_at": "2026-05-13T08:13:09Z",
  "threshold_days": 2,
  "items": [
    {
      "path": "/Users/.../DerivedData/Foo-abc",
      "kind": "derived_data",
      "source_path": "/Users/.../Foo.xcworkspace",
      "size_bytes": 1234567,
      "last_accessed": "2026-04-13T06:55:31Z",
      "mtime": "2026-04-13T06:55:31Z",
      "age_days": 30.05,
      "reason": "stale >2d",
      "to_be_removed": true
    }
  ]
}
```

`kind` is `"derived_data"`, `"worktree_build"`, `"simulator"`, `"simulator_cache"`, or `"simulator_wal"`. `reason` is `null` for kept items. Simulator items add `name`, `udid`, `state`, and `available`; their `last_accessed` is the device's last activity timestamp (`lastBootedAt` / `lastUsedAt`). Simulator-cache items also list `purge_paths`, the directories whose contents get cleared. WAL items use `path` for the WAL, `source_path` for the database, and `action: "checkpoint"`; `to_be_removed` means selected for checkpointing, not database deletion. Totals count a selected simulator only once, including its caches and WAL.

Cache/WAL items also include `requires_shutdown`, `shutdown_then_clean` (eligible with `--shutdown-idle`), and `idle_blocker` (why automatic shutdown was refused). Booted entries retain `to_be_removed: false`; `shutdown_then_clean` describes the conditional action separately. Top-level `after_shutdown_bytes` estimates cache/WAL space on devices requiring shutdown.

Simulator items also expose `oversized`, `to_be_reset`, `requires_shutdown`, `idle_blocker`, and `action` (`"reset"`, `"delete"`, or `null`). Reset candidates keep `reason: null` and `to_be_removed: false`; `to_be_reset` describes their eligibility with `--reset-idle`, including in previews. Top-level `simulator_reset_threshold_bytes` is `2147483648`. With `--reset-idle`, `after_shutdown_bytes` also includes booted reset candidates, counting each device and its contents once.

## Tests

`/usr/bin/python3 -m unittest -v` tests both Xcode timestamp schemas, staleness, previews, idle checks, reset safeguards, and WAL/cache cleanup using temporary data and mocked simulator commands. No real simulators are modified.

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
xcode-clr --no-simulator-caches     # leave simulator caches alone
xcode-clr --simulator-days 30       # only purge sims unbooted >30d
xcode-clr --json | jq '.items[] | select(.kind=="simulator" and .to_be_removed)'
```

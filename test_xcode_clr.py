import importlib.machinery
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch


loader = importlib.machinery.SourceFileLoader("xcode_clr", str(Path(__file__).with_name("xcode-clr")))
spec = importlib.util.spec_from_loader(loader.name, loader)
clr = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = clr
loader.exec_module(clr)


class SimulatorWalTests(unittest.TestCase):
    timestamp_field = "lastBootedAt"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.database = self.data / "Library/Finance/finance_cloud.db"
        self.database.parent.mkdir(parents=True)
        self.db = sqlite3.connect(self.database)
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA wal_autocheckpoint=0")
        self.db.execute("CREATE TABLE records (value TEXT)")
        self.db.execute("INSERT INTO records VALUES ('preserve me')")
        self.db.commit()
        self.wal = self.database.with_name("finance_cloud.db-wal")
        self.device = {
            "udid": "test-device", "dataPath": str(self.data), "name": "Test iPhone",
            "state": "Shutdown", "isAvailable": True, self.timestamp_field: "2026-10-20T00:00:00Z",
        }
        self.devices = {"com.apple.CoreSimulator.SimRuntime.iOS-18-6": [self.device]}
        self.now = datetime(2026, 10, 21, tzinfo=timezone.utc)

    def entries(self):
        entries = clr.find_simulator_wals(self.devices)
        clr.mark_stale(entries, 2, 3, self.now)
        clr.compute_sizes(entries)
        return entries

    def test_shutdown_wal_is_checkpoint_candidate(self):
        entry, = self.entries()
        self.assertEqual(entry.path, self.wal)
        self.assertEqual(entry.source_path, self.database)
        self.assertEqual(entry.kind, "simulator_wal")
        self.assertEqual(entry.reason, "checkpoint")
        self.assertGreater(entry.size_bytes, 0)

    def test_running_devices_are_visible_but_not_candidates(self):
        for state in ("Booted", "Booting", "Shutting Down", None):
            with self.subTest(state=state):
                self.device["state"] = state
                entry, = self.entries()
                self.assertIsNone(entry.reason)
                item, = json.loads(clr.render_json([entry], 2, 3, self.now))["items"]
                self.assertFalse(item["to_be_removed"])
                self.assertEqual(item["state"], state)
                self.assertEqual(item["action"], "checkpoint")

    def test_unavailable_and_never_booted_devices_are_skipped(self):
        self.device["isAvailable"] = False
        self.assertEqual(self.entries(), [])
        self.device["isAvailable"] = True
        self.device.pop(self.timestamp_field)
        self.assertEqual(self.entries(), [])

    def test_missing_database_or_wal_is_skipped(self):
        self.db.close()
        self.wal.unlink(missing_ok=True)
        self.assertEqual(self.entries(), [])
        self.database.unlink()
        self.wal.write_bytes(b"orphan")
        self.assertEqual(self.entries(), [])

    def test_checkpoint_preserves_committed_rows_and_truncates_wal(self):
        before = clr.du_bytes([self.database, self.wal, self.database.with_name("finance_cloud.db-shm")])
        freed = clr.checkpoint_wal(self.database)
        self.assertEqual(self.wal.stat().st_size, 0)
        self.db.close()
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT value FROM records").fetchall(), [("preserve me",)])
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        self.assertGreaterEqual(freed, 0)
        self.assertLessEqual(freed, before)

    def test_busy_database_reports_failure_and_preserves_rows(self):
        self.db.execute("BEGIN IMMEDIATE")
        with self.assertRaises((RuntimeError, sqlite3.OperationalError)):
            clr.checkpoint_wal(self.database)
        self.assertGreater(self.wal.stat().st_size, 0)
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT value FROM records").fetchall(), [("preserve me",)])

    def test_missing_database_is_not_created(self):
        missing = self.data / "missing.db"
        with self.assertRaises(sqlite3.OperationalError):
            clr.checkpoint_wal(missing)
        self.assertFalse(missing.exists())

    def test_invalid_database_is_not_deleted(self):
        invalid = self.data / "invalid.db"
        invalid.write_bytes(b"not a database")
        with self.assertRaises(sqlite3.DatabaseError):
            clr.checkpoint_wal(invalid)
        self.assertEqual(invalid.read_bytes(), b"not a database")

    def test_symlink_is_rejected(self):
        link = self.data / "linked.db"
        link.symlink_to(self.database)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            clr.checkpoint_wal(link)

    def test_cleanup_rechecks_state_and_fails_closed(self):
        entry, = self.entries()
        for states in ({entry.udid: "Booted"}, {}):
            with self.subTest(states=states), patch.object(clr, "device_states", return_value=states), \
                    patch.object(clr, "checkpoint_wal") as checkpoint, redirect_stderr(io.StringIO()):
                self.assertEqual(clr.delete_entries([entry]), 0)
                checkpoint.assert_not_called()
        self.assertGreater(self.wal.stat().st_size, 0)

    def test_cleanup_checkpoints_shutdown_device(self):
        entry, = self.entries()
        output = io.StringIO()
        with patch.object(clr, "device_states", return_value={entry.udid: "Shutdown"}), redirect_stdout(output):
            freed = clr.delete_entries([entry])
        self.assertEqual(self.wal.stat().st_size, 0)
        self.assertIn("checkpointed", output.getvalue())
        self.assertGreaterEqual(freed, 0)
        self.assertTrue(self.database.exists())

    def test_selected_simulator_covers_its_wal(self):
        entry, = self.entries()
        simulator = clr.Entry(self.data.parent, "simulator", None, self.now,
                              udid=entry.udid, size_bytes=1000000)
        self.assertEqual(clr.total_size([entry, simulator]), 1000000)
        with patch.object(clr.subprocess, "run") as run, patch.object(clr, "checkpoint_wal") as checkpoint, \
                redirect_stdout(io.StringIO()):
            run.return_value.returncode = 0
            self.assertEqual(clr.delete_entries([entry, simulator]), 1000000)
            checkpoint.assert_not_called()
            self.assertEqual(run.call_args.args[0], ["xcrun", "simctl", "delete", entry.udid])

    def run_main(self, *flags, config=None, minimum=1, caches=False, selected=None, answer="n"):
        output = io.StringIO()
        cache_flags = [] if caches else ["--no-simulator-caches"]
        with patch.object(sys, "argv", ["xcode-clr", "--no-auto", "--no-simulators", *cache_flags, *flags]), \
                patch.object(clr, "DERIVED_DATA", self.data / "absent"), \
                patch.object(clr, "load_config", return_value=config or {}), \
                patch.object(clr, "env_worktree_roots", return_value=[]), \
                patch.object(clr, "simctl_devices", return_value=self.devices), \
                patch.object(clr, "SIM_CACHE_MIN_BYTES", minimum), \
                patch.object(clr.sqlite3, "connect") as connect, \
                patch.object(clr, "delete_entries", return_value=0) as delete, \
                patch("builtins.input", return_value=answer), redirect_stdout(output):
            self.assertEqual(clr.main(), 0)
            connect.assert_not_called()
            if selected is None:
                delete.assert_not_called()
            else:
                delete.assert_called_once()
                self.assertEqual([(e.udid, e.kind) for e in delete.call_args.args[0]], selected)
        return output.getvalue()

    def test_json_and_dry_run_never_open_database_even_with_yes(self):
        before = self.wal.read_bytes()
        item, = json.loads(self.run_main("--json", "--yes"))["items"]
        self.assertTrue(item["to_be_removed"])
        self.assertEqual(item["action"], "checkpoint")
        self.assertIn("checkpoint", self.run_main("--dry-run", "--yes"))
        self.assertEqual(self.wal.read_bytes(), before)

    def test_flag_config_and_size_threshold_disable_candidates(self):
        self.assertEqual(json.loads(self.run_main("--json", "--no-simulator-wals"))["items"], [])
        self.assertEqual(json.loads(self.run_main("--json", config={"scan_simulator_wals": False}))["items"], [])
        self.assertEqual(json.loads(self.run_main("--json", minimum=50 * 1024 * 1024))["items"], [])

    def test_dry_run_explains_booted_wals(self):
        self.device["state"] = "Booted"
        output = self.run_main("--dry-run")
        self.assertIn("Available after simulator shutdown", output)
        self.assertIn("Test iPhone", output)
        self.assertIn("simulator_wal", output)
        self.assertIn("shutdown required", output)
        self.assertIn("Nothing to clean", output)

    def make_cache(self):
        cache = self.data / "tmp"
        cache.mkdir(exist_ok=True)
        (cache / "payload").write_bytes(b"x" * 4096)
        return cache

    def test_booted_caches_are_visible_and_excluded_from_cleanup(self):
        self.make_cache()
        self.device["state"] = "Booted"
        data = json.loads(self.run_main("--json", caches=True))
        self.assertEqual({e["kind"] for e in data["items"]}, {"simulator_cache", "simulator_wal"})
        self.assertTrue(all(e["requires_shutdown"] and not e["to_be_removed"] for e in data["items"]))
        self.assertEqual(data["after_shutdown_bytes"], sum(e["size_bytes"] for e in data["items"]))
        for flags in ((), ("--dry-run",), ("--all", "--dry-run"), ("--yes",)):
            with self.subTest(flags=flags):
                output = self.run_main(*flags, caches=True)
                self.assertIn("simulator_cache", output)
                self.assertIn("After shutdown:", output)
        self.assertTrue((self.data / "tmp/payload").exists())

    def test_shutdown_cache_remains_eligible_and_state_is_rechecked(self):
        cache = self.make_cache()
        entry, = clr.find_simulator_caches(self.devices)
        clr.mark_stale([entry], 2, 3, self.now)
        self.assertEqual(entry.reason, "purgeable")
        with patch.object(clr, "device_states", return_value={entry.udid: "Booted"}), redirect_stderr(io.StringIO()):
            self.assertEqual(clr.delete_entries([entry]), 0)
        self.assertTrue((cache / "payload").exists())

    def test_idle_requires_quiet_containers_and_no_apps_or_runners(self):
        self.device["state"] = "Booted"
        self.make_cache()
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        quiet = [Mock(stdout="/sbin/launchd\n"), Mock(stdout="PID Status Label\n- 0 inactive.app\n")]
        with patch.object(clr.subprocess, "run", side_effect=quiet):
            self.assertIsNone(clr.simulator_idle_blocker(self.device, 30, future))
        app_data = self.data / "Containers/Data/Application/app/Documents"
        app_data.mkdir(parents=True)
        active = app_data / "recent"
        active.write_text("activity")
        os.utime(active, (future.timestamp(), future.timestamp()))
        with patch.object(clr.subprocess, "run", side_effect=quiet):
            self.assertIn("app-container activity", clr.simulator_idle_blocker(self.device, 30, future))
        for command in ("/usr/bin/xcodebuild", "/usr/bin/xctest", "/apps/Tests-Runner"):
            with self.subTest(command=command), patch.object(clr.subprocess, "run", return_value=Mock(stdout=command)):
                self.assertIn("runner active", clr.simulator_idle_blocker(self.device, 30, future))
        with patch.object(clr.subprocess, "run", side_effect=[quiet[0], Mock(stdout="PID Status Label\n123 0 UIKitApplication:example.app[1]\n")]):
            self.assertIn("app/test running", clr.simulator_idle_blocker(self.device, 30, future))

    def test_idle_fails_closed_on_unknown_state_recent_boot_and_process_errors(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        self.assertIn("Shutdown", clr.simulator_idle_blocker(self.device, 30, future))
        self.device["state"] = "Booted"
        for output in ("", "unrecognized output"):
            with self.subTest(output=output), patch.object(clr.subprocess, "run", return_value=Mock(stdout=output)):
                self.assertIn("inventory", clr.simulator_idle_blocker(self.device, 30, future))
        with patch.object(clr.subprocess, "run", side_effect=subprocess.TimeoutExpired("ps", 10)):
            self.assertIn("failed", clr.simulator_idle_blocker(self.device, 30, future))
        self.device[self.timestamp_field] = future.isoformat()
        self.assertIn("recent", clr.simulator_idle_blocker(self.device, 30, future))

    def test_idle_preview_never_shuts_down_even_with_yes(self):
        self.device["state"] = "Booted"
        with patch.object(clr, "simulator_idle_blocker", return_value=None), \
                patch.object(clr, "shutdown_idle_simulator") as shutdown:
            for flag in ("--json", "--dry-run"):
                output = self.run_main("--shutdown-idle", "--yes", flag)
                if flag == "--json":
                    item, = json.loads(output)["items"]
                    self.assertTrue(item["shutdown_then_clean"])
                    self.assertFalse(item["to_be_removed"])
            shutdown.assert_not_called()

    def test_idle_option_selects_only_idle_devices_and_respects_decline(self):
        self.device["state"] = "Booted"
        with patch.object(clr, "simulator_idle_blocker", return_value=None):
            self.run_main("--shutdown-idle", answer="n")
            self.run_main("--shutdown-idle", "--yes", selected=[("test-device", "simulator_wal")])
        with patch.object(clr, "simulator_idle_blocker", return_value="app running"):
            output = self.run_main("--shutdown-idle", "--yes")
            self.assertIn("skipped — app running", output)

    def test_mixed_devices_keep_booted_items_out_of_normal_selection(self):
        booted = dict(self.device, udid="booted-device", state="Booted", name="Busy iPhone")
        self.devices[next(iter(self.devices))].append(booted)
        self.run_main("--yes", selected=[("test-device", "simulator_wal")])
        with patch.object(clr, "simulator_idle_blocker", return_value=None):
            self.run_main("--shutdown-idle", answer="2", selected=[("booted-device", "simulator_wal")])

    def test_passive_system_jobs_do_not_block_idle_shutdown(self):
        self.device["state"] = "Booted"
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        jobs = "PID Status Label\n1 0 com.apple.financed\n2 0 UIKitApplication:com.apple.Spotlight[1]\n3 0 UIKitApplication:com.apple.chrono.WidgetRenderer-Default[1]\n"
        with patch.object(clr.subprocess, "run", side_effect=[Mock(stdout="/sbin/launchd\n"), Mock(stdout=jobs)]):
            self.assertIsNone(clr.simulator_idle_blocker(self.device, 30, future))

    def test_unreadable_app_container_blocks_idle_shutdown(self):
        self.device["state"] = "Booted"
        (self.data / "Containers/Data/Application").mkdir(parents=True)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        with patch.object(clr.subprocess, "run", side_effect=[Mock(stdout="/sbin/launchd\n"), Mock(stdout="PID Status Label\n")]), \
                patch.object(clr.os, "walk", side_effect=PermissionError("denied")):
            self.assertIn("activity check failed", clr.simulator_idle_blocker(self.device, 30, future))

    def test_shutdown_rechecks_activity_and_confirms_state(self):
        self.device["state"] = "Booted"
        with patch.object(clr, "simctl_devices", return_value=self.devices), \
                patch.object(clr, "simulator_idle_blocker", return_value="app running"), \
                patch.object(clr.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "not idle"):
                clr.shutdown_idle_simulator("test-device", 30)
            run.assert_not_called()
        with patch.object(clr, "simctl_devices", return_value=self.devices), \
                patch.object(clr, "simulator_idle_blocker", return_value=None), \
                patch.object(clr.subprocess, "run") as run, \
                patch.object(clr, "device_states", return_value={"test-device": "Booted"}):
            with self.assertRaisesRegex(RuntimeError, "not confirmed"):
                clr.shutdown_idle_simulator("test-device", 30)
            self.assertEqual(run.call_args.args[0], ["xcrun", "simctl", "shutdown", "test-device"])

    def test_shutdown_once_before_cleaning_wal_and_cache(self):
        self.make_cache()
        self.device["state"] = "Booted"
        entries = self.entries() + clr.find_simulator_caches(self.devices)
        for entry in entries:
            entry.shutdown_idle = True
        with patch.object(clr, "shutdown_idle_simulator") as shutdown, \
                patch.object(clr, "device_states", return_value={"test-device": "Shutdown"}), \
                redirect_stdout(io.StringIO()):
            clr.delete_entries(entries, idle_minutes=30)
            shutdown.assert_called_once_with("test-device", 30)
        self.assertEqual(self.wal.stat().st_size, 0)
        self.assertFalse((self.data / "tmp/payload").exists())

    def test_shutdown_success_checks_fresh_device_and_threshold(self):
        self.device["state"] = "Booted"
        with patch.object(clr, "simctl_devices", return_value=self.devices), \
                patch.object(clr, "simulator_idle_blocker", return_value=None) as blocker, \
                patch.object(clr.subprocess, "run") as run, \
                patch.object(clr, "device_states", return_value={"test-device": "Shutdown"}), \
                redirect_stdout(io.StringIO()):
            clr.shutdown_idle_simulator("test-device", 60)
            self.assertEqual(blocker.call_args.args[:2], (self.device, 60))
            self.assertEqual(run.call_args.args[0], ["xcrun", "simctl", "shutdown", "test-device"])

    def test_failed_shutdown_does_not_clean_or_retry_same_device(self):
        self.make_cache()
        self.device["state"] = "Booted"
        entries = self.entries() + clr.find_simulator_caches(self.devices)
        for entry in entries:
            entry.shutdown_idle = True
        with patch.object(clr, "shutdown_idle_simulator", side_effect=RuntimeError("busy")) as shutdown, \
                patch.object(clr, "checkpoint_wal") as checkpoint, \
                patch.object(clr, "clear_dir") as clear, redirect_stderr(io.StringIO()):
            self.assertEqual(clr.delete_entries(entries, idle_minutes=30), 0)
            shutdown.assert_called_once()
            checkpoint.assert_not_called()
            clear.assert_not_called()

    def test_idle_option_handles_disabled_scans_and_rejects_invalid_threshold(self):
        self.run_main("--shutdown-idle", "--no-simulator-wals", "--json")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ex:
            self.run_main("--shutdown-idle", "--idle-minutes", "0", "--json")
        self.assertEqual(ex.exception.code, 2)


class SimulatorWalXcode27Tests(SimulatorWalTests):
    # Exercise the same scanner, preview, cleanup and shutdown safeguards with
    # the Xcode 27 simctl schema; all data and cleanup remain temporary/mocked.
    timestamp_field = "lastUsedAt"


class SimulatorActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        (self.data / "tmp").mkdir(parents=True)
        self.device = {
            "udid": "test-device", "dataPath": str(self.data), "name": "Test iPhone",
            "state": "Shutdown", "isAvailable": True, "dataPathSize": 1024,
        }
        self.devices = {"com.apple.CoreSimulator.SimRuntime.iOS-27-0": [self.device]}
        self.now = datetime(2026, 10, 30, tzinfo=timezone.utc)
        self.old = self.now - timedelta(days=10)
        self.recent = self.now - timedelta(minutes=5)

    def test_stale_and_recent_devices_on_both_xcode_versions(self):
        for field in ("lastBootedAt", "lastUsedAt"):
            for activity, reason in ((self.old, "stale >3d"), (self.recent, None)):
                with self.subTest(field=field, activity=activity):
                    self.device.pop("lastBootedAt", None)
                    self.device.pop("lastUsedAt", None)
                    self.device[field] = activity.isoformat()
                    entry, = clr.find_simulators(self.devices)
                    clr.mark_stale([entry], 2, 3, self.now)
                    self.assertEqual(entry.last_accessed, activity)
                    self.assertEqual(entry.reason, reason)
                    item, = json.loads(clr.render_json([entry], 2, 3, self.now))["items"]
                    self.assertEqual(item["to_be_removed"], reason is not None)
                    self.assertAlmostEqual(item["age_days"], (self.now - activity).total_seconds() / 86400, places=2)

    def test_newer_timestamp_protects_device_and_blocks_idle_shutdown(self):
        self.device["state"] = "Booted"
        for older_field, newer_field in (("lastBootedAt", "lastUsedAt"), ("lastUsedAt", "lastBootedAt")):
            with self.subTest(newer_field=newer_field):
                self.device[older_field] = self.old.isoformat()
                self.device[newer_field] = self.recent.isoformat()
                self.assertEqual(clr.simulator_last_activity(self.device), self.recent)
                with patch.object(clr.subprocess, "run") as run:
                    self.assertIn("recent", clr.simulator_idle_blocker(self.device, 30, self.now))
                    run.assert_not_called()
                self.device["state"] = "Shutdown"
                entry, = clr.find_simulators(self.devices)
                clr.mark_stale([entry], 2, 3, self.now)
                self.assertIsNone(entry.reason)
                self.device["state"] = "Booted"

    def test_missing_or_invalid_timestamps_keep_devices_and_skip_caches(self):
        for value in (None, "", "invalid"):
            with self.subTest(value=value):
                self.device.update(lastBootedAt=value, lastUsedAt=value)
                entry, = clr.find_simulators(self.devices)
                clr.mark_stale([entry], 2, 3, self.now)
                self.assertIsNone(entry.last_accessed)
                self.assertIsNone(entry.reason)
                self.assertEqual(clr.find_simulator_caches(self.devices), [])
                self.device["state"] = "Booted"
                with patch.object(clr.subprocess, "run") as run:
                    self.assertIn("unknown", clr.simulator_idle_blocker(self.device, 30, self.now))
                    run.assert_not_called()
                self.device["state"] = "Shutdown"

    def test_valid_timestamp_survives_invalid_other_field(self):
        for valid_field, invalid_field in (("lastBootedAt", "lastUsedAt"), ("lastUsedAt", "lastBootedAt")):
            with self.subTest(valid_field=valid_field):
                self.device[valid_field] = self.old.isoformat()
                self.device[invalid_field] = "invalid"
                self.assertEqual(clr.simulator_last_activity(self.device), self.old)

    def test_timestamps_are_normalized_to_utc(self):
        self.device.update(lastBootedAt="2026-10-20T03:00:00+03:00", lastUsedAt="2026-10-20T01:00:00")
        self.assertEqual(clr.simulator_last_activity(self.device), datetime(2026, 10, 20, 1, tzinfo=timezone.utc))

    def test_booted_device_with_old_activity_is_kept_on_both_versions(self):
        self.device["state"] = "Booted"
        for field in ("lastBootedAt", "lastUsedAt"):
            with self.subTest(field=field):
                self.device.pop("lastBootedAt", None)
                self.device.pop("lastUsedAt", None)
                self.device[field] = self.old.isoformat()
                entry, = clr.find_simulators(self.devices)
                clr.mark_stale([entry], 2, 3, self.now)
                self.assertIsNone(entry.reason)


class SimulatorResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.data.mkdir()
        self.now = datetime.now(timezone.utc)
        self.device = {
            "udid": "test-device", "dataPath": str(self.data), "name": "Large iPhone",
            "state": "Shutdown", "isAvailable": True, "dataPathSize": 3 * 1024 ** 3,
            "lastUsedAt": (self.now - timedelta(hours=1)).isoformat(),
        }
        self.devices = {"com.apple.CoreSimulator.SimRuntime.iOS-27-0": [self.device]}

    def entry(self):
        entry, = clr.find_simulators(self.devices)
        clr.mark_stale([entry], 2, 3, self.now)
        return entry

    def run_main(self, *flags, answer="n", blocker=None, caches=False):
        output = io.StringIO()
        cache_flags = [] if caches else ["--no-simulator-caches"]
        with patch.object(sys, "argv", ["xcode-clr", "--no-auto", *cache_flags,
                                       "--no-simulator-wals", *flags]), \
                patch.object(clr, "load_config", return_value={}), \
                patch.object(clr, "find_derived_data", return_value=[]), \
                patch.object(clr, "env_worktree_roots", return_value=[]), \
                patch.object(clr, "simctl_devices", return_value=self.devices), \
                patch.object(clr, "simulator_idle_blocker", return_value=blocker), \
                patch.object(clr, "delete_entries", return_value=0) as delete, \
                patch.object(clr, "reset_idle_simulator") as reset, \
                patch("builtins.input", return_value=answer) as prompt, redirect_stdout(output):
            self.assertEqual(clr.main(), 0)
            reset.assert_not_called()
        return output.getvalue(), delete, prompt

    def test_warning_and_reset_offer_are_visible_without_all(self):
        for flags in (("--dry-run",), ("--yes",), ()):
            with self.subTest(flags=flags):
                output, delete, prompt = self.run_main(*flags)
                self.assertIn("Warning: simulators over 2 GB", output)
                self.assertIn("Large iPhone", output)
                self.assertIn("--reset-idle", output)
                self.assertIn("ALL installed apps, app data and settings", output)
                delete.assert_not_called()
                prompt.assert_not_called()

    def test_strict_size_boundary_and_disabled_scan(self):
        for size in (clr.SIM_RESET_MIN_BYTES - 1, clr.SIM_RESET_MIN_BYTES, clr.SIM_RESET_MIN_BYTES + 1):
            self.device["dataPathSize"] = size
            output, _, _ = self.run_main("--dry-run")
            self.assertEqual("Warning:" in output, size > clr.SIM_RESET_MIN_BYTES)
        output, delete, _ = self.run_main("--reset-idle", "--no-simulators", "--yes")
        self.assertNotIn("Warning:", output)
        delete.assert_not_called()

    def test_preview_only_and_json_distinguishes_reset_from_deletion(self):
        for state in ("Booted", "Shutdown"):
            self.device["state"] = state
            for mode in ("--json", "--dry-run"):
                with self.subTest(state=state, mode=mode):
                    output, delete, prompt = self.run_main("--reset-idle", "--yes", mode)
                    delete.assert_not_called()
                    prompt.assert_not_called()
                    if mode == "--json":
                        data = json.loads(output)
                        item, = data["items"]
                        self.assertTrue(item["oversized"])
                        self.assertTrue(item["to_be_reset"])
                        self.assertFalse(item["to_be_removed"])
                        self.assertEqual(item["action"], "reset")
                        self.assertEqual(item["requires_shutdown"], state == "Booted")
                        self.assertEqual(data["simulator_reset_threshold_bytes"], 2 * 1024 ** 3)
                    else:
                        self.assertIn("shutdown + reset" if state == "Booted" else "reset", output)

    def test_reset_requires_flag_and_normal_selection_or_yes(self):
        for state in ("Booted", "Shutdown"):
            self.device["state"] = state
            _, delete, _ = self.run_main("--reset-idle", answer="n")
            delete.assert_not_called()
            for flags, answer in ((("--reset-idle",), "1"), (("--reset-idle", "--yes"), "n")):
                _, delete, _ = self.run_main(*flags, answer=answer)
                delete.assert_called_once()
                entry, = delete.call_args.args[0]
                self.assertTrue(entry.reset_idle)
                self.assertEqual(delete.call_args.kwargs["idle_minutes"], 30)

    def test_blocked_reset_does_not_fall_back_to_device_deletion(self):
        self.device["lastUsedAt"] = (self.now - timedelta(days=10)).isoformat()
        output, delete, _ = self.run_main("--reset-idle", "--yes", blocker="test/build runner active")
        delete.assert_not_called()
        self.assertIn("reset skipped — test/build runner active", output)
        output, delete, _ = self.run_main("--yes")
        self.assertEqual(delete.call_args.args[0][0].reason, "stale >3d")

    def test_reset_eligibility_uses_both_timestamp_schemas_and_fails_closed(self):
        for field in ("lastBootedAt", "lastUsedAt"):
            self.device.pop("lastBootedAt", None)
            self.device.pop("lastUsedAt", None)
            self.device[field] = (self.now - timedelta(hours=1)).isoformat()
            with patch.object(clr.subprocess, "run", return_value=Mock(stdout="/sbin/launchd\n")):
                entry = self.entry()
                clr.prepare_simulator_resets([entry], self.devices, 30, self.now)
                self.assertTrue(entry.reset_idle)
            for value in (None, "invalid", self.now.isoformat()):
                self.device[field] = value
                entry = self.entry()
                clr.prepare_simulator_resets([entry], self.devices, 30, self.now)
                self.assertFalse(entry.reset_idle)
                self.assertIn("recent or unknown", entry.idle_blocker)

    def test_reset_skips_unavailable_devices_and_unknown_states(self):
        self.device["isAvailable"] = False
        entry = self.entry()
        with patch.object(clr, "simulator_idle_blocker") as check:
            clr.prepare_simulator_resets([entry], self.devices, 30, self.now)
            check.assert_not_called()
        self.assertFalse(entry.reset_idle)
        self.assertEqual(entry.reason, "runtime unavailable")
        self.device["isAvailable"] = True
        for state in (None, "Booting", "Shutting Down"):
            self.device["state"] = state
            entry = self.entry()
            clr.prepare_simulator_resets([entry], self.devices, 30, self.now)
            self.assertFalse(entry.reset_idle)

    def test_shutdown_device_still_checks_processes_and_app_containers(self):
        with patch.object(clr.subprocess, "run", return_value=Mock(stdout="/usr/bin/xcodebuild\n")):
            self.assertIn("runner active", clr.simulator_idle_blocker(self.device, 30, self.now, allow_shutdown=True))
        app = self.data / "Containers/Data/Application/app"
        app.mkdir(parents=True)
        with patch.object(clr.subprocess, "run", return_value=Mock(stdout="/sbin/launchd\n")) as run:
            self.assertIn("app-container activity", clr.simulator_idle_blocker(self.device, 30, self.now, allow_shutdown=True))
            run.assert_called_once()  # no simctl spawn on a shut-down simulator
        with patch.object(clr.subprocess, "run", side_effect=PermissionError("denied")):
            self.assertIn("failed", clr.simulator_idle_blocker(self.device, 30, self.now, allow_shutdown=True))

    def test_reset_rechecks_idle_and_shutdown_and_measures_savings(self):
        with patch.object(clr, "simctl_devices", return_value=self.devices), \
                patch.object(clr, "simulator_idle_blocker", return_value=None) as check, \
                patch.object(clr, "device_states", return_value={"test-device": "Shutdown"}), \
                patch.object(clr, "du_bytes", side_effect=[3000, 100]), \
                patch.object(clr.subprocess, "run") as run:
            self.assertEqual(clr.reset_idle_simulator("test-device", 60), 2900)
            self.assertEqual(check.call_args.args[:2], (self.device, 60))
            self.assertTrue(check.call_args.kwargs["allow_shutdown"])
            self.assertEqual(run.call_args.args[0], ["xcrun", "simctl", "erase", "test-device"])
            self.assertTrue(run.call_args.kwargs["check"])

    def test_reset_shuts_down_booted_device_then_checks_again(self):
        self.device["state"] = "Booted"
        stopped = dict(self.device, state="Shutdown")
        with patch.object(clr, "simctl_devices", side_effect=[self.devices, {"runtime": [stopped]}]), \
                patch.object(clr, "simulator_idle_blocker", return_value=None) as check, \
                patch.object(clr, "shutdown_idle_simulator") as shutdown, \
                patch.object(clr, "device_states", return_value={"test-device": "Shutdown"}), \
                patch.object(clr, "du_bytes", return_value=1), \
                patch.object(clr.subprocess, "run") as run:
            self.assertEqual(clr.reset_idle_simulator("test-device", 30), 0)
            shutdown.assert_called_once_with("test-device", 30)
            self.assertEqual(check.call_count, 2)
            self.assertEqual(run.call_args.args[0][2], "erase")

    def test_failed_activity_or_state_recheck_never_erases(self):
        for blocker, state in (("app running", "Shutdown"), (None, "Booted"), (None, None)):
            with self.subTest(blocker=blocker, state=state), \
                    patch.object(clr, "simctl_devices", return_value=self.devices), \
                    patch.object(clr, "simulator_idle_blocker", return_value=blocker), \
                    patch.object(clr, "device_states", return_value={"test-device": state}), \
                    patch.object(clr, "du_bytes", return_value=1), \
                    patch.object(clr.subprocess, "run") as run:
                with self.assertRaises(RuntimeError):
                    clr.reset_idle_simulator("test-device", 30)
                run.assert_not_called()
        for devices in ({}, {"runtime": [dict(self.device, isAvailable=False)]}):
            with patch.object(clr, "simctl_devices", return_value=devices), \
                    patch.object(clr.subprocess, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "missing or runtime unavailable"):
                    clr.reset_idle_simulator("test-device", 30)
                run.assert_not_called()

    def test_busy_after_shutdown_or_failed_shutdown_never_erases(self):
        self.device["state"] = "Booted"
        for shutdown_error in (None, RuntimeError("shutdown failed")):
            with patch.object(clr, "simctl_devices", return_value=self.devices), \
                    patch.object(clr, "simulator_idle_blocker", side_effect=[None, "new activity"]), \
                    patch.object(clr, "shutdown_idle_simulator", side_effect=shutdown_error), \
                    patch.object(clr.subprocess, "run") as run:
                with self.assertRaises(RuntimeError):
                    clr.reset_idle_simulator("test-device", 30)
                run.assert_not_called()

    def test_reset_covers_caches_and_wals_and_failure_does_not_clean_them(self):
        simulator = self.entry()
        simulator.reset_idle = True
        cache = clr.Entry(self.data, "simulator_cache", None, self.now, udid="test-device", size_bytes=100)
        wal = clr.Entry(self.data / "db-wal", "simulator_wal", None, self.now, udid="test-device", size_bytes=100)
        entries = [cache, wal, simulator]
        self.assertEqual(clr.total_size(entries), simulator.size_bytes)
        for error in (None, RuntimeError("busy"), subprocess.CalledProcessError(1, "simctl")):
            with patch.object(clr, "reset_idle_simulator", return_value=123, side_effect=error) as reset, \
                    patch.object(clr, "clear_dir") as clear, \
                    patch.object(clr, "checkpoint_wal") as checkpoint, \
                    patch.object(clr.subprocess, "run") as run, \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(clr.delete_entries(entries, idle_minutes=30), 123 if error is None else 0)
                reset.assert_called_once_with("test-device", 30)
                clear.assert_not_called()
                checkpoint.assert_not_called()
                run.assert_not_called()
        with patch.object(clr, "reset_idle_simulator") as reset, redirect_stderr(io.StringIO()):
            self.assertEqual(clr.delete_entries([simulator]), 0)
            reset.assert_not_called()

    def test_reset_selection_deduplicates_cache_cleanup(self):
        cache = clr.Entry(self.data, "simulator_cache", None, self.now, udid="test-device", size_bytes=100 * 1024 ** 2,
                          state="Shutdown", last_accessed=self.now)
        with patch.object(clr, "find_simulator_caches", return_value=[cache]):
            _, delete, _ = self.run_main("--reset-idle", "--yes", caches=True)
            selected, = delete.call_args.args
            self.assertEqual([e.kind for e in selected], ["simulator"])
            self.assertTrue(selected[0].reset_idle)
        simulator = self.entry()
        simulator.reset_idle = True
        self.device["state"] = "Booted"
        simulator.state = "Booted"
        cache.state = "Booted"
        data = json.loads(clr.render_json([simulator, cache], 2, 3, self.now))
        self.assertEqual(data["after_shutdown_bytes"], simulator.size_bytes)


if __name__ == "__main__":
    unittest.main()

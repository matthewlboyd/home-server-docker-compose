"""Exercise failed migrations, interrupted recovery, and the game exclusion."""

import importlib.machinery
import importlib.util
import fcntl
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader("image_updates", str(ROOT / "scripts/update-homelab-images"))
MODULE = importlib.util.module_from_spec(importlib.util.spec_from_loader(LOADER.name, LOADER))
LOADER.exec_module(MODULE)
SOURCE = (ROOT / "compose.yaml").read_text()
PINS = MODULE.images_from_text(SOURCE)


class ImagePolicyTests(unittest.TestCase):
    def test_only_three_pinned_stable_images_are_accepted(self):
        for image in ("brandawg93/peanut:latest", "brandawg93/peanut:6.0.1",
                      "attacker/peanut:6.0.1@sha256:" + "a" * 64,
                      "brandawg93/peanut:6.0.1-beta@sha256:" + "a" * 64):
            with self.subTest(image=image), self.assertRaises(MODULE.UpdateError):
                MODULE.validate_image("peanut", image)

    def test_game_cannot_be_added_to_downloaded_manifest(self):
        data = yaml.safe_load(SOURCE)
        data["services"]["zomboid"] = {"image": "some/game:1.2.3"}
        with self.assertRaises(MODULE.UpdateError):
            MODULE.images_from_text(yaml.safe_dump(data))

    def test_image_replacement_preserves_all_other_settings_and_comments(self):
        image = "brandawg93/peanut:6.0.1@sha256:" + "a" * 64
        changed = MODULE.replace_image(SOURCE, "peanut", image)
        self.assertEqual(changed.replace(image, PINS["peanut"]), SOURCE)

    def test_renovate_excludes_game_and_automerges_only_allowlist(self):
        config = json.loads((ROOT / "renovate.json").read_text())
        self.assertEqual(config["includePaths"], ["compose.yaml"])
        automatic = [rule for rule in config["packageRules"] if rule.get("automerge")]
        self.assertEqual(set(automatic[0]["matchPackageNames"]), set(MODULE.IMAGES.values()))
        self.assertFalse(config["packageRules"][-1]["enabled"])


class RolloutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = self.root / "stack"
        self.stack.mkdir()
        self.data = self.stack / "data" / "peanut"
        self.data.mkdir(parents=True)
        (self.data / "database").write_text("old schema")
        (self.stack / "compose.yaml").write_text(SOURCE)
        self.state = self.root / "state"
        self.state.mkdir()
        self.config = {"stack_dir": str(self.stack), "state_dir": str(self.state),
                       "config_file": "unused", "source_url": "https://example.com/compose.yaml"}
        self.updater = MODULE.Updater(self.config)
        self.events = []
        self.running = True
        self.bad = False
        self.fail_health = False
        self.target = "brandawg93/peanut:6.0.1@sha256:" + "a" * 64
        self.addCleanup(patch.stopall)
        patch.object(MODULE, "run", side_effect=self.fake_run).start()
        patch.object(self.updater, "inspect", side_effect=self.inspect).start()
        patch.object(self.updater, "compose", side_effect=self.compose).start()
        patch.object(self.updater, "health", side_effect=self.health).start()
        patch.object(self.updater, "wait_healthy").start()

    def fake_run(self, args, **kwargs):
        self.events.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"Id": "old-image-id"}])
        if args[0] == "du":
            return "100\tdata"
        if args[0] == "cp":
            shutil.copytree(args[-2], args[-1], symlinks=True)
        return ""

    def inspect(self, service):
        self.assertEqual(service, "peanut")
        return {"Image": "old-image-id", "State": {"Running": self.running, "ExitCode": 0},
                "Mounts": [{"Destination": "/config", "Source": str(self.data), "Type": "bind"}]}

    def compose(self, *args):
        self.events.append(list(args))
        self.assertEqual(args[-1], "peanut", "Only the selected service may be touched")
        if args[0] == "stop":
            self.running = False
        if args[0] in ("up", "start"):
            self.running = True
            pin = MODULE.images_from_text((self.stack / "compose.yaml").read_text())["peanut"]
            self.bad = pin == self.target
            if self.bad:
                (self.data / "database").write_text("new incompatible schema")
            if args[0] == "up":
                self.assertIn("--no-deps", args)
                self.assertIn("never", args)
        return ""

    def health(self):
        if self.bad and self.fail_health:
            raise MODULE.UpdateError("Failed authenticated dashboard")

    def test_success_keeps_recovery_copy_and_never_touches_game(self):
        self.updater.update("peanut", self.target)
        self.assertTrue(self.running)
        self.assertFalse(self.updater.journal.exists())
        self.assertEqual((self.state / "backups/peanut/0/database").read_text(), "old schema")
        self.assertEqual((self.data / "database").read_text(), "new incompatible schema")
        self.assertNotIn("zomboid", json.dumps(self.events))
        pull = next(i for i, args in enumerate(self.events) if args[:2] == ["docker", "pull"])
        stop = next(i for i, args in enumerate(self.events) if args[0] == "stop")
        self.assertLess(pull, stop)

    def test_bad_dashboard_restores_data_and_image_and_blocks_candidate(self):
        self.fail_health = True
        with self.assertRaises(MODULE.UpdateError):
            self.updater.update("peanut", self.target)
        self.assertEqual((self.data / "database").read_text(), "old schema")
        self.assertEqual(MODULE.images_from_text((self.stack / "compose.yaml").read_text())["peanut"], PINS["peanut"])
        self.assertEqual(json.loads(self.updater.failed_file.read_text())["peanut"], self.target)
        self.assertTrue(self.running)
        self.assertFalse(self.updater.journal.exists())

    def test_interrupted_migration_is_recoverable_on_next_run(self):
        self.updater.update("peanut", self.target)
        transaction = json.loads((self.state / "backups/peanut/transaction.json").read_text())
        self.updater.save_journal(transaction)
        self.updater.recover(transaction)
        self.assertEqual((self.data / "database").read_text(), "old schema")
        self.assertTrue(self.running)

    def test_partial_backup_never_restores_incomplete_data(self):
        transaction = {"service": "peanut", "previous": PINS["peanut"], "target": self.target,
                       "paths": [str(self.data)], "phase": "backing-up"}
        self.running = False
        self.updater.save_journal(transaction)
        self.updater.recover(transaction)
        self.assertTrue(self.running)
        self.assertEqual((self.data / "database").read_text(), "old schema")
        self.assertFalse(any(args[0] == "cp" for args in self.events))

    def test_stopped_service_is_not_started(self):
        self.running = False
        with self.assertRaises(MODULE.UpdateError):
            self.updater.update("peanut", self.target)
        self.assertEqual(self.events, [])

    def test_failed_pull_leaves_running_service_untouched(self):
        original = self.fake_run
        def fail_pull(args, **kwargs):
            if args[:2] == ["docker", "pull"]:
                raise MODULE.UpdateError("Registry unavailable")
            return original(args, **kwargs)
        with patch.object(MODULE, "run", side_effect=fail_pull), self.assertRaises(MODULE.UpdateError):
            self.updater.update("peanut", self.target)
        self.assertTrue(self.running)
        self.assertFalse(self.updater.journal.exists())
        self.assertFalse(any(args[0] == "stop" for args in self.events))

    def test_busy_backup_lock_prevents_downloads_and_container_changes(self):
        lock_path = self.root / "maintenance.lock"
        with lock_path.open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with patch.object(MODULE, "LOCK", str(lock_path)), patch.object(MODULE.urllib.request, "urlopen") as fetch:
                self.updater.execute(apply=True)
            fetch.assert_not_called()
        self.assertEqual(self.events, [])

    def test_only_npm_runs_a_staging_certificate_renewal(self):
        self.updater.renewal_check("peanut")
        self.updater.renewal_check("pihole")
        self.assertEqual(self.events, [])
        self.updater.renewal_check("nginx-proxy-manager")
        self.assertEqual(len(self.events), 1)
        self.assertIn("--dry-run", self.events[0])
        self.assertNotIn("--force-renewal", self.events[0])

    def test_failed_recovery_retains_journal_for_next_attempt(self):
        self.updater.update("peanut", self.target)
        transaction = json.loads((self.state / "backups/peanut/transaction.json").read_text())
        self.updater.save_journal(transaction)
        with patch.object(self.updater, "health", side_effect=MODULE.UpdateError("Still unhealthy")), self.assertRaises(MODULE.UpdateError):
            self.updater.recover(transaction)
        self.assertTrue(self.updater.journal.exists())
        self.updater.recover(transaction)
        self.assertFalse(self.updater.journal.exists())
        self.assertEqual((self.data / "database").read_text(), "old schema")


if __name__ == "__main__":
    unittest.main()

"""Exercise the read-only migration guard embedded in the Ansible task."""

import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest


TASK = Path(__file__).resolve().parents[1] / "ansible/tasks/backup-preflight.yml"
# Keep the deployed inline program as the test subject without a YAML dependency.
PROGRAM = textwrap.dedent(re.search(r"      - \|\n((?:          .*\n|\n)+)", TASK.read_text())[1])


class BackupPreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.paths = self.root / "selected paths"
        self.paths.write_text("/opt/homelab\n/etc\n")
        self.excludes = self.root / "selected exclusions"
        self.excludes.write_text("/etc/restic\n")
        self.backend = "/usr/local/sbin/restic-existing"

    def check(self, environment=None, properties=None, selected=None, raw_environment=None):
        values = {
            "RESTIC_COMMAND": self.backend,
            "RESTIC_PATHS_FILE": str(self.paths),
            "RESTIC_EXCLUDES_FILE": str(self.excludes),
        }
        values.update(environment or {})
        encoded = " ".join(shlex.quote(key + "=" + value) for key, value in values.items() if value is not None)
        fields = {"Environment": encoded if raw_environment is None else raw_environment}
        fields.update(properties or {})
        result = subprocess.run(
            [sys.executable, "-c", PROGRAM, selected or self.backend],
            input="\n".join(key + "=" + value for key, value in fields.items()),
            text=True, capture_output=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_custom_backend_and_paths_are_preserved_without_writing(self):
        before = (self.paths.read_bytes(), self.excludes.read_bytes())
        self.assertEqual(self.check(), {"ready": True})
        self.assertEqual((self.paths.read_bytes(), self.excludes.read_bytes()), before)
        self.assertEqual(set(self.root.iterdir()), {self.paths, self.excludes})

    def test_generic_backend_default_and_cleared_legacy_setting_are_allowed(self):
        for value in (None, ""):
            with self.subTest(value=value):
                self.assertEqual(self.check(
                    {"RESTIC_COMMAND": value, "BASE_BACKUP_COMMAND": ""},
                    selected="/usr/local/sbin/restic-server",
                ), {"ready": True})

    def test_systemctl_double_quoted_assignments_support_spaces(self):
        entries = ["RESTIC_COMMAND=" + self.backend, "RESTIC_PATHS_FILE=" + str(self.paths),
                   "RESTIC_EXCLUDES_FILE=" + str(self.excludes)]
        self.assertEqual(self.check(raw_environment=" ".join(json.dumps(entry) for entry in entries)), {"ready": True})

    def test_legacy_delegation_fails_before_missing_path_check(self):
        self.paths.unlink()
        result = self.check({"BASE_BACKUP_COMMAND": "/usr/local/sbin/old-backup"})
        self.assertIn("one-time backup migration", result["error"])
        self.assertIn("remove BASE_BACKUP_COMMAND", result["error"])

    def test_inventory_cannot_silently_switch_the_existing_backend(self):
        result = self.check(selected="/usr/local/sbin/restic-different")
        self.assertIn("differs from homelab_restic_command", result["error"])

    def test_missing_empty_directory_and_relative_paths_fail(self):
        empty = self.root / "empty"
        empty.touch()
        for value in (str(self.root / "absent"), str(empty), str(self.root), "relative-paths"):
            with self.subTest(value=value):
                self.assertIn("RESTIC_PATHS_FILE", self.check({"RESTIC_PATHS_FILE": value})["error"])

    def test_optional_excludes_can_be_absent_but_not_unreadable_paths(self):
        missing = self.root / "absent"
        self.assertEqual(self.check({"RESTIC_EXCLUDES_FILE": str(missing)}), {"ready": True})
        broken = self.root / "broken"
        broken.symlink_to(missing)
        for value in (str(self.root), str(broken), "relative-excludes"):
            with self.subTest(value=value):
                self.assertIn("RESTIC_EXCLUDES_FILE", self.check({"RESTIC_EXCLUDES_FILE": value})["error"])

    def test_indirect_migration_settings_require_review(self):
        for properties in (
            {"EnvironmentFiles": "/etc/backup.env (ignore_errors=no)"},
            {"PassEnvironment": "RESTIC_COMMAND"},
            {"UnsetEnvironment": "BASE_BACKUP_COMMAND=/usr/local/sbin/old-backup"},
        ):
            with self.subTest(properties=properties):
                self.assertIn("put the four backup migration variables directly", self.check(properties=properties)["error"])
        self.assertEqual(self.check(properties={"PassEnvironment": "LANG", "UnsetEnvironment": "LC_ALL"}), {"ready": True})

    def test_unrelated_secret_and_malformed_input_are_never_returned(self):
        secret = "private-value-must-never-appear"
        self.assertEqual(self.check({"UNRELATED_TOKEN": secret}), {"ready": True})
        for raw in ('RESTIC_COMMAND="' + secret, secret):
            result = self.check(raw_environment=raw)
            self.assertIn("Cannot safely inspect", result["error"])
            self.assertNotIn(secret, json.dumps(result))


if __name__ == "__main__":
    unittest.main()

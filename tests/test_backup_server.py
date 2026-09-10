"""Exercise backup ordering and recovery with fake Docker/restic commands."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/backup-server"
WEB_SERVICES = ["pihole", "nginx-proxy-manager", "peanut"]

FAKE_COMMAND = r'''
import json
import os
from pathlib import Path
import signal
import sys

state_path = Path(os.environ["BACKUP_TEST_STATE"])
events_path = Path(os.environ["BACKUP_TEST_EVENTS"])
state = json.loads(state_path.read_text())
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
cwd = str(Path.cwd())

def record(kind=None):
    event = {
        "tool": tool, "args": args, "cwd": cwd,
        "kind": kind, "web_running": dict(state["web_running"]),
        "game_running": state["game_running"],
    }
    with events_path.open("a") as handle:
        handle.write(json.dumps(event) + "\n")

def finish(code=0, output=None):
    state_path.write_text(json.dumps(state))
    if state.get("terminate_backup") and (
        tool == "base-backup" or (tool == "restic" and args[0] == "backup")
    ):
        os.kill(os.getppid(), signal.SIGTERM)
    if output is not None:
        print(output)
    sys.exit(code)

record()
if tool == "docker":
    if args[:2] == ["container", "ls"]:
        finish(state.get("list_failure", 0),
               "project-zomboid" if state["game_exists"] else "")
    if args[0] == "inspect":
        name = args[-1]
        template = args[2]
        if name != "project-zomboid":
            finish(0, str(state["web_running"].get(name, False)).lower())
        if not state["game_exists"]:
            finish(1)
        if template == "{{.State.Running}}":
            finish(0, str(state["game_running"]).lower())
        if "com.docker.compose.service" in template:
            finish(0, state.get("game_identity", "zomboid|" + state["game_dir"]))
        if "OOMKilled" in template:
            finish(0, "{} {} {}".format(
                str(state["game_running"]).lower(),
                str(state.get("game_oom", False)).lower(),
                state.get("game_exit", 0)))
    if args[0] == "exec":
        if args != ["exec", "project-zomboid", "rcon-cli", "-c",
                    "/home/steam/server/rcon.yml", "-T", "90s", "save"]:
            finish(90)
        finish(state.get("save_failure", 0))
    if args[:2] == ["compose", "stop"]:
        if cwd == state["game_dir"]:
            if args != ["compose", "stop", "zomboid"]:
                finish(91)
            state["game_running"] = state.get("still_running_after_stop", False)
            finish(state.get("game_stop_failure", 0))
        if cwd != state["web_dir"] or args[2:4] != ["--timeout", "30"]:
            finish(92)
        for service in args[4:]:
            state["web_running"][service] = False
        finish(state.get("web_stop_failure", 0))
    if args[:2] == ["compose", "start"]:
        service = args[2]
        if service == "zomboid":
            if cwd != state["game_dir"]:
                finish(93)
            if state.get("game_restart_failure"):
                finish(state["game_restart_failure"])
            state["game_running"] = True
        else:
            if cwd != state["web_dir"]:
                finish(94)
            if service in state.get("web_restart_failures", []):
                finish(72)
            state["web_running"][service] = True
        finish()
elif tool == "restic":
    if args[0] == "backup":
        record("snapshot")
        finish(state.get("backup_failure", 0))
    if args[0] == "forget":
        finish()
elif tool == "base-backup":
    # The existing helper owns Pi-hole and restores it even on failure.
    original = state["web_running"]["pihole"]
    state["web_running"]["pihole"] = False
    record("snapshot")
    state["web_running"]["pihole"] = original
    finish(state.get("backup_failure", 0))

finish(99)
'''


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.web_dir = self.root / "web"
        self.game_dir = self.root / "game"
        self.web_dir.mkdir()
        self.game_dir.mkdir()
        (self.game_dir / "compose.yaml").write_text("services: {}\n")
        self.state_path = self.root / "state.json"
        self.events_path = self.root / "events.jsonl"
        self.commands = {}
        for name in ("docker", "restic", "base-backup"):
            command = self.root / name
            command.write_text("#!" + sys.executable + "\n" + FAKE_COMMAND)
            command.chmod(0o700)
            self.commands[name] = command

    def run_backup(self, *, configured=True, delegated=False, **settings):
        state = {
            "web_dir": str(self.web_dir), "game_dir": str(self.game_dir),
            "web_running": {service: True for service in WEB_SERVICES},
            "game_exists": True, "game_running": True,
        }
        state.update(settings)
        self.state_path.write_text(json.dumps(state))
        self.events_path.write_text("")
        env = dict(os.environ)
        env.update(
            PIHOLE_STACK_DIR=str(self.web_dir),
            ZOMBOID_STACK_DIR=str(self.game_dir) if configured else "",
            RESTIC_PATHS_FILE=str(self.root / "backup-paths"),
            RESTIC_EXCLUDES_FILE=str(self.root / "missing-excludes"),
            RESTIC_COMMAND=str(self.commands["restic"]),
            DOCKER_COMMAND=str(self.commands["docker"]),
            BASE_BACKUP_COMMAND=str(self.commands["base-backup"]) if delegated else "",
            BACKUP_TEST_STATE=str(self.state_path),
            BACKUP_TEST_EVENTS=str(self.events_path),
        )
        result = subprocess.run(
            ["/bin/bash", str(SCRIPT)], cwd=self.root, env=env,
            text=True, capture_output=True, timeout=15,
        )
        self.events = [json.loads(line) for line in self.events_path.read_text().splitlines()]
        self.state = json.loads(self.state_path.read_text())
        return result

    def snapshots(self):
        return [event for event in self.events if event["kind"] == "snapshot"]

    def game_commands(self, action):
        return [event for event in self.events
                if event["tool"] == "docker" and event["cwd"] == str(self.game_dir)
                and event["args"][:2] == ["compose", action]]

    def assert_web_running(self):
        self.assertTrue(all(self.state["web_running"].values()))

    def test_unconfigured_game_is_untouched(self):
        result = self.run_backup(configured=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.state["game_running"])
        self.assertEqual(len(self.snapshots()), 1)
        self.assertFalse(any("project-zomboid" in event["args"] for event in self.events))
        self.assertFalse(any(event["args"][:2] == ["container", "ls"] for event in self.events))

    def test_absent_or_stopped_game_is_not_started(self):
        for settings in ({"game_exists": False, "game_running": False},
                         {"game_running": False}):
            with self.subTest(settings=settings):
                result = self.run_backup(**settings)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(self.state["game_running"])
                self.assertEqual(self.game_commands("start"), [])
                self.assertEqual(self.game_commands("stop"), [])
                self.assertEqual(len(self.snapshots()), 1)

    def test_running_game_saved_before_web_pause_and_recovered_in_both_modes(self):
        for delegated in (False, True):
            with self.subTest(delegated=delegated):
                result = self.run_backup(delegated=delegated)
                self.assertEqual(result.returncode, 0, result.stderr)
                snapshot = self.snapshots()[0]
                self.assertFalse(snapshot["game_running"])
                self.assertFalse(any(snapshot["web_running"].values()))
                save = next(event for event in self.events if event["args"][:1] == ["exec"])
                game_stop = self.game_commands("stop")[0]
                web_stop = next(event for event in self.events
                                if event["cwd"] == str(self.web_dir)
                                and event["args"][:2] == ["compose", "stop"])
                self.assertLess(self.events.index(save), self.events.index(game_stop))
                self.assertLess(self.events.index(game_stop), self.events.index(web_stop))
                self.assertLess(self.events.index(web_stop), self.events.index(snapshot))
                self.assertLess(self.events.index(snapshot), self.events.index(self.game_commands("start")[0]))
                self.assertTrue(self.state["game_running"])
                self.assert_web_running()
                if delegated:
                    self.assertNotIn("pihole", web_stop["args"])
                    self.assertFalse(any(event["tool"] == "restic" for event in self.events))
                else:
                    self.assertIn("pihole", web_stop["args"])
                    prune = next(event for event in self.events if event["args"][:1] == ["forget"])
                    self.assertTrue(prune["game_running"])
                    self.assertTrue(all(prune["web_running"].values()))

    def test_rcon_failure_aborts_before_any_service_stops(self):
        result = self.run_backup(save_failure=66)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshots(), [])
        self.assertEqual(self.game_commands("stop"), [])
        self.assertEqual(self.game_commands("start"), [])
        self.assertTrue(self.state["game_running"])
        self.assert_web_running()

    def test_unsafe_stop_aborts_snapshot_and_restores_game(self):
        for settings in ({"game_exit": 137}, {"game_oom": True},
                         {"still_running_after_stop": True}):
            with self.subTest(settings=settings):
                result = self.run_backup(**settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.snapshots(), [])
                self.assertTrue(self.state["game_running"])
                self.assertEqual(len(self.game_commands("start")), 1)
                self.assert_web_running()

    def test_sigterm_exit_code_is_allowed_after_successful_save(self):
        result = self.run_backup(game_exit=143)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.snapshots()), 1)

    def test_failed_stop_restores_game_without_taking_snapshot(self):
        result = self.run_backup(game_stop_failure=67)
        self.assertEqual(result.returncode, 67)
        self.assertEqual(self.snapshots(), [])
        self.assertTrue(self.state["game_running"])
        self.assert_web_running()

    def test_failed_backup_restores_both_stacks_and_preserves_error(self):
        for delegated in (False, True):
            with self.subTest(delegated=delegated):
                result = self.run_backup(delegated=delegated, backup_failure=71)
                self.assertEqual(result.returncode, 71)
                self.assertEqual(len(self.snapshots()), 1)
                self.assertTrue(self.state["game_running"])
                self.assert_web_running()
                self.assertFalse(any(event["args"][:1] == ["forget"] for event in self.events))

    def test_web_restart_failure_does_not_prevent_game_restart(self):
        for delegated in (False, True):
            with self.subTest(delegated=delegated):
                result = self.run_backup(delegated=delegated, web_restart_failures=["nginx-proxy-manager"])
                self.assertEqual(result.returncode, 72)
                self.assertTrue(self.state["game_running"])
                self.assertTrue(self.state["web_running"]["pihole"])
                self.assertTrue(self.state["web_running"]["peanut"])
                self.assertFalse(self.state["web_running"]["nginx-proxy-manager"])

    def test_interrupted_backup_restores_both_stacks(self):
        for delegated in (False, True):
            with self.subTest(delegated=delegated):
                result = self.run_backup(delegated=delegated, terminate_backup=True)
                self.assertEqual(result.returncode, 143)
                self.assertTrue(self.state["game_running"])
                self.assert_web_running()
                self.assertFalse(any(event["args"][:1] == ["forget"] for event in self.events))

    def test_game_restart_failure_is_reported_after_web_services_recover(self):
        for delegated in (False, True):
            with self.subTest(delegated=delegated):
                result = self.run_backup(delegated=delegated, game_restart_failure=73)
                self.assertEqual(result.returncode, 73)
                self.assertFalse(self.state["game_running"])
                self.assert_web_running()

    def test_partial_web_stop_failure_restores_both_stacks(self):
        result = self.run_backup(web_stop_failure=74)
        self.assertEqual(result.returncode, 74)
        self.assertEqual(self.snapshots(), [])
        self.assertTrue(self.state["game_running"])
        self.assert_web_running()

    def test_different_compose_owner_is_not_stopped(self):
        result = self.run_backup(game_identity="zomboid|/unrelated/project")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshots(), [])
        self.assertEqual(self.game_commands("stop"), [])
        self.assertTrue(self.state["game_running"])
        self.assert_web_running()

    def test_failed_game_inventory_does_not_proceed_to_snapshot(self):
        result = self.run_backup(list_failure=75)
        self.assertEqual(result.returncode, 75)
        self.assertEqual(self.snapshots(), [])
        self.assertTrue(self.state["game_running"])
        self.assert_web_running()


if __name__ == "__main__":
    unittest.main()

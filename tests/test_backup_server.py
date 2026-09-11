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
    if state.get("terminate_backup") and tool == "restic" and args[0] == "backup":
        os.kill(os.getppid(), signal.SIGTERM)
    if output is not None:
        print(output)
    sys.exit(code)

record()
if tool == "docker":
    if args[:2] == ["container", "ls"]:
        if "--filter" not in args:
            finish(state.get("web_list_failure", 0), "\n".join(state["web_running"]))
        finish(state.get("list_failure", 0),
               "project-zomboid" if state["game_exists"] else "")
    if args[0] == "inspect":
        name = args[-1]
        template = args[2]
        if name != "project-zomboid":
            if name in state.get("web_inspect_failures", {}):
                finish(state["web_inspect_failures"][name])
            finish(0, state.get("web_inspect_output", {}).get(
                name, str(state["web_running"].get(name, False)).lower()))
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
        self.paths_file = self.root / "backup-paths"
        self.paths_file.write_text(str(self.web_dir) + "\n" + str(self.game_dir) + "\n")
        self.state_path = self.root / "state.json"
        self.events_path = self.root / "events.jsonl"
        self.commands = {}
        for name in ("docker", "restic"):
            command = self.root / name
            command.write_text("#!" + sys.executable + "\n" + FAKE_COMMAND)
            command.chmod(0o700)
            self.commands[name] = command

    def run_backup(self, *, configured=True, environment=None, **settings):
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
            RESTIC_PATHS_FILE=str(self.paths_file),
            RESTIC_EXCLUDES_FILE=str(self.root / "missing-excludes"),
            RESTIC_COMMAND=str(self.commands["restic"]),
            DOCKER_COMMAND=str(self.commands["docker"]),
            BASE_BACKUP_COMMAND="",
            BACKUP_TEST_STATE=str(self.state_path),
            BACKUP_TEST_EVENTS=str(self.events_path),
        )
        env.update(environment or {})
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
        self.assertFalse(any(event["args"][:2] == ["container", "ls"] and "--filter" in event["args"]
                             for event in self.events))

    def test_failed_web_inspection_aborts_before_any_service_is_stopped(self):
        for service in WEB_SERVICES:
            with self.subTest(service=service):
                result = self.run_backup(web_inspect_failures={service: 74})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.snapshots(), [])
                self.assertTrue(self.state["game_running"])
                self.assert_web_running()
                self.assertFalse(any(event["args"][:2] in (["compose", "stop"], ["compose", "start"])
                                     for event in self.events))
                self.assertFalse(any(event["args"][:1] == ["exec"] for event in self.events))

    def test_legacy_delegation_is_rejected_before_docker_or_restic_runs(self):
        result = self.run_backup(environment={"BASE_BACKUP_COMMAND": "/usr/local/sbin/old-backup"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Migrate BASE_BACKUP_COMMAND", result.stderr)
        self.assertEqual(self.events, [])
        self.assertTrue(self.state["game_running"])
        self.assert_web_running()

    def test_missing_or_empty_paths_and_unusable_backend_abort_before_downtime(self):
        empty_file = self.root / "empty-paths"
        empty_file.touch()
        nonexecutable = self.root / "nonexecutable-backend"
        nonexecutable.write_text("#!/bin/sh\nexit 0\n")
        nonexecutable.chmod(0o600)
        environments = [
            {"RESTIC_PATHS_FILE": str(self.root / "missing-paths")},
            {"RESTIC_PATHS_FILE": str(empty_file)},
            {"RESTIC_PATHS_FILE": str(self.root)},
            {"RESTIC_COMMAND": str(self.root / "missing-backend")},
            {"RESTIC_COMMAND": str(nonexecutable)},
            {"RESTIC_COMMAND": str(self.root)},
            {"RESTIC_EXCLUDES_FILE": str(self.root)},
        ]
        if os.geteuid() != 0:
            unreadable = self.root / "unreadable-paths"
            unreadable.write_text(str(self.web_dir) + "\n")
            unreadable.chmod(0)
            environments.append({"RESTIC_PATHS_FILE": str(unreadable)})
        for environment in environments:
            with self.subTest(environment=environment):
                result = self.run_backup(environment=environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.events, [])
                self.assertTrue(self.state["game_running"])
                self.assert_web_running()

    def test_selected_backend_keeps_the_configured_scope_tag_and_retention(self):
        excludes = self.root / "excludes"
        excludes.write_text("/etc/restic\n")
        result = self.run_backup(environment={"RESTIC_EXCLUDES_FILE": str(excludes)})
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = self.snapshots()[0]
        self.assertEqual(snapshot["tool"], "restic")
        self.assertEqual(snapshot["args"], [
            "backup", "--files-from", str(self.paths_file), "--tag", "automated",
            "--exclude-file", str(excludes),
        ])
        prune = next(event for event in self.events if event["args"][:1] == ["forget"])
        self.assertEqual(prune["args"], [
            "forget", "--keep-daily", "7", "--keep-weekly", "4",
            "--keep-monthly", "12", "--prune",
        ])
        self.assertTrue(prune["game_running"])
        self.assertTrue(all(prune["web_running"].values()))

    def test_failed_web_inventory_or_unknown_state_aborts_before_downtime(self):
        for settings in ({"web_list_failure": 75}, {"web_inspect_output": {"peanut": "unknown"}}):
            with self.subTest(settings=settings):
                result = self.run_backup(**settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.snapshots(), [])
                self.assertTrue(self.state["game_running"])
                self.assert_web_running()
                self.assertFalse(any(event["args"][:2] in (["compose", "stop"], ["compose", "start"])
                                     for event in self.events))

    def test_absent_and_stopped_web_services_are_not_started(self):
        for web_running in ({"pihole": True}, {service: False for service in WEB_SERVICES}, {}):
            with self.subTest(web_running=web_running):
                result = self.run_backup(configured=False, web_running=web_running)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(self.snapshots()), 1)
                self.assertEqual(self.state["web_running"], web_running)
                inspected = {event["args"][-1] for event in self.events if event["args"][:1] == ["inspect"]}
                self.assertEqual(inspected, set(web_running))

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

    def test_running_game_saved_before_web_pause_and_all_services_restart_before_prune(self):
        result = self.run_backup()
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
        result = self.run_backup(backup_failure=71)
        self.assertEqual(result.returncode, 71)
        self.assertEqual(len(self.snapshots()), 1)
        self.assertTrue(self.state["game_running"])
        self.assert_web_running()
        self.assertFalse(any(event["args"][:1] == ["forget"] for event in self.events))

    def test_web_restart_failure_does_not_prevent_game_restart(self):
        result = self.run_backup(web_restart_failures=["nginx-proxy-manager"])
        self.assertEqual(result.returncode, 72)
        self.assertTrue(self.state["game_running"])
        self.assertTrue(self.state["web_running"]["pihole"])
        self.assertTrue(self.state["web_running"]["peanut"])
        self.assertFalse(self.state["web_running"]["nginx-proxy-manager"])

    def test_interrupted_backup_restores_both_stacks(self):
        result = self.run_backup(terminate_backup=True)
        self.assertEqual(result.returncode, 143)
        self.assertTrue(self.state["game_running"])
        self.assert_web_running()
        self.assertFalse(any(event["args"][:1] == ["forget"] for event in self.events))

    def test_game_restart_failure_is_reported_after_web_services_recover(self):
        result = self.run_backup(game_restart_failure=73)
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

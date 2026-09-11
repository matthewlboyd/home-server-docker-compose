"""Offline ownership, port, credential, and output checks for game deployment."""

import copy
import importlib.machinery
import importlib.util
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-zomboid-host"
LOADER = importlib.machinery.SourceFileLoader("check_zomboid_host", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
helper = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(helper)
STACK = Path("/opt/homelab/zomboid")
LAN = "192.168.4.30"


def container():
    return {
        "Config": {"Labels": {
            "com.docker.compose.project": "homelab-zomboid",
            "com.docker.compose.service": "zomboid",
            "com.docker.compose.project.working_dir": str(STACK),
        }},
        "Mounts": [
            {"Type": "bind", "Source": str(STACK / "data/server-files"), "Destination": "/project-zomboid"},
            {"Type": "bind", "Source": str(STACK / "data/server-data"), "Destination": "/project-zomboid-config"},
        ],
        "HostConfig": {"PortBindings": {
            f"{port}/udp": [{"HostIp": LAN, "HostPort": str(port)}] for port in (16261, 16262)
        }},
        "State": {"Running": True},
    }


class ContainerTests(unittest.TestCase):
    def test_owned_existing_container_can_keep_its_world(self):
        self.assertTrue(helper.validate_container(container(), STACK, LAN))

    def test_other_projects_workdirs_and_mounts_are_rejected(self):
        for field in ("com.docker.compose.project", "com.docker.compose.service", "com.docker.compose.project.working_dir"):
            modified = container()
            modified["Config"]["Labels"][field] = "another-deployment"
            with self.subTest(field=field), self.assertRaises(helper.CheckError):
                helper.validate_container(modified, STACK, LAN)
        modified = container()
        modified["Mounts"][1]["Source"] = "/elsewhere/world"
        with self.assertRaises(helper.CheckError):
            helper.validate_container(modified, STACK, LAN)

    def test_public_bindings_and_published_rcon_are_rejected(self):
        modified = container()
        modified["HostConfig"]["PortBindings"]["16261/udp"][0]["HostIp"] = "0.0.0.0"
        with self.assertRaises(helper.CheckError):
            helper.validate_container(modified, STACK, LAN)
        modified = container()
        modified["HostConfig"]["PortBindings"]["27015/tcp"] = [{"HostIp": LAN, "HostPort": "27015"}]
        with self.assertRaises(helper.CheckError):
            helper.validate_container(modified, STACK, LAN)

    def test_other_docker_udp_bindings_conflict_even_without_a_host_socket(self):
        for address in (LAN, "0.0.0.0", "::", ""):
            with self.subTest(address=address):
                existing = {"NetworkSettings": {"Ports": {"9999/udp": [{"HostIp": address, "HostPort": "16261"}]}}}
                self.assertTrue(helper.conflicting_ports(existing, LAN))
        self.assertFalse(helper.conflicting_ports({"NetworkSettings": {"Ports": {"16261/tcp": [{"HostIp": LAN, "HostPort": "16261"}]}}}, LAN))

    def test_symlinked_stack_or_data_paths_are_rejected(self):
        for relative in (".", "data", "data/server-files", "data/server-data", "data/server-data/Server"):
            with tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary)
                stack = parent / "zomboid"
                target = parent / "outside"
                target.mkdir()
                link = stack if relative == "." else stack / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(target, target_is_directory=True)
                with self.subTest(relative=relative), self.assertRaises(helper.CheckError):
                    helper.validate_paths(stack)
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            (parent / "outside").mkdir()
            (parent / "homelab").symlink_to(parent / "outside", target_is_directory=True)
            with self.assertRaises(helper.CheckError):
                helper.validate_paths(parent / "homelab/zomboid")


class CredentialAndOutputTests(unittest.TestCase):
    def values(self):
        return {"LAN_IP": LAN, "ADMIN_PASSWORD": "a" * 48, "RCON_PASSWORD": "b" * 48, "SERVER_PASSWORD": "c" * 48}

    def test_existing_credentials_and_names_are_validated_without_replacement(self):
        values = self.values()
        before = copy.deepcopy(values)
        self.assertEqual(helper.validate_environment(values, LAN), "homelab")
        self.assertEqual(values, before)
        for key, value in (("SERVER_PASSWORD", ""), ("RCON_PASSWORD", "shell;syntax"), ("SERVER_NAME", "../../other"), ("SERVER_BRANCH", "unstable"), ("LAN_IP", "192.168.4.31")):
            modified = self.values()
            modified[key] = value
            with self.subTest(key=key), self.assertRaises(helper.CheckError):
                helper.validate_environment(modified, LAN)

    def test_environment_must_be_private_regular_and_unambiguous(self):
        path = mock.Mock()
        path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0)
        path.read_text.return_value = "LAN_IP=192.168.4.30\n# comment\nSERVER_BRANCH=\n"
        self.assertEqual(helper.environment_values(path)["SERVER_BRANCH"], "")
        path.read_text.return_value += "LAN_IP=another\n"
        with self.assertRaises(helper.CheckError):
            helper.environment_values(path)
        for mode in (stat.S_IFLNK | 0o600, stat.S_IFREG | 0o644):
            path.lstat.return_value.st_mode = mode
            with self.subTest(mode=mode), self.assertRaises(helper.CheckError):
                helper.environment_values(path)

    def test_existing_ini_must_keep_saved_password_and_private_access_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'homelab.ini'
            original = f'Password={"c" * 48}\nPublic=false\nUPnP=false\nOpen=true\n'
            path.write_text(original)
            helper.validate_ini(path, self.values())
            self.assertEqual(path.read_text(), original)
            for edited in (
                original.replace('Public=false', 'Public=true'),
                original.replace('UPnP=false', 'UPnP=true'),
                original.replace('c' * 48, 'd' * 48),
                original + 'Password=' + 'd' * 48 + '\n',
            ):
                path.write_text(edited)
                with self.assertRaises(helper.CheckError):
                    helper.validate_ini(path, self.values())

    def test_game_version_reads_both_streams_without_returning_log_credentials(self):
        secret = "test-secret-that-must-stay-private"
        result = SimpleNamespace(returncode=0, stdout=f"-adminpassword {secret}\n", stderr="LOG version=41.78.16 demo=false\n")
        with mock.patch.object(helper.subprocess, "run", return_value=result):
            self.assertEqual(helper.game_version(), "41.78.16")
        result.returncode = 1
        with mock.patch.object(helper.subprocess, "run", return_value=result), self.assertRaises(helper.CheckError) as failure:
            helper.game_version()
        self.assertNotIn(secret, str(failure.exception))

    def test_game_version_is_informational_when_startup_line_has_expired(self):
        for logs in ("", "player joined\n-adminpassword private-test-password\n"):
            result = SimpleNamespace(returncode=0, stdout=logs, stderr="")
            with self.subTest(logs=bool(logs)), mock.patch.object(helper.subprocess, "run", return_value=result):
                self.assertEqual(helper.game_version(), "unknown (startup log expired)")

    def test_game_version_still_fails_when_logs_cannot_be_read(self):
        for error in (
            OSError("private-test-password"),
            helper.subprocess.TimeoutExpired("docker", 60, output="private-test-password"),
        ):
            with self.subTest(error=type(error).__name__), mock.patch.object(helper.subprocess, "run", side_effect=error):
                with self.assertRaises(helper.CheckError) as failure:
                    helper.game_version()
                self.assertNotIn("private-test-password", str(failure.exception))


if __name__ == "__main__":
    unittest.main()

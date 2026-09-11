"""Offline checks for the exact-address Docker startup gate and retry deadline."""

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/wait-for-homelab-address"
LOADER = importlib.machinery.SourceFileLoader("wait_for_homelab_address", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
helper = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(helper)
LAN = "192.168.50.10"


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class AddressTests(unittest.TestCase):
    def test_requires_a_literal_nonlocal_ipv4_address(self):
        self.assertEqual(helper.ipv4_address(LAN), LAN)
        for value in ("localhost", "192.168.50.10/24", "192.168.50.999", "::1", "0.0.0.0", "127.0.0.1", "224.0.0.1"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                helper.ipv4_address(value)
        for value in ("0", "-1", "nan", "inf", "seconds"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                helper.positive_seconds(value)

    def test_exact_address_must_be_assigned_even_when_other_networks_are_ready(self):
        interfaces = [
            {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1"}]},
            {"ifname": "tailscale0", "addr_info": [{"family": "inet", "local": "100.64.0.1"}]},
            {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.50.100"}]},
        ]
        result = subprocess.CompletedProcess([], 0, json.dumps(interfaces), "")
        with mock.patch.object(helper.subprocess, "run", return_value=result) as command:
            self.assertFalse(helper.address_present(LAN, "/usr/sbin/ip", 3))
            interfaces[2]["addr_info"].append({"family": "inet", "local": LAN})
            result.stdout = json.dumps(interfaces)
            self.assertTrue(helper.address_present(LAN, "/usr/sbin/ip", 3))
            command.assert_called_with(
                ["/usr/sbin/ip", "-j", "-4", "address", "show"],
                capture_output=True, text=True, check=False, timeout=3,
            )

    def test_tentative_or_failed_address_does_not_open_the_gate(self):
        for flag in ("tentative", "dadfailed"):
            result = subprocess.CompletedProcess([], 0, json.dumps([
                {"addr_info": [{"family": "inet", "local": LAN, flag: True}]}
            ]), "")
            with self.subTest(flag=flag), mock.patch.object(helper.subprocess, "run", return_value=result):
                self.assertFalse(helper.address_present(LAN, "/usr/sbin/ip", 2))

    def test_ip_is_resolved_only_from_fixed_system_paths(self):
        with mock.patch.object(helper.os.path, "isfile", side_effect=lambda path: path == "/usr/bin/ip"), \
                mock.patch.object(helper.os, "access", return_value=True):
            self.assertEqual(helper.find_ip_command(), "/usr/bin/ip")
        with mock.patch.object(helper.os.path, "isfile", return_value=False), self.assertRaisesRegex(RuntimeError, "iproute2"):
            helper.find_ip_command()


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def wait(self, probe, timeout=60):
        return helper.wait_for_address(
            LAN, timeout, command="/usr/sbin/ip", clock=self.clock.monotonic,
            sleep=self.clock.sleep, probe=probe,
        )

    def test_present_address_starts_immediately_and_late_address_eventually_starts(self):
        self.assertTrue(self.wait(lambda *args: True))
        self.assertEqual(self.clock.now, 0)
        self.assertTrue(self.wait(lambda *args: self.clock.now >= 47))
        self.assertEqual(self.clock.now, 47)

    def test_absent_address_waits_the_full_default_minute_then_fails(self):
        probe = mock.Mock(return_value=False)
        self.assertFalse(self.wait(probe))
        self.assertEqual(self.clock.now, 60)
        self.assertEqual(probe.call_count, 60)
        self.assertEqual(probe.call_args.args[-1], 1)

    def test_transient_ip_errors_retry_until_the_same_deadline(self):
        for error in (OSError("temporary failure"), ValueError("bad JSON"),
                      TypeError("bad structure"), subprocess.TimeoutExpired("ip", 5)):
            self.clock = Clock()
            with self.subTest(error=type(error).__name__):
                self.assertFalse(self.wait(mock.Mock(side_effect=error)))
                self.assertEqual(self.clock.now, 60)
        self.clock = Clock()
        probe = mock.Mock(side_effect=[OSError("temporary"), ValueError("JSON"), True])
        self.assertTrue(self.wait(probe))
        self.assertEqual(self.clock.now, 2)

    def test_each_stalled_ip_process_is_bounded_by_the_remaining_deadline(self):
        timeouts = []

        def stalled_probe(address, command, timeout):
            timeouts.append(timeout)
            self.clock.sleep(timeout)
            raise subprocess.TimeoutExpired(command, timeout)

        self.assertFalse(self.wait(stalled_probe, timeout=8))
        self.assertEqual(timeouts, [5, 2])
        self.assertEqual(self.clock.now, 8)

    def test_a_success_after_the_deadline_cannot_open_the_gate(self):
        def late_probe(*args):
            self.clock.sleep(61)
            return True

        self.assertFalse(self.wait(late_probe))

    def test_cli_default_timeout_returns_nonzero_for_absent_address(self):
        output = io.StringIO()
        with mock.patch.object(helper.sys, "argv", [str(SCRIPT), LAN]), \
                mock.patch.object(helper, "wait_for_address", return_value=False) as wait, \
                contextlib.redirect_stderr(output):
            self.assertEqual(helper.main(), 1)
            wait.assert_called_once_with(LAN, 60)
        self.assertIn("Docker startup deferred", output.getvalue())


if __name__ == "__main__":
    unittest.main()

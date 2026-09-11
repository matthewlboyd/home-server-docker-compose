"""Offline web preflight checks, including Docker mappings without host sockets."""

import contextlib
import importlib.machinery
import importlib.util
import io
import ipaddress
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-web-network"
LOADER = importlib.machinery.SourceFileLoader("check_web_network", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
helper = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(helper)
LAN = "192.168.4.30"


def container(bindings, own=False):
    return {
        "Name": "/nginx-proxy-manager" if own else "/another-service",
        "Config": {"Labels": {
            "com.docker.compose.project": "home-dns" if own else "another-project",
            "com.docker.compose.service": "nginx-proxy-manager" if own else "another-service",
        }},
        "NetworkSettings": {"Ports": bindings},
    }


class PortTests(unittest.TestCase):
    def check(self, containers):
        with mock.patch.object(helper, "run", return_value="container-id\n" if containers else ""), \
                mock.patch.object(helper, "read_json", return_value=containers), \
                mock.patch.object(helper.socket, "socket") as socket:
            helper.check_ports(LAN)
        return socket

    def test_another_docker_mapping_is_rejected_even_when_native_socket_bind_would_succeed(self):
        for address in (LAN, "0.0.0.0", "::", ""):
            for port in (80, 81, 443):
                bindings = {"3000/tcp": [{"HostIp": address, "HostPort": str(port)}]}
                with self.subTest(address=address, port=port), self.assertRaisesRegex(helper.CheckError, "Docker mapping"):
                    self.check([container(bindings)])

    def test_owned_npm_mappings_skip_native_socket_probes(self):
        bindings = {f"{port}/tcp": [{"HostIp": LAN, "HostPort": str(port)}] for port in (80, 81, 443)}
        self.check([container(bindings, own=True)]).assert_not_called()

    def test_all_running_container_ids_are_inspected(self):
        with mock.patch.object(helper, "run", return_value="first-id\nsecond-id\n") as run, \
                mock.patch.object(helper, "read_json", return_value=[]) as read_json, \
                mock.patch.object(helper.socket, "socket"):
            helper.check_ports(LAN)
        run.assert_called_once_with("docker", "ps", "--quiet")
        read_json.assert_called_once_with("docker", "inspect", "--type", "container", "first-id", "second-id")

    def test_container_name_alone_does_not_establish_npm_ownership(self):
        other = container({"80/tcp": [{"HostIp": LAN, "HostPort": "80"}]})
        other["Name"] = "/nginx-proxy-manager"
        with self.assertRaises(helper.CheckError):
            self.check([other])

    def test_unrelated_addresses_and_udp_mappings_do_not_conflict(self):
        bindings = {
            "80/tcp": [{"HostIp": "192.168.4.31", "HostPort": "80"}],
            "443/udp": [{"HostIp": LAN, "HostPort": "443"}],
            "8080/tcp": [{"HostIp": LAN, "HostPort": "8080"}],
        }
        socket = self.check([container(bindings)])
        self.assertEqual(socket.return_value.__enter__.return_value.bind.call_args_list,
                         [mock.call((LAN, port)) for port in (80, 81, 443)])

    def test_native_host_service_conflicts_are_still_rejected(self):
        with mock.patch.object(helper, "run", return_value=""), \
                mock.patch.object(helper.socket, "socket") as socket:
            socket.return_value.__enter__.return_value.bind.side_effect = OSError(98, "Address already in use")
            with self.assertRaisesRegex(helper.CheckError, "cannot bind"):
                helper.check_ports(LAN)


class NetworkTests(unittest.TestCase):
    def check(self, subnet="172.29.20.0/29", gateway="172.29.20.1", routes=(), networks=()):
        with mock.patch.object(helper, "run", return_value="network-id\n" if networks else ""), \
                mock.patch.object(helper, "read_json", side_effect=[list(routes), list(networks)]):
            helper.check_networks(ipaddress.ip_network(subnet), ipaddress.ip_address(gateway))

    def test_gateway_check_does_not_enumerate_a_large_subnet(self):
        with mock.patch.object(ipaddress.IPv4Network, "hosts", side_effect=AssertionError("must not enumerate")):
            self.check("10.0.0.0/8", "10.255.255.254")
            self.check("172.29.20.0/31", "172.29.20.0")
            self.check("172.29.20.1/32", "172.29.20.1")
        for gateway in ("172.29.20.0", "172.29.20.7", "172.29.21.1", "::1"):
            with self.subTest(gateway=gateway), self.assertRaises(helper.CheckError):
                self.check(gateway=gateway)

    def test_existing_route_and_docker_network_overlaps_are_rejected(self):
        with self.assertRaisesRegex(helper.CheckError, "overlaps route"):
            self.check(routes=[{"dst": "172.29.20.0/24", "dev": "vpn0"}])
        network = {"Name": "other", "IPAM": {"Config": [{"Subnet": "172.29.20.0/24"}]}}
        with self.assertRaisesRegex(helper.CheckError, "overlaps Docker"):
            self.check(networks=[network])

    def test_existing_peanut_bridge_requires_matching_ownership_and_addresses(self):
        network = {
            "Name": "home-dns_peanut_link", "Options": {"com.docker.network.bridge.name": "br-peanut"},
            "Labels": {"com.docker.compose.project": "home-dns", "com.docker.compose.network": "peanut_link"},
            "IPAM": {"Config": [{"Subnet": "172.29.20.0/29", "Gateway": "172.29.20.1"}]},
        }
        self.check(networks=[network])
        network["IPAM"]["Config"][0]["Gateway"] = "172.29.20.2"
        with self.assertRaisesRegex(helper.CheckError, "different addresses"):
            self.check(networks=[network])
        network["Labels"]["com.docker.compose.project"] = "other"
        with self.assertRaisesRegex(helper.CheckError, "owned by a different"):
            self.check(networks=[network])

    def test_command_failure_is_bounded_and_does_not_print_private_output(self):
        secret = "private-container-test-value"
        error = subprocess.CalledProcessError(1, ["docker", "inspect"], output=secret, stderr=secret)
        with mock.patch.object(helper.subprocess, "run", side_effect=error) as run:
            with self.assertRaises(helper.CheckError) as failure:
                helper.run("docker", "inspect")
            self.assertNotIn(secret, str(failure.exception))
            self.assertEqual(run.call_args.kwargs["timeout"], 60)
        output = io.StringIO()
        with mock.patch.object(helper.sys, "argv", [str(SCRIPT), "172.29.20.0/29", "172.29.20.1"]), \
                mock.patch.object(helper, "check_networks", side_effect=json.JSONDecodeError(secret, secret, 0)), \
                contextlib.redirect_stderr(output):
            self.assertEqual(helper.main(), 1)
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())


if __name__ == "__main__":
    unittest.main()

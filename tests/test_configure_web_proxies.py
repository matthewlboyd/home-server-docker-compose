"""Offline checks for adding Cockpit to the shared homelab HTTPS certificate."""

import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import itertools
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/configure-web-proxies"
LOADER = importlib.machinery.SourceFileLoader("configure_web_proxies", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
helper = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(helper)

DOMAINS = {
    "npm": "npm.example.test",
    "peanut": "peanut.example.test",
    "pihole": "pihole.example.test",
    "cockpit": "cockpit.example.test",
}
BLOCK = "location = /api/ws { return 403; }\nlocation ^~ /api/ws/ { return 403; }"
TOKEN = "offline_test_token_never_used_for_network"


def certificate(cert_id=15, names=None):
    return {
        "id": cert_id, "provider": "letsencrypt",
        "domain_names": list(DOMAINS.values()) if names is None else names,
        "expires_on": "2099-01-01 00:00:00",
        "meta": {"dns_challenge": True, "dns_provider": "cloudflare"},
    }


class FakeApi:
    def __init__(self, hosts=(), certificates=(), bad_certificate_read=False):
        self.hosts = {host["id"]: copy.deepcopy(host) for host in hosts}
        self.certificates = {cert["id"]: copy.deepcopy(cert) for cert in certificates}
        self.calls = []
        self.bad_certificate_read = bad_certificate_read

    def request(self, method, path, body=None, timeout=30):
        self.calls.append((method, path, copy.deepcopy(body)))
        if path == "/nginx/certificates":
            if method == "GET":
                return copy.deepcopy(list(self.certificates.values()))
            if method == "POST":
                new = certificate(50, copy.deepcopy(body["domain_names"]))
                self.certificates[new["id"]] = new
                return copy.deepcopy(new)
        if path.startswith("/nginx/certificates/") and method == "GET":
            cert = copy.deepcopy(self.certificates[int(path.rsplit("/", 1)[1])])
            if self.bad_certificate_read:
                cert["domain_names"] = ["wrong.example.org"]
            return cert
        if path == "/nginx/proxy-hosts":
            if method == "GET":
                return copy.deepcopy(list(self.hosts.values()))
            if method == "POST":
                host_id = max(self.hosts, default=0) + 1
                new = copy.deepcopy(body)
                new.update(id=host_id, meta={"nginx_online": True})
                self.hosts[host_id] = new
                return copy.deepcopy(new)
        if path.startswith("/nginx/proxy-hosts/"):
            host = self.hosts[int(path.rsplit("/", 1)[1])]
            if method == "PUT":
                host.update(copy.deepcopy(body))
            if method in {"GET", "PUT"}:
                return copy.deepcopy(host)
        raise AssertionError(f"Unexpected fake request: {method} {path}")

    def mutations(self, resource):
        return [call for call in self.calls if call[0] != "GET" and resource in call[1]]


class StackTests(unittest.TestCase):
    def read_stack(self, aliases=None, zone="example.test", gateway="172.29.20.1", block=BLOCK):
        config = {
            "services": {
                "nginx-proxy-manager": {
                    "environment": {"HOMELAB_DOMAIN": zone},
                    "ports": [{"target": 81, "published": "81", "host_ip": "192.168.4.30"}],
                },
                "pihole": {"environment": {"FTLCONF_dns_hostRecord": ",".join(
                    list(DOMAINS.values()) + ["192.168.4.30"] if aliases is None else aliases
                )}},
                "peanut": {"environment": {"WEB_HOST": gateway, "WEB_PORT": "8081"}},
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "nginx").mkdir()
            (directory / "nginx/peanut-advanced.conf").write_text(block)
            response = types.SimpleNamespace(stdout=json.dumps(config))
            with mock.patch.object(helper.subprocess, "run", return_value=response):
                return helper.read_stack(directory)

    def test_role_mapping_ignores_record_order_and_preserved_aliases(self):
        for order in itertools.permutations(DOMAINS.values()):
            with self.subTest(order=order):
                aliases = ["bombadil", "pihole.home.arpa", *order, "192.168.4.30"]
                url, roles, gateway, port, _ = self.read_stack(aliases)
                self.assertEqual(roles, DOMAINS)
                self.assertEqual(url, "http://192.168.4.30:81/api")
                self.assertEqual((gateway, port), ("172.29.20.1", 8081))

    def test_missing_npm_record_is_rejected(self):
        with self.assertRaises(helper.SetupError):
            self.read_stack([DOMAINS["pihole"], DOMAINS["peanut"], DOMAINS["cockpit"], "192.168.4.30"])

    def test_missing_cockpit_record_is_rejected(self):
        with self.assertRaises(helper.SetupError):
            self.read_stack([DOMAINS["pihole"], DOMAINS["peanut"], DOMAINS["npm"], "192.168.4.30"])

    def test_host_services_must_use_a_private_non_loopback_ipv4_gateway(self):
        for gateway in ("8.8.8.8", "127.0.0.1", "0.0.0.0", "::1"):
            with self.subTest(gateway=gateway), self.assertRaises(helper.SetupError):
                self.read_stack(gateway=gateway)

    def test_game_dns_alias_does_not_add_a_web_proxy_or_certificate_role(self):
        aliases = [*DOMAINS.values(), "zomboid.example.test", "192.168.4.30"]
        _, roles, _, _, _ = self.read_stack(aliases)
        self.assertEqual(roles, DOMAINS)

    def test_domain_and_lan_address_must_match_the_dns_record(self):
        with self.assertRaises(helper.SetupError):
            self.read_stack(zone="another.example.org")
        with self.assertRaises(helper.SetupError):
            self.read_stack([*DOMAINS.values(), "192.168.4.99"])

    def test_terminal_block_requires_both_active_denies_and_no_other_directives(self):
        self.read_stack(block="# terminal policy\n" + BLOCK)
        for block in (
            "\n".join("# " + line for line in BLOCK.splitlines()),
            BLOCK.splitlines()[0],
            BLOCK + "\nlocation = /api/ws/terminal { return 200; }",
            BLOCK + "\n" + BLOCK,
        ):
            with self.subTest(block=block), self.assertRaises(helper.SetupError):
                self.read_stack(block=block)


    def test_root_workaround_accepts_only_one_relative_device_redirect(self):
        for device in ("cyberpower", "ups%20name", "host~3493~ups"):
            self.read_stack(block=BLOCK + "\nlocation = / { return 302 /device/" + device + "; }")
        for redirect in (
            "return 302 https://elsewhere.example/;",
            "return 302 //elsewhere.example/;",
            "return 302 /device/;",
            "return 302 /device/ups?next=elsewhere;",
            "return 302 /device/ups; proxy_pass http://elsewhere;",
        ):
            with self.subTest(redirect=redirect), self.assertRaises(helper.SetupError):
                self.read_stack(block=BLOCK + "\nlocation = / { " + redirect + " }")
        root = "location = / { return 302 /device/cyberpower; }"
        with self.assertRaises(helper.SetupError):
            self.read_stack(block=BLOCK + "\n" + root + "\n" + root)


class ConfigureTests(unittest.TestCase):
    def run_configure(self, api, token=TOKEN):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            helper.configure(api, DOMAINS, "172.29.20.1", 8081, BLOCK, token)
        self.assertNotIn(TOKEN, output.getvalue())
        return output.getvalue()

    def existing_hosts(self):
        return [
            {"id": 1, "domain_names": [DOMAINS["pihole"]], "certificate_id": 9,
             "ssl_forced": True, "http2_support": True, "hsts_enabled": True,
             "hsts_subdomains": False, "meta": {"nginx_online": True}},
            {"id": 2, "domain_names": [DOMAINS["peanut"]], "certificate_id": 9,
             "ssl_forced": True, "http2_support": False, "hsts_enabled": False,
             "hsts_subdomains": False, "advanced_config": "# keep my setting\n" + BLOCK,
             "meta": {"nginx_online": True}},
            {"id": 3, "domain_names": [DOMAINS["npm"]], "certificate_id": 9,
             "ssl_forced": True, "http2_support": True, "hsts_enabled": False,
             "hsts_subdomains": False, "advanced_config": "proxy_read_timeout 90s;",
             "access_list_id": 7, "meta": {"nginx_online": True}},
        ]

    def test_existing_three_host_certificate_migrates_to_one_four_host_certificate(self):
        old_cert = certificate(9, [DOMAINS["pihole"], DOMAINS["peanut"], DOMAINS["npm"]])
        api = FakeApi(self.existing_hosts(), [old_cert])
        output = self.run_configure(api)
        requests = api.mutations("/nginx/certificates")
        self.assertEqual(len(requests), 1)
        self.assertEqual(set(requests[0][2]["domain_names"]), set(DOMAINS.values()))
        self.assertEqual(requests[0][2]["meta"]["dns_provider"], "cloudflare")
        self.assertNotIn("zomboid.example.test", requests[0][2]["domain_names"])
        expected = {
            DOMAINS["npm"]: ("http", "127.0.0.1", 81),
            DOMAINS["pihole"]: ("http", "pihole", 80),
            DOMAINS["peanut"]: ("http", "172.29.20.1", 8081),
            DOMAINS["cockpit"]: ("https", "172.29.20.1", 9090),
        }
        for host in api.hosts.values():
            name = host["domain_names"][0]
            self.assertEqual((host["forward_scheme"], host["forward_host"], host["forward_port"]), expected[name])
            self.assertEqual(host["allow_websocket_upgrade"], name == DOMAINS["cockpit"])
            self.assertEqual(host["certificate_id"], 50)
            self.assertTrue(host["ssl_forced"])
            self.assertIn("https://" + name, output)
        self.assertTrue(api.hosts[1]["hsts_enabled"])
        self.assertIn("# keep my setting", api.hosts[2]["advanced_config"])
        self.assertIn(BLOCK, api.hosts[2]["advanced_config"])
        self.assertEqual(api.hosts[3]["advanced_config"], "proxy_read_timeout 90s;")
        self.assertEqual(api.hosts[3]["access_list_id"], 7)
        cockpit = next(host for host in api.hosts.values() if host["domain_names"] == [DOMAINS["cockpit"]])
        self.assertEqual(cockpit["access_list_id"], 0)
        self.assertFalse(cockpit["caching_enabled"])
        self.assertIn(helper.COCKPIT_BLOCK, cockpit["advanced_config"])
        certificate_read = api.calls.index(("GET", "/nginx/certificates/50", None))
        first_proxy_write = min(api.calls.index(call) for call in api.mutations("/nginx/proxy-hosts"))
        self.assertLess(certificate_read, first_proxy_write)
        api.calls.clear()
        self.run_configure(api)
        self.assertEqual(api.mutations("/nginx/"), [])

    def test_matching_valid_certificate_is_reused_and_second_run_is_idempotent(self):
        api = FakeApi(certificates=[certificate()])
        self.run_configure(api)
        self.assertEqual(api.mutations("/nginx/certificates"), [])
        self.assertEqual({host["certificate_id"] for host in api.hosts.values()}, {15})
        api.calls.clear()
        self.run_configure(api)
        self.assertEqual(api.mutations("/nginx/"), [])

    def test_no_token_preserves_existing_tls_and_the_terminal_block(self):
        original = self.existing_hosts()
        api = FakeApi(original)
        self.run_configure(api, token=None)
        for old in original:
            for field in ("certificate_id", "ssl_forced", "http2_support", "hsts_enabled", "hsts_subdomains"):
                self.assertEqual(api.hosts[old["id"]][field], old[field])
        self.assertIn(BLOCK, api.hosts[2]["advanced_config"])
        self.assertEqual(api.mutations("/nginx/certificates"), [])

    def test_unconfirmed_certificate_cannot_change_any_proxy(self):
        api = FakeApi(self.existing_hosts(), bad_certificate_read=True)
        with self.assertRaisesRegex(helper.SetupError, "did not retain"):
            self.run_configure(api)
        self.assertEqual(api.mutations("/nginx/proxy-hosts"), [])

    def test_expired_or_http_challenge_certificates_are_not_reused(self):
        expired = certificate(12)
        expired["expires_on"] = "2000-01-01 00:00:00"
        http = certificate(13)
        http["meta"] = {"dns_challenge": False}
        api = FakeApi(certificates=[expired, http])
        self.run_configure(api)
        self.assertEqual(len(api.mutations("/nginx/certificates")), 1)

    def test_multidomain_overlap_stops_before_certificate_issuance(self):
        hosts = self.existing_hosts()
        hosts[0]["domain_names"].append("unrelated.example.org")
        api = FakeApi(hosts)
        with self.assertRaisesRegex(helper.SetupError, "overlap"):
            self.run_configure(api)
        self.assertEqual(api.mutations("/nginx/"), [])

    def test_terminal_location_conflicts_are_checked_for_existing_managed_and_legacy_blocks(self):
        managed = helper.BLOCK_START + "\n" + BLOCK + "\n" + helper.BLOCK_END
        conflict = "location = /api/ws/terminal { proxy_pass http://172.29.20.1:8081; }"
        for block in (BLOCK, managed):
            for current in (conflict + "\n" + block, block + "\n" + conflict):
                hosts = self.existing_hosts()
                hosts[1]["advanced_config"] = current
                api = FakeApi(hosts)
                with self.subTest(current=current), self.assertRaisesRegex(helper.SetupError, "terminal locations"):
                    self.run_configure(api)
                self.assertEqual(api.mutations("/nginx/"), [])

    def test_terminal_custom_settings_and_comment_examples_remain_unchanged(self):
        managed = helper.BLOCK_START + "\n" + BLOCK + "\n" + helper.BLOCK_END
        for block in (BLOCK, managed):
            current = "proxy_read_timeout 90s;\n# example: location = /api/ws/terminal\n" + block
            self.assertEqual(helper.peanut_config({"advanced_config": current}, BLOCK), current)

    def test_legacy_terminal_block_migrates_to_root_redirect_without_losing_custom_settings(self):
        old = "proxy_read_timeout 90s;\n" + helper.LEGACY_BLOCK_START + "\n" + BLOCK + "\n" + helper.LEGACY_BLOCK_END
        root = "location = / { return 302 /device/cyberpower; }"
        desired = root + "\n" + BLOCK
        result = helper.peanut_config({"advanced_config": old}, desired)
        self.assertIn("proxy_read_timeout 90s;", result)
        self.assertIn(root, result)
        self.assertIn(BLOCK, result)
        self.assertNotIn(helper.LEGACY_BLOCK_START, result)
        self.assertEqual(result.count("location = / {"), 1)
        self.assertEqual(helper.peanut_config({"advanced_config": result}, desired), result)

    def test_existing_root_location_is_not_silently_replaced(self):
        managed = helper.BLOCK_START + "\n" + BLOCK + "\n" + helper.BLOCK_END
        for current in ("location = / { return 200; }\n" + managed,
                        managed + "\nlocation = / { return 200; }"):
            hosts = self.existing_hosts()
            hosts[1]["advanced_config"] = current
            api = FakeApi(hosts)
            with self.assertRaisesRegex(helper.SetupError, "root location"):
                self.run_configure(api)
            self.assertEqual(api.mutations("/nginx/"), [])

    def test_malformed_terminal_markers_stop_before_any_mutation(self):
        for current in (
            helper.BLOCK_START,
            helper.BLOCK_END + "\n" + helper.BLOCK_START,
            helper.BLOCK_START + "\n" + helper.BLOCK_START + "\n" + helper.BLOCK_END,
        ):
            hosts = self.existing_hosts()
            hosts[1]["advanced_config"] = current
            api = FakeApi(hosts)
            with self.subTest(current=current), self.assertRaisesRegex(helper.SetupError, "markers"):
                self.run_configure(api)
            self.assertEqual(api.mutations("/nginx/"), [])

    def test_cockpit_preserves_unrelated_settings_and_refreshes_its_managed_block(self):
        existing = {
            "id": 4, "domain_names": [DOMAINS["cockpit"]], "meta": {"nginx_online": True},
            "advanced_config": "proxy_read_timeout 3600s;\n" + helper.COCKPIT_BLOCK_START
            + "\n# old managed directives\n" + helper.COCKPIT_BLOCK_END + "\n# retained comment",
            "hsts_enabled": True, "hsts_subdomains": False,
        }
        api = FakeApi([*self.existing_hosts(), existing])
        self.run_configure(api)
        current = api.hosts[4]
        self.assertTrue(current["hsts_enabled"])
        self.assertIn("proxy_read_timeout 3600s;", current["advanced_config"])
        self.assertIn("# retained comment", current["advanced_config"])
        self.assertNotIn("# old managed directives", current["advanced_config"])
        self.assertEqual(current["advanced_config"].count(helper.COCKPIT_BLOCK), 1)
        self.assertIn("set $x_forwarded_proto $scheme;", current["advanced_config"])
        self.assertIn("set $x_forwarded_scheme $scheme;", current["advanced_config"])

    def test_cockpit_conflicting_headers_or_locations_stop_before_any_mutation(self):
        for configuration in (
            {"advanced_config": "set $x_forwarded_proto $http_x_forwarded_proto;"},
            {"advanced_config": "set $x_forwarded_scheme $http_x_forwarded_scheme;"},
            {"advanced_config": "proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;"},
            {"advanced_config": "gzip on;"},
            {"advanced_config": "proxy_buffering on;"},
            {"advanced_config": "location / { proxy_pass http://elsewhere; }"},
            {"locations": [{"path": "/"}]},
            {"advanced_config": helper.COCKPIT_BLOCK_START},
        ):
            existing = {"id": 4, "domain_names": [DOMAINS["cockpit"]], **configuration}
            api = FakeApi([*self.existing_hosts(), existing])
            with self.subTest(configuration=configuration), self.assertRaises(helper.SetupError):
                self.run_configure(api)
            self.assertEqual(api.mutations("/nginx/"), [])

    def test_existing_cockpit_access_list_is_preserved_by_refusing_migration(self):
        existing = {
            "id": 4, "domain_names": [DOMAINS["cockpit"]], "access_list_id": 12,
            "certificate_id": 9, "ssl_forced": True,
        }
        api = FakeApi([*self.existing_hosts(), existing])
        with self.assertRaisesRegex(helper.SetupError, "access list"):
            self.run_configure(api)
        self.assertEqual(api.hosts[4], existing)
        self.assertEqual(api.mutations("/nginx/"), [])


if __name__ == "__main__":
    unittest.main()

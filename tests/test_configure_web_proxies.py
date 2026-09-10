"""Offline checks for migrating the two HTTP proxy hosts to three HTTPS hosts."""

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
    "npm": "npm.bigbiscuit.org",
    "peanut": "peanut.bigbiscuit.org",
    "pihole": "pihole.bigbiscuit.org",
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
    def read_stack(self, aliases=None, zone="bigbiscuit.org"):
        config = {
            "services": {
                "nginx-proxy-manager": {
                    "environment": {"HOMELAB_DOMAIN": zone},
                    "ports": [{"target": 81, "published": "81", "host_ip": "192.168.4.30"}],
                },
                "pihole": {"environment": {"FTLCONF_dns_hostRecord": ",".join(
                    list(DOMAINS.values()) + ["192.168.4.30"] if aliases is None else aliases
                )}},
                "peanut": {"environment": {"WEB_HOST": "172.29.20.1", "WEB_PORT": "8081"}},
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "nginx").mkdir()
            (directory / "nginx/peanut-advanced.conf").write_text(BLOCK)
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
            self.read_stack([DOMAINS["pihole"], DOMAINS["peanut"], "192.168.4.30"])

    def test_domain_and_lan_address_must_match_the_dns_record(self):
        with self.assertRaises(helper.SetupError):
            self.read_stack(zone="another.example.org")
        with self.assertRaises(helper.SetupError):
            self.read_stack([*DOMAINS.values(), "192.168.4.99"])


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
        ]

    def test_existing_two_host_certificate_migrates_to_one_three_host_certificate(self):
        old_cert = certificate(9, [DOMAINS["pihole"], DOMAINS["peanut"]])
        api = FakeApi(self.existing_hosts(), [old_cert])
        output = self.run_configure(api)
        requests = api.mutations("/nginx/certificates")
        self.assertEqual(len(requests), 1)
        self.assertEqual(set(requests[0][2]["domain_names"]), set(DOMAINS.values()))
        self.assertEqual(requests[0][2]["meta"]["dns_provider"], "cloudflare")
        expected = {
            DOMAINS["npm"]: ("127.0.0.1", 81),
            DOMAINS["pihole"]: ("pihole", 80),
            DOMAINS["peanut"]: ("172.29.20.1", 8081),
        }
        for host in api.hosts.values():
            name = host["domain_names"][0]
            self.assertEqual((host["forward_host"], host["forward_port"]), expected[name])
            self.assertEqual(host["certificate_id"], 50)
            self.assertTrue(host["ssl_forced"])
            self.assertIn("https://" + name, output)
        self.assertTrue(api.hosts[1]["hsts_enabled"])
        self.assertIn("# keep my setting", api.hosts[2]["advanced_config"])
        self.assertIn(BLOCK, api.hosts[2]["advanced_config"])
        certificate_read = api.calls.index(("GET", "/nginx/certificates/50", None))
        first_proxy_write = min(api.calls.index(call) for call in api.mutations("/nginx/proxy-hosts"))
        self.assertLess(certificate_read, first_proxy_write)

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


if __name__ == "__main__":
    unittest.main()

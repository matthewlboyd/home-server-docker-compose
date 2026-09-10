"""Offline checks for preserving Cockpit settings while adding proxy origins."""

import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/render-cockpit-proxy-config"
LOADER = importlib.machinery.SourceFileLoader("render_cockpit_proxy_config", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
helper = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(helper)


class CockpitConfigTests(unittest.TestCase):
    def setUp(self):
        self.origins = helper.required_origins("example.org", "100.81.175.110")

    def test_new_configuration_includes_proxy_and_direct_tailscale_origins(self):
        result = helper.render_configuration("", self.origins)
        self.assertTrue(result["changed"])
        self.assertEqual(result["content"],
                         "[WebService]\nOrigins = https://cockpit.example.org wss://cockpit.example.org "
                         "https://100.81.175.110:9090 wss://100.81.175.110:9090\n"
                         "ProtocolHeader = X-Forwarded-Proto\n")

    def test_ipv6_origins_escape_literal_brackets_for_cockpit_matching(self):
        origins = helper.required_origins("example.org", "100.81.175.110", "fd7a:115c:a1e0::1")
        self.assertIn(r"https://\[fd7a:115c:a1e0::1\]:9090", origins)
        self.assertIn(r"wss://\[fd7a:115c:a1e0::1\]:9090", origins)
        original = "[WebService]\nOrigins = " + " ".join(origins[-2:]) + "\n"
        result = helper.render_configuration(original, origins)
        self.assertEqual(result["origins"][:2], origins[-2:])

    def test_preserves_existing_origins_and_other_authentication_settings(self):
        original = (
            "# Local configuration\n[WebService]\n"
            "LoginTo=false\nAllowUnencrypted=false\n"
            "Origins = https://old.example.org wss://old.example.org\n"
            "[Basic]\nAction=disabled\n[Session]\nIdleTimeout=15\n"
        )
        result = helper.render_configuration(original, self.origins)
        self.assertTrue(result["changed"])
        self.assertEqual(result["origins"][:2], ["https://old.example.org", "wss://old.example.org"])
        self.assertIn("LoginTo=false\nAllowUnencrypted=false\n", result["content"])
        self.assertIn("[Basic]\nAction=disabled\n[Session]\nIdleTimeout=15\n", result["content"])
        self.assertLess(result["content"].index("ProtocolHeader"), result["content"].index("[Basic]"))

    def test_second_run_preserves_configuration_byte_for_byte(self):
        first = helper.render_configuration("[Session]\nIdleTimeout=15", self.origins)
        second = helper.render_configuration(first["content"], self.origins)
        self.assertFalse(second["changed"])
        self.assertEqual(second["content"], first["content"])
        self.assertTrue(first["content"].startswith("[Session]\nIdleTimeout=15\n\n"))

    def test_existing_correct_settings_are_not_reformatted(self):
        original = "[WebService]\nOrigins=" + "   ".join(self.origins) + "\nProtocolHeader=X-Forwarded-Proto"
        result = helper.render_configuration(original, self.origins)
        self.assertFalse(result["changed"])
        self.assertEqual(result["content"], original)

    def test_preserves_crlf_and_missing_options_before_next_section(self):
        original = "[WebService]\r\nLoginTo=false\r\n[Session]\r\nIdleTimeout=15\r\n"
        result = helper.render_configuration(original, self.origins)
        self.assertIn("LoginTo=false\r\n", result["content"])
        self.assertIn("ProtocolHeader = X-Forwarded-Proto\r\n[Session]", result["content"])
        self.assertNotIn("\n", result["content"].replace("\r\n", ""))

    def test_rejects_ambiguous_configuration_without_disclosing_values(self):
        configurations = (
            "[WebService]\nOrigins=PRIVATE_VALUE\nOrigins=duplicate\n",
            "[WebService]\nOrigins=PRIVATE_VALUE\n[WebService]\n",
            "[DEFAULT]\nOrigins=PRIVATE_VALUE\n",
            "[webservice]\nOrigins=PRIVATE_VALUE\n",
            "[Session]\nBanner=PRIVATE_VALUE\n  [WebService]\n",
        )
        for contents in configurations:
            with self.subTest(contents=contents):
                with self.assertRaises(helper.ConfigError) as error:
                    helper.render_configuration(contents, self.origins)
                self.assertNotIn("PRIVATE_VALUE", str(error.exception))

    def test_refuses_conflicting_paths_and_authentication_instead_of_resetting_them(self):
        settings = (
            "[WebService]\nUrlRoot=/existing\n",
            "[WebService]\nCustomLoginPage=/custom/login\n",
            "[WebService]\nShell=/custom/shell.html\n",
            "[WebService]\nClientCertAuthentication=true\n",
            "[WebService]\nProtocolHeader=Different-Header\n",
            "[OAuth]\nURL=https://identity.example.org/authorize\n",
        )
        for contents in settings:
            with self.subTest(contents=contents):
                with self.assertRaises(helper.ConfigError):
                    helper.render_configuration(contents, self.origins)

    def test_rejects_wildcard_origins_without_disclosing_or_changing_configuration(self):
        message = "Existing wildcard Origins require review before configuring the proxy; cockpit.conf has not been changed."
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cockpit.conf"
            for origin in ("https://*.PRIVATE_VALUE.org", "wss://PRIVATE_VALUE?.example.org"):
                with self.subTest(origin=origin):
                    original = ("[WebService]\nOrigins = " + " ".join(self.origins + [origin])
                                + "\nProtocolHeader = X-Forwarded-Proto\n")
                    path.write_text(original)
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), "--config", str(path), "--domain", "example.org",
                         "--tailscale-ipv4", "100.81.175.110"],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, message + "\n")
                    self.assertNotIn("PRIVATE_VALUE", result.stderr)
                    self.assertEqual(path.read_text(), original)

    def test_file_reader_is_read_only_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cockpit.conf"
            self.assertEqual(helper.read_configuration(path), "")
            path.write_bytes(b"[Session]\r\nIdleTimeout=15\r\n")
            original = path.read_bytes()
            self.assertEqual(helper.read_configuration(path), original.decode())
            self.assertEqual(path.read_bytes(), original)
            link = Path(directory) / "link.conf"
            link.symlink_to(path)
            with self.assertRaises(helper.ConfigError):
                helper.read_configuration(link)

    def test_cli_error_does_not_print_private_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cockpit.conf"
            original = "[WebService]\nUrlRoot=/PRIVATE_VALUE\n"
            path.write_text(original)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--config", str(path), "--domain", "example.org",
                 "--tailscale-ipv4", "100.81.175.110"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("PRIVATE_VALUE", result.stderr)
            self.assertEqual(path.read_text(), original)


if __name__ == "__main__":
    unittest.main()

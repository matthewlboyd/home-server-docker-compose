"""Offline validation of the domain read from the private Compose environment."""

import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/read-homelab-domain"
LOADER = importlib.machinery.SourceFileLoader("read_homelab_domain", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
helper = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(helper)


class DomainTests(unittest.TestCase):
    def read(self, contents):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text(contents)
            return helper.read_domain(path)

    def test_literal_dotenv_forms_select_the_same_domain(self):
        for line in (
            "HOMELAB_DOMAIN=example.test",
            "HOMELAB_DOMAIN = Example.Test # service suffix",
            "export HOMELAB_DOMAIN='example.test' # service suffix",
            'HOMELAB_DOMAIN="example.test"',
        ):
            with self.subTest(line=line):
                self.assertEqual(self.read("# comment\n" + line + "\nOTHER=value\n"), "example.test")

    def test_domain_is_required_and_cannot_be_ambiguous(self):
        for contents in (
            "OTHER=value\n",
            "# HOMELAB_DOMAIN=example.test\n",
            "HOMELAB_DOMAIN=\n",
            "HOMELAB_DOMAIN\n",
            "HOMELAB_DOMAIN=example.test\nHOMELAB_DOMAIN=another.test\n",
            "HOMELAB_DOMAIN=example.test\nHOMELAB_DOMAIN\n",
        ):
            with self.subTest(contents=contents), self.assertRaises(helper.DomainError):
                self.read(contents)

    def test_only_valid_literal_dns_suffixes_are_accepted(self):
        for value in (
            "https://example.test", "example.test/path", "example.test:443",
            "localhost", "example..test", "-example.test", "example-.test",
            "example.test.", "example_test", "${OTHER_DOMAIN}", '"${OTHER_DOMAIN}"',
            "example.test#not-a-comment", "'example.test", "example.test extra",
            "a" * 64 + ".test", ".".join(["a" * 61] * 4),
        ):
            with self.subTest(value=value), self.assertRaises(helper.DomainError):
                self.read("HOMELAB_DOMAIN=" + value + "\n")

    def test_suffix_leaves_room_for_the_cockpit_hostname(self):
        domain = '.'.join(['a' * 61] * 3 + ['b' * 59])
        self.assertEqual(len(domain), 245)
        self.assertEqual(self.read('HOMELAB_DOMAIN=' + domain), domain)
        with self.assertRaises(helper.DomainError):
            self.read('HOMELAB_DOMAIN=' + domain + 'b')

    def test_other_environment_values_are_not_evaluated_or_disclosed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = directory / "unexpected-shell-execution"
            path = directory / ".env"
            secret = "private-test-value-never-print"
            path.write_text(f"OTHER=$(touch {marker})\nSECRET={secret}\nHOMELAB_DOMAIN=example.test\n")
            result = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "example.test\n")
            self.assertEqual(result.stderr, "")
            self.assertFalse(marker.exists())
            self.assertNotIn(secret, result.stdout + result.stderr)

            path.write_text(f"SECRET={secret}\nHOMELAB_DOMAIN=$({secret})\n")
            result = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_missing_file_has_an_actionable_error_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(Path(temporary) / ".env")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("create it before deployment", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()

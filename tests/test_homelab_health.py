"""Certificate tests must target the live proxy certificates, using ACME staging."""

import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

LOADER = importlib.machinery.SourceFileLoader("homelab_health", str(Path(__file__).resolve().parents[1] / "scripts/check-homelab-health"))
MODULE = importlib.util.module_from_spec(importlib.util.spec_from_loader(LOADER.name, LOADER))
LOADER.exec_module(MODULE)


class RenewalTests(unittest.TestCase):
    def test_only_active_certificates_are_tested_with_time_for_dns_caches(self):
        with patch.object(MODULE.subprocess, "run") as run:
            MODULE.renew_certificates({2})
        args = run.call_args.args[0]
        self.assertEqual(run.call_count, 1)
        self.assertEqual(args[args.index("--cert-name") + 1], "npm-2")
        self.assertIn("--dry-run", args)
        self.assertNotIn("--force-renewal", args)
        self.assertEqual(args[args.index("--dns-cloudflare-propagation-seconds") + 1], "120")

    def test_unexpected_certificate_identifier_is_rejected_before_execution(self):
        with patch.object(MODULE.subprocess, "run") as run, self.assertRaises(MODULE.HealthError):
            MODULE.renew_certificates({"../../other"})
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

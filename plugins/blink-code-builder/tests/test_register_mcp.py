"""Tests for the URL routing in hooks/register_mcp.py. Run: python3 -m unittest discover -s tests"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import register_mcp  # noqa: E402


class RouteTest(unittest.TestCase):
    def test_tenant_with_subdomain(self):
        target = register_mcp.route("https://acme.app.blinkops.com")
        self.assertEqual((target.environment.name, target.subdomain, target.matched), ("us1", "acme", True))

    def test_tenant_with_subdomain_on_other_environment(self):
        target = register_mcp.route("https://acme.eu1.blinkops.com/workspace/1")
        self.assertEqual((target.environment.name, target.subdomain, target.matched), ("eu1", "acme", True))

    def test_bare_domain_has_no_subdomain(self):
        target = register_mcp.route("https://app.blinkops.com")
        self.assertEqual((target.environment.name, target.subdomain, target.matched), ("us1", "", True))

    def test_bare_domain_of_other_environment_uses_its_own_server(self):
        for name in ("us2", "eu1"):
            target = register_mcp.route(f"https://{name}.blinkops.com")
            self.assertEqual((target.environment.name, target.subdomain, target.matched), (name, "", True))

    def test_unlisted_domain_falls_back_to_default(self):
        target = register_mcp.route("https://acme.example.com")
        self.assertEqual((target.environment.name, target.subdomain, target.matched), ("us1", "acme", False))


class ServerConfigTest(unittest.TestCase):
    def test_header_is_sent_with_a_subdomain(self):
        config = register_mcp.server_config(register_mcp.route("https://acme.app.blinkops.com"))
        self.assertEqual(config["headers"], {"Blink-Subdomain": "acme"})

    def test_no_header_for_bare_domain(self):
        config = register_mcp.server_config(register_mcp.route("https://app.blinkops.com"))
        self.assertNotIn("headers", config)

    def test_is_current_for_bare_domain(self):
        wanted = register_mcp.server_config(register_mcp.route("https://app.blinkops.com"))
        self.assertTrue(register_mcp.is_current(dict(wanted), wanted))
        self.assertTrue(register_mcp.is_current({**wanted, "headers": {}}, wanted))
        self.assertFalse(register_mcp.is_current({**wanted, "headers": {"Blink-Subdomain": "app"}}, wanted))


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import tempfile
import unittest

import requests

from session_store import CookieStore, SessionRegistry, import_cookie_file, read_cookie_file


class SessionStoreTests(unittest.TestCase):
    def test_json_filters_expired_wrong_domain_and_domainless(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "elsevier.json"
            path.write_text(json.dumps([
                {"name": "ok", "value": "secret", "domain": ".sciencedirect.com",
                 "path": "/", "expirationDate": 2000},
                {"name": "old", "value": "x", "domain": ".sciencedirect.com",
                 "expires": 999},
                {"name": "evil", "value": "x", "domain": ".example.com"},
                {"name": "tld", "value": "x", "domain": ".com"},
                {"name": "super", "value": "x"},
            ]), encoding="utf-8")
            cookies = CookieStore(path, ["sciencedirect.com"]).load(now=1000)
            self.assertEqual([c["name"] for c in cookies], ["ok"])

    def test_reads_netscape_and_applies_to_requests_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wiley.txt"
            path.write_text(
                "# Netscape HTTP Cookie File\n"
                ".wiley.com\tTRUE\t/\tTRUE\t0\tinstitution\tsecret\n",
                encoding="utf-8",
            )
            self.assertEqual(len(read_cookie_file(path)), 1)
            session = requests.Session()
            count = CookieStore(path, ["wiley.com"]).apply(session)
            self.assertEqual(count, 1)
            self.assertEqual(session.cookies.get("institution"), "secret")

    def test_import_normalizes_and_registry_is_publisher_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "export.json"
            source.write_text(json.dumps({"cookies": [
                {"name": "auth", "value": "secret", "domain": ".nature.com"},
                {"name": "other", "value": "no", "domain": ".wiley.com"},
            ]}), encoding="utf-8")
            target = root / "sessions" / "nature.json"
            self.assertEqual(import_cookie_file(source, target, ["nature.com"]), 1)
            registry = SessionRegistry(root / "sessions")
            session, count = registry.session_for("nature", ["nature.com"])
            self.assertEqual(count, 1)
            self.assertIsNone(session.cookies.get("other"))
            self.assertNotIn("secret", str(registry.status("nature", ["nature.com"])))


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import Mock, patch

import browser_fetch
import elsevier_api
import fulltext
import publisher_routes


class ElsevierAPITests(unittest.TestCase):
    def tearDown(self):
        elsevier_api.configure()

    @patch("elsevier_api.requests.get")
    def test_api_credentials_stay_in_server_side_headers(self, get):
        response = Mock(status_code=200, content=b"%PDF-test")
        get.return_value = response
        elsevier_api.configure("api-secret", "institution-secret")

        self.assertEqual(
            elsevier_api.fetch_pdf("10.1016/j.cell.2024.01.001"), b"%PDF-test")

        _, kwargs = get.call_args
        self.assertIn("10.1016%2Fj.cell.2024.01.001", get.call_args.args[0])
        self.assertEqual(kwargs["headers"]["X-ELS-APIKey"], "api-secret")
        self.assertEqual(kwargs["headers"]["X-ELS-Insttoken"], "institution-secret")
        self.assertNotIn("api-secret", get.call_args.args[0])
        self.assertEqual(kwargs["params"]["httpAccept"], "application/pdf")

    @patch("elsevier_api.requests.get")
    def test_missing_api_key_does_not_make_request(self, get):
        elsevier_api.configure()
        self.assertIsNone(elsevier_api.fetch_pdf("10.1016/example"))
        get.assert_not_called()


class BrowserRouteTests(unittest.TestCase):
    def test_cookie_conversion_keeps_domain_scope_and_session_expiry(self):
        result = browser_fetch.playwright_cookies([{
            "name": "auth", "value": "secret", "domain": ".mdpi.com",
            "path": "/", "secure": True, "expires": 0,
        }])
        self.assertEqual(result[0]["domain"], ".mdpi.com")
        self.assertEqual(result[0]["expires"], -1.0)

    def test_browser_candidates_reject_social_or_unrelated_hosts(self):
        route = publisher_routes.ROUTES_BY_KEY["mdpi"]
        self.assertTrue(browser_fetch._official_candidate(
            route, "https://www.mdpi.com/2304-8158/13/7/1002/pdf-vor"))
        self.assertFalse(browser_fetch._official_candidate(
            route, "https://social.example/share?url=https://mdpi.com/article.pdf"))

    def test_mdpi_skips_generic_requests_and_uses_visible_browser_route(self):
        class Registry:
            def cookies_for(self, key, domains):
                return [{"name": "auth", "value": "secret", "domain": ".mdpi.com"}]

        old_registry = fulltext._SESSION_REGISTRY
        fulltext._SESSION_REGISTRY = Registry()
        try:
            with patch("fulltext.browser_fetch.is_enabled", return_value=True), \
                    patch("fulltext.browser_fetch.fetch_pdf", return_value=b"%PDF-browser") as fetch, \
                    patch("fulltext._download_pdf") as raw_download:
                result = fulltext._official_publisher_pdf({"doi": "10.3390/foods10081757"})
        finally:
            fulltext._SESSION_REGISTRY = old_registry
        self.assertEqual(result, b"%PDF-browser")
        fetch.assert_called_once()
        raw_download.assert_not_called()

    def test_elsevier_api_precedes_browser(self):
        with patch("fulltext.elsevier_api.configured", return_value=True), \
                patch("fulltext.elsevier_api.fetch_pdf", return_value=b"%PDF-api") as api_fetch, \
                patch("fulltext.browser_fetch.is_enabled", return_value=True), \
                patch("fulltext.browser_fetch.fetch_pdf") as browser:
            result = fulltext._official_publisher_pdf(
                {"doi": "10.1016/j.cell.2024.01.001"})
        self.assertEqual(result, b"%PDF-api")
        api_fetch.assert_called_once_with("10.1016/j.cell.2024.01.001")
        browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()

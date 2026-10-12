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
        pdf = b"%PDF-" + b"x" * 10_001
        pdf_response = Mock(status_code=200, content=pdf)
        get.return_value = pdf_response
        elsevier_api.configure("api-secret", "institution-secret")

        with patch("fitz.open") as open_pdf:
            open_pdf.return_value.__enter__.return_value.page_count = 2
            self.assertEqual(
                elsevier_api.fetch_pdf("10.1016/j.cell.2024.01.001"), pdf)

        get.assert_called_once()
        call = get.call_args
        self.assertIn("10.1016%2Fj.cell.2024.01.001", call.args[0])
        self.assertIn("/content/article/doi/", call.args[0])
        self.assertEqual(call.kwargs["params"], {
            "httpAccept": "application/pdf", "amsRedirect": "true",
        })
        self.assertEqual(call.kwargs["headers"]["Accept"], "application/pdf")
        self.assertEqual(call.kwargs["headers"]["X-ELS-APIKey"], "api-secret")
        self.assertEqual(
            call.kwargs["headers"]["X-ELS-Insttoken"], "institution-secret")
        self.assertNotIn("api-secret", call.args[0])
        self.assertNotIn("institution-secret", call.args[0])

    @patch("elsevier_api.requests.get")
    def test_invalid_article_pdf_falls_back_to_metadata_attachment(self, get):
        pdf = b"%PDF-" + b"x" * 10_001
        invalid_article = Mock(status_code=403, content=b"")
        metadata = Mock(status_code=200, text=(
            "<root><attachment><eid>1-s2.0-S123-main.pdf</eid>"
            "<filename>article-main.pdf</filename>"
            "<mimetype>application/pdf</mimetype><type>MAIN</type>"
            "</attachment></root>"))
        attachment = Mock(status_code=200, content=pdf)
        get.side_effect = [invalid_article, metadata, attachment]
        elsevier_api.configure("api-secret")

        with patch("fitz.open") as open_pdf:
            open_pdf.return_value.__enter__.return_value.page_count = 2
            self.assertEqual(elsevier_api.fetch_pdf("10.1016/example"), pdf)

        self.assertEqual(get.call_count, 3)
        self.assertIn("/content/article/doi/", get.call_args_list[0].args[0])
        self.assertEqual(get.call_args_list[1].kwargs["params"], {
            "view": "META", "httpAccept": "text/xml",
        })
        self.assertIn("/content/object/eid/", get.call_args_list[2].args[0])

    @patch("elsevier_api.requests.get")
    def test_missing_api_key_does_not_make_request(self, get):
        elsevier_api.configure()
        self.assertIsNone(elsevier_api.fetch_pdf("10.1016/example"))
        get.assert_not_called()

    def test_attachment_selection_uses_structured_pdf_metadata_only(self):
        xml = """
        <root>
          <attachment><eid>1-s2.0-S1-gr1.jpg</eid><mimetype>image/jpeg</mimetype>
            <type>IMAGE-HIGH-RES</type></attachment>
          <attachment><eid>1-s2.0-S1-mmc1.pdf</eid><mimetype>application/pdf</mimetype>
            <type>SUPPLEMENT</type></attachment>
          <attachment><eid>1-s2.0-S1-main.pdf</eid><mimetype>application/pdf</mimetype>
            <type>MAIN</type></attachment>
        </root>
        """

        self.assertEqual(
            [x["eid"] for x in elsevier_api._pdf_attachments(xml)],
            ["1-s2.0-S1-main.pdf"],
        )

    @patch("fitz.open", side_effect=RuntimeError("corrupt"))
    def test_corrupt_pdf_is_rejected(self, _open_pdf):
        response = Mock(status_code=200, content=b"%PDF-" + b"x" * 10_001)
        self.assertIsNone(elsevier_api._validated_pdf(response))

    @patch("elsevier_api.requests.get")
    def test_fulltext_requests_full_xml_and_reports_pdf_eids(self, get):
        article = Mock(status_code=200, text="""
          <article><title>Example</title><abstract>Summary</abstract>
            <body><section><section-title>Results</section-title>
              <para>Body text</para></section></body></article>""")
        metadata = Mock(status_code=200, text="""
          <root><attachment><eid>1-s2.0-S1-main.pdf</eid>
            <mimetype>application/pdf</mimetype><type>MAIN</type>
          </attachment></root>""")
        get.side_effect = [article, metadata]
        elsevier_api.configure("api-secret")

        result = elsevier_api.fetch_fulltext("10.1016/example")

        self.assertEqual(result["title"], "Example")
        self.assertIn("Body text", result["full_text"])
        self.assertEqual(result["pdf_eids"], ["1-s2.0-S1-main.pdf"])
        self.assertEqual(get.call_args_list[0].kwargs["params"], {
            "view": "FULL", "httpAccept": "text/xml",
        })
        self.assertEqual(get.call_args_list[0].kwargs["headers"]["Accept"], "text/xml")

    def test_nested_figure_caption_is_collected(self):
        result = elsevier_api._parse_xml("""
          <article xmlns:ce="urn:test"><ce:figure><ce:label>Fig. 1</ce:label>
            <ce:caption><ce:simple-para>Pathway <ce:italic>in vivo</ce:italic>.</ce:simple-para>
            </ce:caption></ce:figure></article>""")

        self.assertEqual(result["figures"], [{
            "label": "Fig. 1", "caption": "Pathway in vivo .", "eid": "",
        }])


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

    def test_elsevier_api_failure_falls_back_to_browser(self):
        with patch("fulltext.elsevier_api.configured", return_value=True), \
                patch("fulltext.elsevier_api.fetch_pdf", return_value=None) as api_fetch, \
                patch("fulltext.browser_fetch.is_enabled", return_value=True), \
                patch("fulltext.browser_fetch.fetch_pdf", return_value=b"%PDF-browser") as browser:
            record = {"doi": "10.1016/j.cell.2024.01.001"}
            result = fulltext._official_publisher_pdf(record)

        self.assertEqual(result, b"%PDF-browser")
        api_fetch.assert_called_once_with(record["doi"])
        browser.assert_called_once()


if __name__ == "__main__":
    unittest.main()

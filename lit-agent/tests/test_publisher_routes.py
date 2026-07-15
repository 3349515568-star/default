import unittest

import fulltext
import publisher_routes


class PublisherRoutesTests(unittest.TestCase):
    def test_infers_route_by_doi_and_domain(self):
        self.assertEqual(
            publisher_routes.route_for_record({"doi": "10.1016/j.cell.2024.01.001"}).key,
            "elsevier",
        )
        self.assertEqual(
            publisher_routes.route_for_record({}, "https://subdomain.nature.com/articles/x").key,
            "nature",
        )
        self.assertEqual(
            publisher_routes.route_for_record(
                {"publisher": "Springer Nature"}, "https://www.nature.com/articles/x").key,
            "nature",
        )

    def test_builds_template_route(self):
        urls = publisher_routes.build_pdf_candidates({"doi": "10.1021/acs.jmedchem.4c00001"})
        self.assertEqual(urls, ["https://pubs.acs.org/doi/pdf/10.1021/acs.jmedchem.4c00001"])

    def test_mdpi_uses_landing_page_discovery_not_a_guessed_pdf_path(self):
        self.assertEqual(
            publisher_routes.build_pdf_candidates({"doi": "10.3390/foods13071002"}),
            [],
        )

    def test_builds_special_elsevier_and_ieee_routes(self):
        elsevier = publisher_routes.build_pdf_candidates(
            {"doi": "10.1016/j.cell.2024.01.001"},
            "https://www.sciencedirect.com/science/article/pii/S0092867424000012",
        )
        self.assertIn("/pii/S0092867424000012/pdfft", elsevier[0])
        ieee = publisher_routes.build_pdf_candidates(
            {"doi": "10.1109/TMI.2024.1234567"},
            "https://ieeexplore.ieee.org/document/1234567",
        )
        self.assertEqual(
            ieee, ["https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=1234567"])

    def test_discovers_relative_meta_pdf_and_drops_supplement(self):
        html = """
        <meta name="citation_pdf_url" content="/article/main.pdf">
        <a href="/article/main-supplement.pdf">PDF supplement</a>
        """
        urls = publisher_routes.build_pdf_candidates(
            {"doi": "10.1017/S0000000000000001"},
            "https://www.cambridge.org/core/journals/example/article/x",
            html,
        )
        self.assertEqual(urls, ["https://www.cambridge.org/article/main.pdf"])

    def test_plos_and_copernicus_special_templates(self):
        plos = publisher_routes.build_pdf_candidates({"doi": "10.1371/journal.pone.0123456"})
        self.assertIn("journals.plos.org/plosone/article/file", plos[0])
        copernicus = publisher_routes.build_pdf_candidates({"doi": "10.5194/acp-24-123-2024"})
        self.assertEqual(
            copernicus,
            ["https://acp.copernicus.org/articles/24/123/2024/acp-24-123-2024.pdf"],
        )

    def test_fulltext_uses_scoped_session_for_doi_landing_discovery(self):
        class Response:
            status_code = 200

            def __init__(self, url, content=b"", text=""):
                self.url, self.content, self.text = url, content, text

        class Session:
            def __init__(self):
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append(url)
                if url.startswith("https://doi.org/"):
                    return Response(
                        "https://www.sciencedirect.com/science/article/pii/S1234567890",
                        b"<html>",
                        '<meta name="citation_pdf_url" content="/science/article/pii/S1234567890/pdfft">',
                    )
                return Response(url, b"%PDF-test")

        class Registry:
            def __init__(self):
                self.request = None
                self.session = Session()

            def session_for(self, key, domains):
                self.request = (key, domains)
                return self.session, 1

        old_registry = fulltext._SESSION_REGISTRY
        registry = Registry()
        fulltext._SESSION_REGISTRY = registry
        try:
            pdf = fulltext._official_publisher_pdf({"doi": "10.1016/j.cell.2024.01.001"})
        finally:
            fulltext._SESSION_REGISTRY = old_registry
        self.assertEqual(pdf, b"%PDF-test")
        self.assertEqual(registry.request[0], "elsevier")
        self.assertIn("sciencedirect.com", registry.request[1])
        self.assertEqual(registry.session.calls[0], "https://doi.org/10.1016/j.cell.2024.01.001")


if __name__ == "__main__":
    unittest.main()

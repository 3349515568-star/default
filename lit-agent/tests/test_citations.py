import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import citations


class BibtexParseTests(unittest.TestCase):
    def _write(self, text: str) -> str:
        f = tempfile.NamedTemporaryFile(
            "w", suffix=".bib", delete=False, encoding="utf-8")
        f.write(text)
        f.close()
        return f.name

    def test_nested_braces_in_title_do_not_truncate_entry(self):
        path = self._write(
            "@article{key1,\n"
            "  title = {The {BRCA1} gene in {DNA} repair},\n"
            "  author = {Wang, Yi},\n"
            "  year = {2024},\n"
            "  doi = {10.1000/xyz},\n"
            "}\n"
            "@article{key2,\n"
            "  title = {Plain title},\n"
            "  year = {2023},\n"
            "}\n")

        entries = citations.read_bibtex_entries(path)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["key"], "key1")
        self.assertEqual(entries[0]["fields"]["title"],
                         "The {BRCA1} gene in {DNA} repair")
        self.assertEqual(entries[0]["fields"]["doi"], "10.1000/xyz")
        self.assertEqual(entries[1]["key"], "key2")
        self.assertEqual(entries[1]["fields"]["year"], "2023")
        Path(path).unlink()

    def test_escaped_braces_are_ignored_during_balancing(self):
        path = self._write(
            "@misc{esc,\n"
            "  note = {see \\{literal\\} braces},\n"
            "  year = {2022},\n"
            "}\n")

        entries = citations.read_bibtex_entries(path)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["fields"]["year"], "2022")
        Path(path).unlink()


class VerifyDoiTests(unittest.TestCase):
    @patch("citations.requests.get")
    def test_verify_doi_404_returns_false_none(self, get):
        get.return_value = Mock(status_code=404, headers={})

        self.assertEqual(citations.verify_doi("10.1000/nope"), (False, None))

    @patch("citations.requests.get")
    def test_verify_doi_timeout_returns_none_none(self, get):
        get.side_effect = requests.Timeout()

        self.assertEqual(citations.verify_doi("10.1000/x"), (None, None))

    @patch("citations.requests.get")
    def test_verify_doi_429_and_5xx_return_none_none(self, get):
        get.return_value = Mock(status_code=429, headers={})
        self.assertEqual(citations.verify_doi("10.1000/x"), (None, None))

        get.return_value = Mock(status_code=503, headers={})
        self.assertEqual(citations.verify_doi("10.1000/x"), (None, None))

    @patch("citations.requests.get")
    def test_verify_doi_success_and_url_quoting(self, get):
        response = Mock(status_code=200, headers={})
        response.raise_for_status.return_value = None
        response.json.return_value = {"message": {"title": ["T"]}}
        get.return_value = response

        ok, msg = citations.verify_doi("10.1000/ABC DEF")

        self.assertIs(ok, True)
        self.assertEqual(msg["title"], ["T"])
        self.assertIn("10.1000/abc%20def", get.call_args.args[0])

    @patch("citations.requests.get")
    def test_validate_dois_marks_network_failure(self, get):
        get.side_effect = requests.ConnectionError()

        results = citations.validate_dois(["10.1000/x"])

        self.assertIsNone(results["10.1000/x"]["ok"])
        self.assertEqual(results["10.1000/x"]["status"], "无法验证（网络）")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()

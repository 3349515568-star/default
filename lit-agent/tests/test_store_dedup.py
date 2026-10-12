import sqlite3
import unittest

import store


class StoreDedupTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(store.SCHEMA)
        self.preprint = {
            "uid": "10.20944/preprints202606.0709.v1",
            "doi": "10.20944/preprints202606.0709.v1",
            "title": ("Genome-Wide Identification and Functional Verification of Core "
                      "<em>BBEL</em> Genes for BIA Biosynthesis in "
                      "<em>Corydalis saxicola</em> Bunting"),
            "authors": "Kang L, Liu H, Zhu D, Li C, Lei M, Zhang Z.",
            "year": 2026,
            "status": "ingested",
        }
        store.upsert(self.conn, self.preprint)

    def tearDown(self):
        self.conn.close()

    def test_html_markup_does_not_change_normalized_title(self):
        self.assertEqual(store.norm_title("A <em>BBEL</em> study"),
                         store.norm_title("A &lt;i&gt;BBEL&lt;/i&gt; study"))

    def test_expanded_published_title_matches_preprint(self):
        published = {
            "doi": "10.3390/ijms27156708",
            "title": ("Genome-Wide Identification and Functional Verification of Core "
                      "&lt;i&gt;Berberine Bridge-like Enzyme&lt;/i&gt; Genes for "
                      "Benzylisoquinoline Alkaloid Biosynthesis in "
                      "&lt;i&gt;Corydalis saxicola&lt;/i&gt; Bunting"),
            "authors": "Kang L, Liu H, Zhu D, Li C, Lei M, Zhang Z.",
            "year": 2026,
        }
        self.assertTrue(store.matches_seen(published, store.seen_records(self.conn)))

    def test_same_authors_and_year_do_not_hide_unrelated_title(self):
        unrelated = {
            "doi": "10.1000/different",
            "title": "A distinct metabolomics study of drought response in rice roots",
            "authors": "Kang L, Liu H, Zhu D, Li C, Lei M, Zhang Z.",
            "year": 2026,
        }
        self.assertFalse(store.matches_seen(unrelated, store.seen_records(self.conn)))


if __name__ == "__main__":
    unittest.main()

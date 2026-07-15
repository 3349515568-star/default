import sqlite3
import unittest
from unittest.mock import Mock, patch

import lit_agent
import semantic_scholar
import store


class SemanticScholarTests(unittest.TestCase):
    def tearDown(self):
        semantic_scholar.init("")

    def test_normalize_query_removes_boolean_syntax_and_hyphens(self):
        query = '(methyltransferase OR demethylase) AND "gene-editing"[Title/Abstract]'

        normalized = semantic_scholar.normalize_query(query)

        self.assertEqual(normalized, "methyltransferase demethylase gene editing")

    @patch("semantic_scholar.requests.get")
    def test_search_parses_citations_external_ids_and_oa_pdf(self, get):
        response = Mock(status_code=200, headers={})
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": [{
            "paperId": "s2-id", "title": "Paper.", "abstract": "Abstract",
            "year": 2025, "venue": "Journal", "citationCount": 123,
            "influentialCitationCount": 17, "isOpenAccess": True,
            "externalIds": {"DOI": "10.1/ABC", "PubMed": "42", "PubMedCentral": "99"},
            "authors": [{"name": "A"}, {"name": "B"}],
            "url": "https://www.semanticscholar.org/paper/s2-id",
            "openAccessPdf": {"url": "https://example.org/paper.pdf"},
        }]}
        get.return_value = response
        semantic_scholar.init("secret")

        rows = semantic_scholar.search("gene editing", 30, 2020)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["doi"], "10.1/abc")
        self.assertEqual(rows[0]["pmcid"], "PMC99")
        self.assertEqual(rows[0]["citations"], 123)
        self.assertEqual(rows[0]["influential_citations"], 17)
        self.assertEqual(rows[0]["s2_pdf_url"], "https://example.org/paper.pdf")
        kwargs = get.call_args.kwargs
        self.assertEqual(kwargs["params"]["year"], "2020-")
        self.assertEqual(kwargs["headers"]["x-api-key"], "secret")

    @patch("semantic_scholar.time.sleep")
    @patch("semantic_scholar.requests.get")
    def test_search_retries_429(self, get, sleep):
        limited = Mock(status_code=429, headers={"Retry-After": "1"})
        ok = Mock(status_code=200, headers={})
        ok.raise_for_status.return_value = None
        ok.json.return_value = {"data": []}
        get.side_effect = [limited, ok]

        self.assertEqual(semantic_scholar.search("query", 5), [])
        sleep.assert_called_once_with(1.0)


class CandidateMergeTests(unittest.TestCase):
    def test_s2_enriches_duplicate_from_epmc(self):
        epmc = {
            "source": "epmc", "doi": "10.1/x", "title": "Same paper",
            "abstract": "short", "citations": 10, "year": 2024,
            "pmid": "1", "pmcid": "", "journal": "J", "authors": "A",
            "has_pdf": False, "is_oa": False,
        }
        s2 = {
            "source": "s2", "doi": "10.1/x", "title": "Same paper",
            "abstract": "a much longer abstract", "citations": 100,
            "influential_citations": 9, "year": 2024, "pmid": "", "pmcid": "",
            "journal": "", "authors": "", "has_pdf": True, "is_oa": True,
            "s2_paper_id": "s2", "s2_url": "page", "s2_pdf_url": "pdf",
        }

        rows = lit_agent._merge_candidate_sources([epmc], [s2])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "epmc+s2")
        self.assertEqual(rows[0]["citations"], 100)
        self.assertEqual(rows[0]["influential_citations"], 9)
        self.assertEqual(rows[0]["abstract"], "a much longer abstract")
        self.assertEqual(rows[0]["s2_pdf_url"], "pdf")

    def test_s2_only_record_gets_stable_uid(self):
        uid = store.make_uid({"s2_paper_id": "abc", "title": "Paper"})
        self.assertEqual(uid, "s2:abc")


class CostAggregationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(store.SCHEMA)
        self.conn.execute(store.USAGE_SCHEMA)
        store._migrate(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_period_and_scope_cost_aggregation(self):
        rows = [{
            "tag": "score", "model": "model-a", "prompt_tokens": 1_000_000,
            "completion_tokens": 500_000, "total_tokens": 1_500_000,
        }]
        store.log_usage(self.conn, rows, ref="[打分] topic", scope="profile:topic")

        period = store.usage_period_total(self.conn, "2000-01-01")
        scoped = store.usage_by_scope_model(self.conn)
        amount, unknown = lit_agent._estimate_cost(
            store.usage_by_model(self.conn), {"model-a": [1.0, 2.0]})

        self.assertEqual(period["t"], 1_500_000)
        self.assertEqual(scoped[0]["scope_name"], "profile:topic")
        self.assertEqual(amount, 2.0)
        self.assertEqual(unknown, set())

    def test_mixed_currency_cost_does_not_mislabel_usd_as_yuan(self):
        rows = [
            {"model": "cn-model", "p": 1_000_000, "c": 500_000},
            {"model": "us-model", "p": 2_000_000, "c": 250_000},
        ]

        yuan, usd, unknown = lit_agent._estimate_mixed_cost(
            rows, {"cn-model": [1.0, 2.0]}, {"us-model": [0.8, 4.8]})

        self.assertEqual(yuan, 2.0)
        self.assertEqual(usd, 2.8)
        self.assertEqual(unknown, set())


if __name__ == "__main__":
    unittest.main()

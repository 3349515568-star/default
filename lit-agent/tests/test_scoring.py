import unittest
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import llm
import lit_agent
import review


def candidate(title, citations=0, year=2025):
    return {
        "title": title,
        "abstract": "abstract",
        "citations": citations,
        "year": year,
    }


class ScoreValidationTests(unittest.TestCase):
    def test_rejects_duplicate_out_of_range_and_non_finite_scores(self):
        data = {"scores": [
            {"i": 0, "score": 8},
            {"i": 0, "score": 9},
            {"i": 1, "score": "nan"},
            {"i": 2, "score": 11},
            {"i": 3, "score": 6},
        ]}

        self.assertEqual(llm._validated_scores(data, 4, 10), {13: 6.0})

    @patch("llm._log_raw")
    @patch("llm._score_one_batch", side_effect=ValueError("bad response"))
    def test_batch_failure_is_unscored_instead_of_defaulting_to_five(
            self, _score_one_batch, _log_raw):
        scores = llm.score_relevance("query", [candidate("paper")])

        self.assertEqual(scores, {})


class RankingGateTests(unittest.TestCase):
    def setUp(self):
        self.rank_cfg = {
            "w_relevance": 1.0,
            "w_citation": 0.6,
            "w_recency": 0.3,
            "min_relevance": 6.0,
            "audit_min_candidates": 3,
        }

    def test_zero_score_cannot_be_rescued_by_extreme_citations(self):
        candidates = [
            candidate("irrelevant classic", citations=10_000_000, year=2025),
            candidate("relevant new paper", citations=0, year=2025),
        ]

        selected, stats = lit_agent._rank_and_select(
            candidates, {0: 0, 1: 6}, self.rank_cfg, quota=10)

        self.assertEqual([row["title"] for row in selected], ["relevant new paper"])
        self.assertEqual(stats["low_relevance"], 1)

    def test_all_zero_scores_select_nothing(self):
        candidates = [candidate("a"), candidate("b")]

        selected, stats = lit_agent._rank_and_select(
            candidates, {0: 0, 1: 0}, self.rank_cfg, quota=10)

        self.assertEqual(selected, [])
        self.assertEqual(stats["eligible"], 0)

    def test_daily_minimum_falls_back_to_highest_valid_score(self):
        candidates = [
            candidate("lower", citations=1_000_000),
            candidate("highest", citations=0),
        ]

        selected, stats = lit_agent._rank_and_select(
            candidates, {0: 3, 1: 5}, self.rank_cfg, quota=10, minimum=1)

        self.assertEqual([row["title"] for row in selected], ["highest"])
        self.assertTrue(selected[0]["selection_fallback"])
        self.assertEqual(selected[0]["selection_threshold"], 6.0)
        self.assertEqual(stats["fallback_selected"], 1)

    def test_daily_minimum_never_uses_invalid_scores(self):
        candidates = [candidate("missing"), candidate("invalid")]

        selected, stats = lit_agent._rank_and_select(
            candidates, {1: float("nan")}, self.rank_cfg, quota=10, minimum=1)

        self.assertEqual(selected, [])
        self.assertEqual(stats["fallback_selected"], 0)

    def test_daily_minimum_does_not_expand_normal_shortlist(self):
        candidates = [candidate("eligible"), candidate("low")]

        selected, stats = lit_agent._rank_and_select(
            candidates, {0: 7, 1: 5}, self.rank_cfg, quota=10, minimum=1)

        self.assertEqual([row["title"] for row in selected], ["eligible"])
        self.assertEqual(stats["fallback_selected"], 0)

    def test_missing_and_invalid_scores_fail_closed(self):
        candidates = [candidate("missing"), candidate("invalid"), candidate("valid")]

        selected, stats = lit_agent._rank_and_select(
            candidates, {1: float("inf"), 2: 8}, self.rank_cfg, quota=10)

        self.assertEqual([row["title"] for row in selected], ["valid"])
        self.assertEqual(stats["unscored"], 2)

    def test_relevance_first_prevents_citations_from_overtaking_relevance(self):
        candidates = [
            candidate("higher relevance", citations=0),
            candidate("higher citations", citations=1_000_000),
        ]

        selected, _ = lit_agent._rank_and_select(
            candidates, {0: 7, 1: 6}, self.rank_cfg, quota=10)

        self.assertEqual(
            [row["title"] for row in selected],
            ["higher relevance", "higher citations"],
        )

    def test_combined_score_ranking_can_be_restored_from_config(self):
        candidates = [
            candidate("higher relevance", citations=0),
            candidate("higher citations", citations=1_000_000),
        ]
        rank_cfg = {**self.rank_cfg, "relevance_first": False}

        selected, _ = lit_agent._rank_and_select(
            candidates, {0: 7, 1: 6}, rank_cfg, quota=10)

        self.assertEqual(
            [row["title"] for row in selected],
            ["higher citations", "higher relevance"],
        )

    @patch("lit_agent.llm.score_relevance", return_value={0: 4, 1: 7})
    def test_strict_audit_uses_lower_score_and_drops_false_positive(self, scorer):
        candidates = [candidate("false positive"), candidate("confirmed"),
                      candidate("confirmed 2"), candidate("low")]

        scores, stats = lit_agent._strict_audit(
            "intent", candidates, {0: 8, 1: 8, 2: 8, 3: 3}, self.rank_cfg)

        self.assertEqual(scores, {0: 4.0, 1: 7.0, 3: 3})
        self.assertEqual(stats, {
            "audited": 3, "downgraded": 1, "audit_failed": 1,
            "skipped_small": 0,
        })
        scorer.assert_called_once_with(
            "intent", candidates[:3], audit=True, threshold=6.0)

    @patch("lit_agent.llm.score_relevance", return_value={})
    def test_strict_audit_fails_closed_when_audit_score_is_missing(self, _scorer):
        scores, stats = lit_agent._strict_audit(
            "intent", [candidate("a"), candidate("b"), candidate("c")],
            {0: 9, 1: 8, 2: 7}, self.rank_cfg)

        self.assertEqual(scores, {})
        self.assertEqual(stats["audit_failed"], 3)

    @patch("lit_agent.llm.score_relevance")
    def test_strict_audit_skips_when_at_most_two_candidates_pass(self, scorer):
        candidates = [candidate("a"), candidate("b"), candidate("low")]

        scores, stats = lit_agent._strict_audit(
            "intent", candidates, {0: 9, 1: 8, 2: 2}, self.rank_cfg)

        self.assertEqual(scores, {0: 9, 1: 8, 2: 2})
        self.assertEqual(stats["skipped_small"], 2)
        scorer.assert_not_called()

    @patch("lit_agent.llm.score_relevance")
    def test_strict_audit_can_be_disabled(self, scorer):
        rank_cfg = {**self.rank_cfg, "verify_shortlist": False}

        scores, stats = lit_agent._strict_audit(
            "intent", [candidate("paper")], {0: 9}, rank_cfg)

        self.assertEqual(scores, {0: 9})
        self.assertEqual(stats["audited"], 0)
        scorer.assert_not_called()


class BlacklistTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "enabled": True,
            "doi_prefixes": ["10.3389/"],
            "publishers": ["Frontiers Media", "Frontiers"],
            "journals": ["Blocked Journal"],
            "journal_prefixes": ["Frontiers in"],
        }

    def test_frontiers_doi_is_blocked_case_insensitively(self):
        rec = {"doi": "10.3389/FNINS.2025.1", "journal": "Other Journal"}

        self.assertEqual(lit_agent._blacklist_reason(rec, self.cfg), "DOI 前缀")

    def test_journal_and_publisher_fallbacks_cover_missing_doi(self):
        self.assertEqual(lit_agent._blacklist_reason(
            {"doi": "", "journal": "Frontiers in Pharmacology"}, self.cfg),
            "期刊名前缀",
        )
        self.assertEqual(lit_agent._blacklist_reason(
            {"doi": "", "journal": "Journal", "publisher": "Frontiers Media SA"},
            self.cfg,
        ), "出版商")

    def test_non_frontiers_journal_is_kept(self):
        rec = {"doi": "10.1038/example", "journal": "Nature Medicine"}

        self.assertIsNone(lit_agent._blacklist_reason(rec, self.cfg))

    def test_filter_reports_reasons_and_keeps_only_allowed_candidates(self):
        candidates = [
            {"title": "doi block", "doi": "10.3389/a", "journal": "J"},
            {"title": "journal block", "doi": "", "journal": "Blocked Journal"},
            {"title": "keep", "doi": "10.1/x", "journal": "Good Journal"},
        ]

        kept, stats = lit_agent._filter_blacklisted(candidates, self.cfg)

        self.assertEqual([rec["title"] for rec in kept], ["keep"])
        self.assertEqual(stats["blocked"], 2)
        self.assertEqual(stats["reasons"], {"DOI 前缀": 1, "期刊名": 1})

    def test_blacklist_can_be_disabled(self):
        rec = {"doi": "10.3389/a", "journal": "Frontiers in Medicine"}

        self.assertIsNone(lit_agent._blacklist_reason(
            rec, {**self.cfg, "enabled": False}))

    def test_default_config_blocks_omics_network_doi_prefixes(self):
        cfg = lit_agent.load_config()["blacklist"]

        for doi in (
            "10.4172/example", "10.36648/example", "10.35841/example",
            "10.37532/example",
        ):
            with self.subTest(doi=doi):
                self.assertEqual(lit_agent._blacklist_reason(
                    {"doi": doi, "journal": "Unknown"}, cfg), "DOI 前缀")

    def test_default_config_blocks_omics_network_publisher_names(self):
        cfg = lit_agent.load_config()["blacklist"]

        for publisher in (
            "OMICS Publishing Group", "Insight Medical Publishing",
            "Allied Academies", "Pulsus Group", "Trade Science Inc",
            "SciTechnol", "EuroSciCon",
        ):
            with self.subTest(publisher=publisher):
                self.assertEqual(lit_agent._blacklist_reason({
                    "doi": "", "journal": "Unknown", "publisher": publisher,
                }, cfg), "出版商")

class SummaryScoreTests(unittest.TestCase):
    def test_summary_reuses_selection_score(self):
        self.assertEqual(llm._relevance_note({"relevance": 8.5}), "8.5/10")

    def test_summary_marks_manual_import_as_unscored(self):
        self.assertIn("未评分", llm._relevance_note({"relevance": None}))

    def test_summary_file_header_contains_authoritative_selection_score(self):
        header = lit_agent._summary_header({
            "title": "paper", "journal": "journal", "year": 2025,
            "citations": 1, "doi": "10.test/x", "authors": "author",
            "relevance": 7,
        })

        self.assertIn("最终筛选相关度：7/10", header)

    def test_summary_file_header_marks_daily_minimum_fallback(self):
        header = lit_agent._summary_header({
            "title": "paper", "journal": "journal", "year": 2025,
            "citations": 1, "doi": "10.test/x", "authors": "author",
            "relevance": 4, "selection_fallback": True,
            "selection_threshold": 6,
        })

        self.assertIn("每日最低产出兜底", header)
        self.assertIn("低于常规门槛 6/10", header)

    @patch("llm._call_model", return_value="## ok")
    def test_abstract_summary_prompt_reuses_selection_score(self, chat):
        rec = {"title": "paper", "relevance": 7, "journal": "", "year": 2025}

        llm.summarize(rec, "abstract", "intent", False, 1000)

        prompt = chat.call_args.args[0][1]["content"]
        self.assertIn("筛选阶段相关度：7/10", prompt)
        self.assertNotIn("给 1-5 分", prompt)

    @patch("llm._call_model", return_value="## ok")
    def test_fulltext_summary_prompt_reuses_selection_score(self, chat):
        rec = {"title": "paper", "relevance": 8, "journal": "", "year": 2025}

        llm.summarize(rec, "full text", "intent", True, 1000)

        prompt = chat.call_args.args[0][1]["content"]
        self.assertIn("筛选阶段相关度：8/10", prompt)
        self.assertNotIn("给出 1-5 分", prompt)

    @patch("lit_agent.store.all_summarized")
    def test_index_shows_final_relevance(self, all_summarized):
        all_summarized.return_value = [{
            "processed_date": "2026-07-11", "status": "summarized_full",
            "relevance": 7.0, "citations": 2, "influential_citations": 1,
            "year": 2025,
            "title": "paper", "journal": "journal", "summary_path": "",
        }]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(lit_agent, "SUMMARIES", root), \
                    patch.object(lit_agent, "INDEX", root / "INDEX.md"):
                lit_agent.rebuild_index(None)

            index = (root / "INDEX.md").read_text(encoding="utf-8")
            self.assertIn("| 相关度 |", index)
            self.assertIn("| 7/10 |", index)


class ReviewGateTests(unittest.TestCase):
    def test_review_does_not_restore_low_score_rows_when_screening_is_empty(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE papers (uid TEXT, title TEXT, status TEXT, query TEXT, "
            "score REAL, relevance REAL)"
        )
        conn.execute(
            "INSERT INTO papers VALUES (?, ?, ?, ?, ?, ?)",
            ("paper-0", "irrelevant", "summarized_abstract", "target", 99, 0),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "review.md"
            with patch("review._themes_from_summaries") as themes:
                review.generate_review(
                    conn, "target", output,
                    min_relevance=6.0,
                )

            themes.assert_not_called()
            self.assertIn("未纳入低分文献", output.read_text(encoding="utf-8"))
        conn.close()


if __name__ == "__main__":
    unittest.main()

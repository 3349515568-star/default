import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import fulltext
import llm
import store


class PdfMetadataTests(unittest.TestCase):
    def test_guess_doi_searches_beyond_old_4000_character_cutoff(self):
        text = "x" * 4400 + " https://doi.org/10.1016/j.test.2026.123456 "

        self.assertEqual(
            fulltext.guess_doi(text), "10.1016/j.test.2026.123456")

    def test_metadata_hints_parse_elsevier_subject(self):
        hints = fulltext.metadata_hints({
            "title": "Paper title",
            "author": "A. Author",
            "subject": "Phytochemistry, 242 (2026) 114690. doi:10.test/x",
        })

        self.assertEqual(hints["title"], "Paper title")
        self.assertEqual(hints["authors"], "A. Author")
        self.assertEqual(hints["journal"], "Phytochemistry")
        self.assertEqual(hints["year"], 2026)


class FulltextPromptTests(unittest.TestCase):
    def setUp(self):
        self.rec = {
            "title": "Paper", "journal": "Journal", "year": 2026,
            "doi": "10.test/x", "authors": "A. Author", "relevance": 8,
        }

    def test_detects_review_from_abstract_language(self):
        text = "A B S T R A C T\nIn this review, we summarized recent advances."

        self.assertEqual(llm._detect_document_type(text), "review")

    def test_detects_systematically_review_as_strong_abstract_signal(self):
        text = (
            "ABSTRACT\nBenzylisoquinoline alkaloids are specialized metabolites. "
            "We systematically review the biosynthetic pathways and key enzymes.\n"
            "INTRODUCTION\nThe pathway begins with tyrosine."
        )

        self.assertEqual(llm._detect_document_type(text), "review")

    def test_weak_review_phrase_does_not_override_research_sections(self):
        text = (
            "Abstract\nWe review prior work before reporting a newly identified enzyme.\n"
            "2. Materials and Methods\n3. Results"
        )

        self.assertEqual(llm._detect_document_type(text), "research")

    def test_detects_original_research_from_section_headings(self):
        text = "Abstract\nWe discovered an enzyme.\n2. Materials and Methods\n3. Results"

        self.assertEqual(llm._detect_document_type(text), "research")

    def test_figure_inventory_is_complete_deduplicated_and_sorted(self):
        text = (
            "Fig. 10. caption\nFigure 2 shows x.\nFig. 1. caption\n"
            "Table 1 lists y. Fig. 2 repeats. Scheme 3 shows z."
        )

        self.assertEqual(llm._figure_inventory(text), [
            "Figure 1", "Figure 2", "Figure 10", "Table 1", "Scheme 3",
        ])

    def test_section_inventory_stops_before_references(self):
        text = (
            "1 Introduction\n2 Methods\n2.1 Inclusion criteria\n"
            "3 Results\nReferences\n2021 Review article title"
        )

        self.assertEqual(llm._section_inventory(text), [
            "1 Introduction", "2 Methods", "2.1 Inclusion criteria", "3 Results",
        ])

    def test_report_validator_detects_truncation_and_missing_figure(self):
        report = "\n".join(f"## {label}：section" for label in "一二三四五六七")

        gaps = llm._report_validation_gaps(report, ["Figure 1"])

        self.assertIn("缺少第八部分", gaps)
        self.assertIn("第五部分未覆盖 Figure 1", gaps)

    def test_report_validator_accepts_subpanel_as_main_figure_coverage(self):
        report = "\n".join([
            "## 一：基本信息", "## 二：研究背景", "## 三：研究思路",
            "## 四：研究方法", "## 五：实验设计及结果分析",
            "Figure 2a 展示主结果；Figure S2a-c 展示补充验证。",
            "## 六：总体结论", "## 七：论文评价", "## 八：关键问题及回答",
        ])

        self.assertEqual(
            llm._report_validation_gaps(report, ["Figure 2", "Figure S2"]), [])

    @patch("llm._call_model", return_value="## ok")
    def test_review_receives_only_review_template(self, chat):
        text = "In this review, we summarize the field.\nFig. 1. Overview."

        llm.summarize(self.rec, text, "topic", True, 10000)

        prompt = chat.call_args.args[0][1]["content"]
        self.assertIn("## 二：核心摘要", prompt)
        self.assertIn("Figure 1", prompt)
        self.assertIn("程序从正文识别出的原文章节", prompt)
        self.assertNotIn("## 五：实验设计及结果分析", prompt)
        self.assertEqual(chat.call_args.kwargs["task"], "summary_full")

    @patch("llm._call_model", return_value="## ok")
    def test_research_receives_only_research_template(self, chat):
        text = "2. Materials and Methods\n3. Results\nFigure 1. Enzyme assay."

        llm.summarize(self.rec, text, "topic", True, 10000)

        prompt = chat.call_args.args[0][1]["content"]
        self.assertIn("## 五：实验设计及结果分析", prompt)
        self.assertNotIn("## 二：核心摘要", prompt)
        self.assertEqual(chat.call_args.kwargs["task"], "summary_full")

    @patch("llm._call_model", return_value="## ok")
    def test_abstract_uses_low_cost_abstract_route(self, chat):
        llm.summarize(self.rec, "abstract text", "topic", False, 10000)

        self.assertEqual(chat.call_args.kwargs["task"], "summary_abstract")

    @patch("llm._call_model", return_value="evidence")
    def test_map_stage_keeps_all_topics_and_uses_page_chunks(self, chat):
        text = (
            "===== PDF PAGE 1 =====\n" + "a" * 90 + "\n"
            "===== PDF PAGE 2 =====\n" + "b" * 90
        )

        llm.summarize(self.rec, text, "narrow query", True, 130)

        self.assertEqual(chat.call_count, 3)  # 两个证据卡 + 一次成文
        self.assertTrue(all(
            call.kwargs["task"] == "summary_full"
            for call in chat.call_args_list
        ))
        first_map_prompt = chat.call_args_list[0].args[0][1]["content"]
        self.assertIn("必须保留本部分全部主题", first_map_prompt)
        self.assertIn("研究对象、样本量、剂量、频率、疗程、对照", first_map_prompt)
        self.assertIn("PDF PAGE 1", first_map_prompt)


def _discover_candidate(doi, title, abstract="abstract text"):
    return {
        "pmid": "", "pmcid": "", "doi": doi, "title": title,
        "journal": "J", "year": 2026, "citations": 5,
        "authors": "A. Author", "abstract": abstract,
        "has_pdf": True, "is_oa": True, "source": "epmc",
    }


class DiscoverShortPdfTests(unittest.TestCase):
    """discover 精读循环：短 PDF / 损坏 PDF 都退回摘要模板，且短 PDF 不落盘。"""

    def setUp(self):
        import lit_agent
        self.lit_agent = lit_agent
        self.tmp = TemporaryDirectory()
        tmp = Path(self.tmp.name)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(store.SCHEMA)
        conn.execute(store.USAGE_SCHEMA)
        self.conn = conn
        self.cfg = {
            "search": {"min_year": 2020, "candidates": 10},
            "limits": {"daily_max": 5, "daily_min": 0},
            "rank": {"min_relevance": 6.0, "w_relevance": 1.0,
                     "w_citation": 0.0, "w_recency": 0.0,
                     "verify_shortlist": False},
            "fulltext": {"prefer_repository": False, "max_chars": 10000},
            "blacklist": {"enabled": False},
        }
        self.args = SimpleNamespace(
            profile=None, query="q", intent=None, min_year=None, max=None,
            source="epmc", no_fulltext=False,
            score_model=None, summary_model=None)
        self.patches = [
            patch.object(store, "connect", return_value=conn),
            patch.object(lit_agent.europepmc, "search"),
            patch.object(lit_agent.semantic_scholar, "search", return_value=[]),
            patch.object(lit_agent.ncbi, "search_pubmed", return_value=[]),
            patch.object(llm, "score_relevance"),
            patch.object(llm, "summarize", return_value="body"),
            patch.object(fulltext, "fetch_pdf"),
            patch.object(fulltext, "pdf_to_text"),
            patch.object(lit_agent, "rebuild_index", lambda conn: None),
            patch.object(lit_agent, "rebuild_to_fetch", lambda conn: None),
            patch.object(lit_agent, "SUMMARIES", tmp / "summaries"),
            patch.object(lit_agent, "PDFS", tmp / "pdfs"),
        ]
        for p in self.patches:
            p.start()
        for p in reversed(self.patches):
            self.addCleanup(p.stop)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _run(self, candidates, scores):
        lit_agent = self.lit_agent
        lit_agent.europepmc.search.return_value = candidates
        llm.score_relevance.return_value = scores
        lit_agent.cmd_discover(self.args, self.cfg, "a@b.c")

    def test_short_pdf_falls_back_to_abstract_and_is_not_saved(self):
        cand = _discover_candidate("10.1000/a", "Paper A", abstract="ABS A")
        fulltext.fetch_pdf.return_value = b"%PDF-a"
        fulltext.pdf_to_text.return_value = "x" * 200  # ≤1500 字

        self._run([cand], {0: 8.0})

        (call,) = llm.summarize.call_args_list
        self.assertEqual(call.args[1], "ABS A")   # 传给模型的是摘要，不是 PDF 乱码
        self.assertFalse(call.args[3])            # is_full=False → 走摘要模板
        pdfs_dir = Path(self.tmp.name) / "pdfs"
        written = list(pdfs_dir.glob("*.pdf")) if pdfs_dir.exists() else []
        self.assertEqual(written, [])             # 短 PDF 不落盘
        row = store.get(self.conn, "10.1000/a")
        self.assertEqual(row["status"], "summarized_abstract")
        self.assertEqual(row["pdf_path"], "")

    def test_broken_pdf_does_not_abort_the_loop(self):
        cand_bad = _discover_candidate("10.1000/bad", "Paper Bad", abstract="ABS BAD")
        cand_ok = _discover_candidate("10.1000/ok", "Paper Ok", abstract="ABS OK")

        def fake_fetch(rec, email, prefer_repo):
            return rec["doi"].encode()

        def fake_to_text(pdf_bytes):
            if pdf_bytes == b"10.1000/bad":
                raise ValueError("damaged pdf")
            return "y" * 2000

        fulltext.fetch_pdf.side_effect = fake_fetch
        fulltext.pdf_to_text.side_effect = fake_to_text

        self._run([cand_bad, cand_ok], {0: 8.0, 1: 9.0})

        self.assertEqual(llm.summarize.call_count, 2)
        calls = {c.args[0]["doi"]: c for c in llm.summarize.call_args_list}
        self.assertEqual(calls["10.1000/bad"].args[1], "ABS BAD")  # 损坏→退回摘要
        self.assertFalse(calls["10.1000/bad"].args[3])
        self.assertEqual(calls["10.1000/ok"].args[1], "y" * 2000)  # 循环继续到下一篇
        self.assertTrue(calls["10.1000/ok"].args[3])
        self.assertIsNotNone(store.get(self.conn, "10.1000/bad"))
        self.assertIsNotNone(store.get(self.conn, "10.1000/ok"))


if __name__ == "__main__":
    unittest.main()

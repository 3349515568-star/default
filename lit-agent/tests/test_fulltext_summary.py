import unittest
from unittest.mock import patch

import fulltext
import llm


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

    @patch("llm._call_model", return_value="## ok")
    def test_review_receives_only_review_template(self, chat):
        text = "In this review, we summarize the field.\nFig. 1. Overview."

        llm.summarize(self.rec, text, "topic", True, 10000)

        prompt = chat.call_args.args[0][1]["content"]
        self.assertIn("## 二：核心摘要", prompt)
        self.assertIn("Figure 1", prompt)
        self.assertIn("程序从正文识别出的原文章节", prompt)
        self.assertNotIn("## 五：实验设计及结果分析", prompt)

    @patch("llm._call_model", return_value="## ok")
    def test_research_receives_only_research_template(self, chat):
        text = "2. Materials and Methods\n3. Results\nFigure 1. Enzyme assay."

        llm.summarize(self.rec, text, "topic", True, 10000)

        prompt = chat.call_args.args[0][1]["content"]
        self.assertIn("## 五：实验设计及结果分析", prompt)
        self.assertNotIn("## 二：核心摘要", prompt)

    @patch("llm._call_model", return_value="evidence")
    def test_map_stage_keeps_all_topics_and_uses_page_chunks(self, chat):
        text = (
            "===== PDF PAGE 1 =====\n" + "a" * 90 + "\n"
            "===== PDF PAGE 2 =====\n" + "b" * 90
        )

        llm.summarize(self.rec, text, "narrow query", True, 130)

        self.assertEqual(chat.call_count, 3)  # 两个证据卡 + 一次成文
        first_map_prompt = chat.call_args_list[0].args[0][1]["content"]
        self.assertIn("必须保留本部分全部主题", first_map_prompt)
        self.assertIn("研究对象、样本量、剂量、频率、疗程、对照", first_map_prompt)
        self.assertIn("PDF PAGE 1", first_map_prompt)


if __name__ == "__main__":
    unittest.main()

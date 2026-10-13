import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import llm
import review
import store


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(store.SCHEMA)
    return conn


def _insert(conn, uid, title, year, query="crispr"):
    store.upsert(conn, {
        "uid": uid, "doi": uid, "title": title, "journal": "J",
        "year": year, "citations": 10, "authors": "A. Author",
        "status": "summarized_full", "relevance": 8.0, "score": 9.0,
        "query": query,
        "processed_date": "2026-10-01",
    })


class ThemesFromSummariesTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        _insert(self.conn, "10.1000/a", "CRISPR off-target detection", 2024)
        _insert(self.conn, "10.1000/b", "CRISPR delivery vectors", 2025)
        self.rows = self.conn.execute(
            "SELECT * FROM papers ORDER BY uid").fetchall()

    def tearDown(self):
        self.conn.close()

    def test_model_json_object_yields_themes_with_real_uids(self):
        payload = {"themes": [{
            "theme": "脱靶检测",
            "papers": ["10.1000/a", "10.1000/b", "10.1000/hallucinated"],
            "narrative": "从检测到递送。",
        }]}
        with patch.object(llm, "_call_model", return_value=json.dumps(payload)):
            themes = review._themes_from_summaries(self.rows, 10000)

        self.assertEqual(len(themes), 1)
        self.assertEqual(themes[0]["theme"], "脱靶检测")
        # 编造的 uid 被丢弃，只保留真实存在的
        self.assertEqual(themes[0]["papers"], ["10.1000/a", "10.1000/b"])

    def test_garbage_response_falls_back_to_year_buckets(self):
        with patch.object(llm, "_call_model", return_value="不是 JSON，垃圾输出"):
            themes = review._themes_from_summaries(self.rows, 10000)

        by_year = {t["theme"]: t["papers"] for t in themes}
        self.assertIn("2024 年研究", by_year)
        self.assertIn("2025 年研究", by_year)
        self.assertEqual(by_year["2024 年研究"], ["10.1000/a"])
        self.assertEqual(by_year["2025 年研究"], ["10.1000/b"])

    def test_model_error_propagates(self):
        # 只有解析失败才走兜底；API 异常本身继续上抛，不悄悄掩盖
        with patch.object(llm, "_call_model", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                review._themes_from_summaries(self.rows, 10000)


class GenerateReviewTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        _insert(self.conn, "10.1000/a", "CRISPR off-target detection", 2024)
        _insert(self.conn, "10.1000/b", "CRISPR delivery vectors", 2025)
        self.tmp = TemporaryDirectory()
        self.out = Path(self.tmp.name) / "review.md"

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_generate_review_writes_file_with_theme_section(self):
        payload = {"themes": [{
            "theme": "脱靶检测", "papers": ["10.1000/a"],
            "narrative": "检测方法不断演进。",
        }]}
        with patch.object(llm, "_call_model", return_value=json.dumps(payload)):
            result = review.generate_review(self.conn, "crispr", self.out)

        self.assertEqual(result, self.out)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("# 文献综述：crispr", text)
        self.assertIn("### 脱靶检测", text)
        self.assertIn("PRISMA", text)
        self.assertIn("CRISPR off-target detection", text)

    def test_generate_review_survives_garbage_model_output(self):
        with patch.object(llm, "_call_model", return_value="垃圾"):
            review.generate_review(self.conn, "crispr", self.out)

        text = self.out.read_text(encoding="utf-8")
        self.assertIn("年研究", text)

    def test_generate_review_without_llm_summarize_fn_parameter(self):
        # 死参数已删除：generate_review 不再接受 llm_summarize_fn
        payload = {"themes": [{"theme": "t", "papers": [], "narrative": ""}]}
        with patch.object(llm, "_call_model", return_value=json.dumps(payload)):
            review.generate_review(
                self.conn, "crispr", self.out, max_chars=10000,
                min_relevance=6.0)
        self.assertTrue(self.out.exists())


if __name__ == "__main__":
    unittest.main()

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import fulltext
import lit_agent
import llm
import store


class FileStemTests(unittest.TestCase):
    def test_same_title_prefix_different_doi_gives_different_stems(self):
        title = "A" * 80  # 前 60 字符完全相同
        a = {"title": title, "doi": "10.1000/aaa"}
        b = {"title": title, "doi": "10.1000/bbb"}

        self.assertNotEqual(lit_agent.file_stem(a), lit_agent.file_stem(b))

    def test_symbol_only_title_is_not_bare_untitled(self):
        stem = lit_agent.file_stem({"title": "!!!", "doi": "10.1000/x"})

        self.assertNotEqual(stem, "untitled")
        self.assertTrue(stem.startswith("untitled_"))

    def test_stem_is_deterministic(self):
        rec = {"title": "Paper", "doi": "10.1000/x"}

        self.assertEqual(lit_agent.file_stem(rec), lit_agent.file_stem(dict(rec)))


class IngestTitleMatchTests(unittest.TestCase):
    def test_short_title_does_not_pass_substring_rule(self):
        # "Editorial" 这类短标题在任意正文里都容易出现，子串规则需要长度下限
        row = {"title": "Editorial"}
        text = "This issue contains an editorial and several research papers. " * 50

        self.assertFalse(lit_agent._ingest_title_matches(row, {}, text))

    def test_long_title_still_passes_substring_rule(self):
        row = {"title": "Berberine bridge enzyme catalysis in isoquinoline alkaloid biosynthesis"}
        text = "… " + row["title"] + " … " + "filler " * 100

        self.assertTrue(lit_agent._ingest_title_matches(row, {}, text))

    def test_short_title_can_pass_via_metadata_similarity(self):
        row = {"title": "Editorial"}

        self.assertTrue(lit_agent._ingest_title_matches(
            row, {"title": "Editorial"}, "unrelated body text"))


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.inbox = tmp / "inbox"
        self.inbox.mkdir()
        self.pdfs_dir = tmp / "pdfs"
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(store.SCHEMA)
        self.conn.execute(store.USAGE_SCHEMA)
        self.args = SimpleNamespace(query="", summary_model=None, score_model=None)
        self.cfg = {"fulltext": {"max_chars": 10000}}
        self.hints = {"title": "My Real Paper Title", "journal": "J",
                      "year": 2026, "authors": "B. Author"}
        self.patches = [
            patch.object(store, "connect", return_value=self.conn),
            patch.object(lit_agent, "INBOX", self.inbox),
            patch.object(lit_agent, "PDFS", self.pdfs_dir),
            patch.object(lit_agent, "SUMMARIES", tmp / "summaries"),
            patch.object(fulltext, "pdf_file_to_text"),
            patch.object(fulltext, "pdf_file_metadata", return_value={}),
            patch.object(fulltext, "metadata_hints", return_value=self.hints),
            patch.object(fulltext, "guess_doi"),
            patch.object(llm, "summarize", return_value="body"),
            patch.object(lit_agent, "rebuild_index", lambda conn: None),
            patch.object(lit_agent, "rebuild_to_fetch", lambda conn: None),
        ]
        for p in self.patches:
            p.start()
        for p in reversed(self.patches):
            self.addCleanup(p.stop)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _drop_pdf(self, name="paper.pdf", content=b"fake pdf"):
        p = self.inbox / name
        p.write_bytes(content)
        return p

    def _existing_row(self, doi, title, tmp_name="old_summary.md"):
        old_summary = Path(self.tmp.name) / tmp_name
        old_summary.write_text("old note", encoding="utf-8")
        store.upsert(self.conn, {
            "uid": doi, "doi": doi, "title": title, "journal": "J",
            "year": 2025, "citations": 3, "authors": "A. Author",
            "status": "summarized_abstract",
            "summary_path": str(old_summary), "query": "old",
            "processed_date": "2026-10-01",
        })
        return old_summary

    def test_mismatched_doi_hit_is_treated_as_new_paper(self):
        # PDF 里的 DOI 指向另一篇标题的记录：不删旧总结，另建新记录
        old_summary = self._existing_row(
            "10.1000/other", "Completely Different Research Paper")
        fulltext.guess_doi.return_value = "10.1000/other"
        fulltext.pdf_file_to_text.return_value = (
            "My Real Paper Title\n" + "z" * 1000)
        self._drop_pdf()

        lit_agent.cmd_ingest(self.args, self.cfg, "a@b.c")

        self.assertTrue(old_summary.exists())  # 旧总结没被删
        old_row = store.get(self.conn, "10.1000/other")
        self.assertEqual(old_row["status"], "summarized_abstract")
        new_row = store.get(self.conn, "ingest:paper")
        self.assertIsNotNone(new_row)
        self.assertEqual(new_row["status"], "ingested")
        self.assertEqual(new_row["title"], "My Real Paper Title")

    def test_matching_doi_replaces_old_summary(self):
        old_summary = self._existing_row(
            "10.1000/match", "Deep Enzyme Study of Catalysis Mechanisms")
        fulltext.guess_doi.return_value = "10.1000/match"
        fulltext.pdf_file_to_text.return_value = (
            "Deep Enzyme Study of Catalysis Mechanisms\n" + "z" * 1000)
        self._drop_pdf()

        lit_agent.cmd_ingest(self.args, self.cfg, "a@b.c")

        self.assertFalse(old_summary.exists())  # 旧的摘要版被全文版替换
        row = store.get(self.conn, "10.1000/match")
        self.assertEqual(row["status"], "ingested")

    def test_existing_dest_is_not_overwritten_but_gets_dup_suffix(self):
        fulltext.guess_doi.return_value = ""
        fulltext.pdf_file_to_text.return_value = "z" * 1000
        self._drop_pdf(content=b"new pdf content")
        self.pdfs_dir.mkdir()
        stem = lit_agent.file_stem({"title": self.hints["title"]})
        occupied = self.pdfs_dir / f"{stem}.pdf"
        occupied.write_bytes(b"pre-existing")

        lit_agent.cmd_ingest(self.args, self.cfg, "a@b.c")

        self.assertEqual(occupied.read_bytes(), b"pre-existing")  # 没被覆盖
        dup = self.pdfs_dir / f"{stem}_dup1.pdf"
        self.assertEqual(dup.read_bytes(), b"new pdf content")


if __name__ == "__main__":
    unittest.main()

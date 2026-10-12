import tempfile
import unittest
from pathlib import Path

import lit_agent


class ExternalSeenManifestTests(unittest.TestCase):
    def test_loads_doi_title_and_year_from_markdown_manifest(self):
        body = """# 文献总清单

| 序号 | 年份 | 类型 | 相关性 | 主主题 | 题名 | DOI | 交叉标签 | 当前文件 | 可用于综述 |
|---:|---:|---|---|---|---|---|---|---|---|
| 1 | 2026 | 综述 | 重要 | 核心 | Targeted DNA methylation | `10.1000/ABC.1` | dCas9 | [a.pdf](a.pdf) | 导论 |
| 2 | 2025 | 研究论文 | 核心 | 工具 | Title-only preprint |  | TET1 | [b.pdf](b.pdf) | 方法 |
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manifest.md"
            path.write_text(body, encoding="utf-8")

            records = lit_agent._load_seen_manifest(path)

        self.assertEqual(records, [
            {
                "uid": "10.1000/abc.1",
                "title": "Targeted DNA methylation",
                "title_key": "targeteddnamethylation",
                "authors": "",
                "year": 2026,
            },
            {
                "uid": "title:titleonlypreprint",
                "title": "Title-only preprint",
                "title_key": "titleonlypreprint",
                "authors": "",
                "year": 2025,
            },
        ])

    def test_missing_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.md"

            with self.assertRaisesRegex(ValueError, "不存在"):
                lit_agent._load_seen_manifest(missing)

    def test_manifest_without_records_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manifest.md"
            path.write_text("# 空清单\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "没有解析到"):
                lit_agent._load_seen_manifest(path)


class ExternalSeenFilterTests(unittest.TestCase):
    def test_database_then_manifest_dedup_reports_each_source(self):
        candidates = [
            {"doi": "10.db/1", "title": "database paper"},
            {"doi": "10.local/1", "title": "local DOI paper"},
            {"doi": "10.preprint/2", "title": "Same title in local library"},
            {"doi": "10.new/1", "title": "new paper"},
        ]
        database_seen = [{
            "uid": "10.db/1", "title": "database paper",
            "title_key": "databasepaper", "authors": "", "year": 2026,
        }]
        manifest_seen = [
            {
                "uid": "10.db/1", "title": "database paper",
                "title_key": "databasepaper", "authors": "", "year": 2026,
            },
            {
                "uid": "10.local/1", "title": "local DOI paper",
                "title_key": "localdoipaper", "authors": "", "year": 2026,
            },
            {
                "uid": "10.published/2", "title": "Same title in local library",
                "title_key": "sametitleinlocallibrary", "authors": "", "year": 2026,
            },
        ]

        kept, stats = lit_agent._exclude_seen_sources(
            candidates, database_seen, manifest_seen)

        self.assertEqual([row["title"] for row in kept], ["new paper"])
        self.assertEqual(stats, {"database": 1, "manifest": 2})


if __name__ == "__main__":
    unittest.main()

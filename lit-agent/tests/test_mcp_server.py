import unittest

import mcp_server


class MCPServerTests(unittest.TestCase):
    def test_discover_argv_requires_exactly_one_query_source(self):
        with self.assertRaises(ValueError):
            mcp_server._discover_argv()
        with self.assertRaises(ValueError):
            mcp_server._discover_argv(query="x", profile="p")

    def test_discover_argv_is_bounded_and_explicit(self):
        argv = mcp_server._discover_argv(
            query="CRISPR review", max_papers=5, min_year=2024,
            source="auto", no_fulltext=True, summary_model="qwen:qwen-long",
        )
        self.assertEqual(argv[0], "discover")
        self.assertIn("CRISPR review", argv)
        self.assertIn("--no-fulltext", argv)
        self.assertIn("qwen:qwen-long", argv)
        with self.assertRaises(ValueError):
            mcp_server._discover_argv(query="x", max_papers=51)

    def test_unknown_run_is_structured(self):
        result = mcp_server._public_job("missing")
        self.assertEqual(result["state"], "not_found")


if __name__ == "__main__":
    unittest.main()

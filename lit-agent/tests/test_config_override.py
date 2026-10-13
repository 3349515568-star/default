import tempfile
import unittest
from pathlib import Path
from unittest import mock

import lit_agent
import llm


class DeepMergeTests(unittest.TestCase):
    def test_tables_merge_recursively_scalars_and_lists_override(self):
        base = {"providers": {"a": {"base_url": "https://a", "key_env": "A_KEY"}},
                "models": {"score": "a:m1"},
                "blacklist": {"journals": ["X", "Y"]}}
        override = {"providers": {"b": {"base_url": "https://b", "key_env": "B_KEY"},
                                  "a": {"timeout_seconds": 30}},
                    "models": {"score": "b:m2"},
                    "blacklist": {"journals": ["Z"]}}

        merged = lit_agent._deep_merge(base, override)

        self.assertEqual(merged["providers"]["a"]["base_url"], "https://a")
        self.assertEqual(merged["providers"]["a"]["timeout_seconds"], 30)
        self.assertEqual(merged["providers"]["b"]["key_env"], "B_KEY")
        self.assertEqual(merged["models"]["score"], "b:m2")
        self.assertEqual(merged["blacklist"]["journals"], ["Z"])
        # 原对象不被修改
        self.assertNotIn("timeout_seconds", base["providers"]["a"])
        self.assertNotIn("b", base["providers"])


class LocalConfigOverrideTests(unittest.TestCase):
    def test_config_local_toml_adds_custom_provider(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "config.toml").write_text(
                "[providers.deepseek]\n"
                'base_url = "https://api.deepseek.com"\n'
                'key_env = "DEEPSEEK_API_KEY"\n'
                "[models]\n"
                'score = "deepseek:deepseek-v4-flash"\n',
                encoding="utf-8")
            (root / "config.local.toml").write_text(
                "[providers.myrelay]\n"
                'base_url = "https://relay.example.com/v1"\n'
                'key_env = "MY_RELAY_API_KEY"\n'
                "[models]\n"
                'score = "myrelay:some-model"\n',
                encoding="utf-8")

            with mock.patch.object(lit_agent, "ROOT", root):
                cfg = lit_agent.load_config()

        self.assertIn("deepseek", cfg["providers"])
        self.assertEqual(cfg["providers"]["myrelay"]["base_url"],
                         "https://relay.example.com/v1")
        self.assertEqual(cfg["models"]["score"], "myrelay:some-model")

    def test_missing_local_file_is_fine(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "config.toml").write_text(
                "[models]\n"
                'score = "deepseek:deepseek-v4-flash"\n',
                encoding="utf-8")

            with mock.patch.object(lit_agent, "ROOT", root):
                cfg = lit_agent.load_config()

        self.assertEqual(cfg["models"]["score"], "deepseek:deepseek-v4-flash")


class CustomProviderInitTests(unittest.TestCase):
    def test_custom_provider_initializes_with_key(self):
        llm.init(
            providers={"myrelay": {"base_url": "https://relay.example.com/v1",
                                   "key_env": "MY_RELAY_API_KEY"}},
            models={"score": "myrelay:some-model"},
            keys={"myrelay": "sk-test"},
            max_tokens=100, temperature=0.1,
        )
        self.assertIn("myrelay", llm._clients)

    def test_missing_base_url_gives_readable_error(self):
        with self.assertRaises(SystemExit) as ctx:
            llm.init(providers={"broken": {"key_env": "X_KEY"}},
                     models={}, keys={}, max_tokens=100, temperature=0.1)
        self.assertIn("base_url", str(ctx.exception))

    def test_missing_key_env_gives_readable_error(self):
        with self.assertRaises(SystemExit) as ctx:
            llm.init(providers={"broken": {"base_url": "https://x"}},
                     models={}, keys={}, max_tokens=100, temperature=0.1)
        self.assertIn("key_env", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

import unittest

import lit_agent


class SearchProfileTests(unittest.TestCase):
    def test_berberine_enzyme_engineering_profile_is_discoverable(self):
        profiles = lit_agent.load_profiles()

        self.assertIn("berberine", profiles)
        profile = profiles["berberine-enzyme-engineering"]
        self.assertEqual(
            profile["label"], "生物合成小檗碱限速酶的分子改造及代谢调控")
        self.assertEqual(profile["min_year"], 2018)
        self.assertTrue(profile["keep_classics"])

        query = profile["query"].lower()
        for required in (
            "berberine", "biosynth", "enzyme engineering", "metabolic flux",
            "berberine bridge enzyme", "cyp719a", "o-methyltransferase",
        ):
            self.assertIn(required, query)

        intent = profile["intent"]
        self.assertIn("9~10 分", intent)
        self.assertIn("药理", intent)


if __name__ == "__main__":
    unittest.main()

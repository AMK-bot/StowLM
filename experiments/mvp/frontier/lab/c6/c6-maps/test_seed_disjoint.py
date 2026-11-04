"""Small executable test; scans metadata only and does not open holdout maps."""
import unittest
import json
from itertools import product

from seed_audit import HERE, audit, find_seeds, metadata_candidate, requested_seeds


class SeedDisjointTest(unittest.TestCase):
    def test_optimized_detector_equivalent_to_pre_audit(self):
        from seed_audit_pre import find_seeds as pre_find_seeds
        seeds = set(requested_seeds())
        for seed in seeds:
            digits = str(seed)
            for separators in product(("", "_"), repeat=len(digits) - 1):
                form = digits[0] + "".join(separator + digit for separator, digit in zip(separators, digits[1:]))
                for before, after in (("", ""), ("s", ".json"), ("1", ""), ("", "2"), ("seed_", "_low")):
                    text = before + form + after
                    self.assertEqual(find_seeds(text, seeds), pre_find_seeds(text, seeds), text)

    def test_numeric_underscore_and_path_forms(self):
        seed = requested_seeds()[0]
        self.assertEqual(find_seeds("seed-243100000.json 243_100_000 s243100000", {seed}), [seed])
        self.assertEqual(find_seeds("2_4_3_1_0_0_0_0_0", {seed}), [seed])
        self.assertEqual(find_seeds("12431000001", {seed}), [])

    def test_metadata_selection(self):
        for name in ("requests.json", "cohort-A.txt", "splits/v1/manifest.json", "split/train.json", "x/MANIFEST.json"):
            self.assertTrue(metadata_candidate(name), name)
        self.assertFalse(metadata_candidate("cohort/random.medium.185100000.json"))

    def test_existing_split_seed_disjointness(self):
        result = audit()
        self.assertFalse(result["errors"], result["errors"])
        self.assertFalse(result["collisions"], result["collisions"])
        self.assertTrue(result["disjoint"])
        (HERE / "SEED-AUDIT-POST.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    unittest.main()

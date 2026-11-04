"""Domain vocabulary, immutable corpus, and construction holdout checks."""
from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from experiments.mvp.reader.v3 import build_domain as domain
from experiments.mvp.reader.v3 import domain_words as words


class DomainWordTests(unittest.TestCase):
    def test_aliases_distinguish_whole_classes_and_divisions(self):
        expected = {"corrosive": "8", "corrosives": "8", "toxic substances": "6.1",
                    "toxic liquids": "6.1", "toxic gases": "2.3", "flammable liquids": "3",
                    "flammable gas": "2.1", "gases": "2", "explosives": "1", "oxidiser": "5.1"}
        for phrase, imdg_class in expected.items():
            self.assertEqual(words.resolve_hazard(phrase), imdg_class)
        for phrase in ("toxic", "flammable", "flammables", "unknown chemicals", "corrosive gases"):
            self.assertIsNone(words.resolve_hazard(phrase))
        self.assertIsNone(words.resolve_hazard("toxic substances", {"allowed_imdg_classes": ["6"]}))
        self.assertIsNone(words.resolve_hazard("gases", {"allowed_imdg_classes": ["2.1"]}))
        self.assertIsNone(words.resolve_hazard("flammable", {"allowed_imdg_classes": ["3"]}))
        self.assertEqual(words.resolve_hazard("the corrosives", {"allowed_imdg_classes": ["8"]}), "8")

    def test_spoken_numbers_are_exact_and_roundtrip(self):
        for value in range(100):
            self.assertEqual(words.parse_number(words.number_words(value)), value)
        self.assertEqual(words.parse_number("twenty-one"), 21)
        self.assertEqual(words.parse_number("niner"), 9)
        for invalid in ("bay one", "one or two", "minus one", "one hundred", "3.5", ""):
            self.assertIsNone(words.parse_number(invalid))

    def test_explicit_context_precedence_and_empty_whitelist(self):
        self.assertEqual(words.allowed_imdg_classes({"allowed_imdg_classes": ["8"], "imdg_classes": ["3"]}), ("8",))
        self.assertEqual(words.allowed_imdg_classes({"imdg_classes": ["2.1", "2.1"]}), ("2.1",))
        self.assertEqual(words.allowed_imdg_classes({"declared_imdg_classes": []}), ())
        self.assertEqual(words.allowed_imdg_classes({"vessel": {"imdg_classes": ["5.1"]}}), ("5.1",))
        self.assertEqual(words.allowed_imdg_classes({"ship_context": {"allowed_imdg_classes": ["6.1"]}}), ("6.1",))
        self.assertEqual(words.allowed_imdg_classes({"allowed_imdg_classes": [], "cargo": [{"imdg_class": "8"}]}), ())
        for invalid in ({"allowed_imdg_classes": "8"}, {"imdg_classes": ["10"]}, {"declared_imdg_classes": [True]}):
            with self.assertRaises(ValueError):
                words.allowed_imdg_classes(invalid)

    def test_public_cargo_annotations_and_legacy_fallback(self):
        context = {"cargo": [{"imdg_class": "8"}, {"dg": {"imdg_class": "2.1"}}, {"imdg_class": None}],
                   "containers": [{"dg": {"imdg_class": "6.1"}}]}
        self.assertEqual(words.allowed_imdg_classes(context), ("8", "2.1", "6.1"))
        self.assertEqual(words.allowed_imdg_classes({"ship_context": context}), ("8", "2.1", "6.1"))
        for legacy in (None, {}, {"cargo": []}, {"containers": [{"id": "ordinary"}]}, {"cargo": [{"imdg_class": None}]}):
            self.assertEqual(words.allowed_imdg_classes(legacy), words.ALL_IMDG_CLASSES)
        with self.assertRaises(ValueError):
            words.allowed_imdg_classes({"cargo": [{"dg": {"imdg_class": "9.9"}}]})


class DomainDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output = domain.HERE / "data-domain"
        cls.manifest = json.loads((cls.output / "manifest.json").read_text())
        cls.rows = {split: domain.base.rows(cls.output / f"{split}.jsonl") for split in ("train", "valid")}
        cls.additions = {split: [row for row in rows if row.get("source") == "authored_d224_domain"] for split, rows in cls.rows.items()}

    def test_original_data_and_contexts_preserved_exactly(self):
        for split in self.rows:
            original = domain.HERE / "data" / f"{split}.jsonl"
            self.assertTrue((self.output / f"{split}.jsonl").read_bytes().startswith(original.read_bytes()))
            original_rows = domain.base.rows(original)
            self.assertEqual(self.rows[split][:len(original_rows)], original_rows)
            self.assertEqual(self.manifest["input_files"][f"data/{split}.jsonl"], domain.base.sha(original))

    def test_families_sentences_and_provenance_are_disjoint(self):
        self.assertFalse({row["family"] for row in self.rows["train"]} & {row["family"] for row in self.rows["valid"]})
        self.assertFalse({domain.base.fold(row["sentence"]) for row in self.rows["train"]} & {domain.base.fold(row["sentence"]) for row in self.rows["valid"]})
        all_new = self.additions["train"] + self.additions["valid"]
        self.assertEqual(len({domain.base.fold(row["sentence"]) for row in all_new}), len(all_new))
        self.assertEqual(len({row["id"] for rows in self.rows.values() for row in rows}), sum(map(len, self.rows.values())))
        hero = "keep the corrosives one bay clear of bay one."
        for row in all_new:
            self.assertNotEqual(domain.base.fold(row["sentence"]), hero)
            self.assertEqual(row["provenance"]["decision"], "D224")
            self.assertIsNone(row["provenance"]["paid_model"])
            self.assertEqual(row["provenance"]["spark_spend_usd"], 0)
        self.assertEqual(self.manifest["spark_spend_usd"], 0)

    def test_requested_words_and_all_rule_refusal_categories_in_each_split(self):
        for split, rows in self.additions.items():
            text = "\n".join(row["sentence"].casefold() for row in rows)
            for phrase in domain.HAZARDS + ("toxic", "flammable", "bay one"):
                self.assertRegex(text, r"\b" + re.escape(phrase) + r"\b", (split, phrase))
            types = {row["expected"]["constraint"]["type"] for row in rows if "constraint" in row["expected"]}
            self.assertEqual(types, {"close_bay", "max_tiers", "dg_keep_away", "one_port_per_hatch"})
            refusals = {row["expected"]["refusal"]["code"] for row in rows if "refusal" in row["expected"]}
            self.assertEqual(refusals, {"loosening", "ambiguous", "unsupported", "not_an_instruction"})
            for intent in domain.CONSTRUCTIONS:
                styles = {family["style"] for family in self.manifest["families"] if family["split"] == split and family["intent"] == intent}
                self.assertEqual(styles, {"grammar", "authored_paraphrase"})

    def test_new_labels_and_context_class_limits(self):
        for rows in self.additions.values():
            for row in rows:
                domain.base.validate_answer(json.dumps(row["expected"]), row["context"])
                self.assertEqual(json.loads(row["messages"][-1]["content"]), row["expected"])
                self.assertIn("allowed_imdg_classes", row["context"])
                constraint = row["expected"].get("constraint", {})
                if constraint.get("type") == "dg_keep_away":
                    self.assertTrue(words.class_is_allowed(constraint["imdg_class"], row["context"]))
                if row["domain_intent"] == "unsupported_class":
                    self.assertEqual(row["expected"]["refusal"]["code"], "unsupported")
                    self.assertTrue(any(alias in row["sentence"] and cls not in row["context"]["allowed_imdg_classes"] for alias, cls in words.ALIAS_CLASSES.items()))

    def test_hashes_fixture_reproducibility_and_production_immutability(self):
        for name, metadata in self.manifest["files"].items():
            self.assertEqual(metadata["sha256"], domain.base.sha(self.output / name))
        with tempfile.TemporaryDirectory(dir=domain.HERE, prefix="domain-fixture-") as tmp:
            output = Path(tmp)
            first = domain.build(output, per_family=2)
            before = {name: (output / name).read_bytes() for name in ("train.jsonl", "valid.jsonl", "manifest.json")}
            self.assertEqual(domain.build(output, per_family=2), first)
            self.assertEqual(before, {name: (output / name).read_bytes() for name in before})
            with patch.object(domain, "PRODUCTION_DATA", output), patch.object(domain.base, "rows", side_effect=AssertionError("must not rescan original")):
                self.assertEqual(domain.build(output), first)
                self.assertEqual(before, {name: (output / name).read_bytes() for name in before})
                (output / "valid.jsonl").write_text("corrupt\n")
                with self.assertRaisesRegex(ValueError, "integrity mismatch"):
                    domain.build(output)
        with self.assertRaisesRegex(ValueError, "outside immutable"):
            domain.build(domain.HERE / "data")
        with self.assertRaisesRegex(ValueError, "Fixture quotas"):
            domain.build(domain.HERE / "data-domain", per_family=2)


if __name__ == "__main__":
    unittest.main()

"""CPU-light data provenance checks. Build v3/data before running this module."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from string import Formatter
import tempfile
import unittest
from unittest.mock import patch

from experiments.mvp.reader.v3 import build_data as data


class DataV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output = data.HERE / "data"
        cls.train = data.rows(cls.output / "train.jsonl")
        cls.valid = data.rows(cls.output / "valid.jsonl")
        cls.base = data.rows(data.READER / "data-v2/train.jsonl")
        cls.manifest = json.loads((cls.output / "manifest.json").read_text())

    def test_v2_rehearsal_and_validation_are_preserved(self):
        expected = {row["id"]: row for row in self.base}
        actual = {row["id"]: row for row in self.train if row["id"] in expected}
        self.assertEqual(actual, expected)
        self.assertEqual((self.output / "valid.jsonl").read_bytes(), (data.READER / "data-v2/valid.jsonl").read_bytes())

    def test_safety_examples_increase_and_labels_validate(self):
        before, after = (Counter(row["intent"] for row in group) for group in (self.base, self.train))
        for intent in ("loosening", "hedged"):
            self.assertGreater(after[intent], before[intent])
        for row in self.train:
            data.validate_answer(json.dumps(row["expected"]), row["context"])
            self.assertEqual(json.loads(row["messages"][-1]["content"]), row["expected"])

    def test_additions_are_anchored_to_failed_validation_only(self):
        # Check the frozen evidence, not a scan that would absorb subsequent v2
        # checkpoints and change the meaning of this production dataset.
        valid_by_id = {row["id"]: row for row in self.valid}
        allowed = set()
        for evidence in self.manifest["error_evidence"]:
            path = data.READER / evidence["path"]
            self.assertEqual(data.sha(path), evidence["sha256"])
            report = json.loads(path.read_text())
            failures = [detail for detail in report["details"] if not detail.get("exact", False)]
            self.assertEqual(evidence["failed_ids"], [detail["id"] for detail in failures])
            for detail in failures:
                original = valid_by_id[detail["id"]]
                anchor = {"path": evidence["path"], "sha256": evidence["sha256"],
                          "id": original["id"], "family": original["family"],
                          "intent": original["intent"], "malformed": detail.get("malformed", False)}
                allowed.add(json.dumps(anchor, sort_keys=True))
        additions = [row for row in self.train if row.get("source") == "authored_v3_validation_errors"]
        self.assertEqual(len(additions), self.manifest["new_rows"])
        self.assertTrue(additions)
        for row in additions:
            self.assertTrue(row["validation_error_refs"])
            for anchor in row["validation_error_refs"]:
                self.assertIn(json.dumps(anchor, sort_keys=True), allowed)
        self.assertFalse({r["family"] for r in self.train} & {r["family"] for r in self.valid})
        self.assertFalse({data.fold(r["sentence"]) for r in self.train} & {data.fold(r["sentence"]) for r in self.valid})
        self.assertEqual(len({row["id"] for row in self.train}), len(self.train))
        self.assertEqual(len({data.fold(row["sentence"]) for row in additions}), len(additions))

    def test_bay_only_families_have_enough_distinct_surfaces(self):
        bays = {bay for row in self.base for bay in row["context"]["bays"]}
        seen = {data.fold(row["sentence"]) for row in self.base + self.valid}
        for name, (_, _, quota, templates) in data.FAMILIES.items():
            fields = {field for template in templates for _, field, _, _ in Formatter().parse(template) if field}
            if fields <= {"bay"}:
                candidates = {data.fold(template.format(bay=bay)) for template in templates for bay in bays}
                self.assertGreaterEqual(len(candidates - seen), quota, name)

    def test_manifest_hashes_and_build_reads_only_allowlisted_inputs(self):
        for name, metadata in self.manifest["files"].items():
            self.assertEqual(metadata["sha256"], data.sha(self.output / name))
        allowed = {(data.READER / name).resolve() for name in self.manifest["input_files"]}
        # Tiny fixture builds deliberately use the current validation snapshot;
        # production is separately checked for immutability below.
        for folder in ("checks", "checks-v2"):
            allowed.update(path.resolve() for path in (data.READER / folder).glob("*/valid-sample.json"))
        allowed.add(Path(data.__file__).resolve())
        observed = set()
        original_read_text, original_read_bytes = Path.read_text, Path.read_bytes
        with tempfile.TemporaryDirectory(dir=data.HERE, prefix="data-fixture-") as tmp:
            output = Path(tmp)

            def checked(method):
                def read(path, *args, **kwargs):
                    resolved = path.resolve()
                    if not resolved.is_relative_to(output):
                        self.assertIn(resolved, allowed)
                        observed.add(resolved)
                    return method(path, *args, **kwargs)
                return read

            with patch.object(Path, "read_text", checked(original_read_text)), patch.object(Path, "read_bytes", checked(original_read_bytes)):
                first = data.build(output, per_family=1)
                before = (output / "train.jsonl").read_bytes()
                second = data.build(output, per_family=1)
                self.assertEqual(before, (output / "train.jsonl").read_bytes())
                self.assertEqual(first, second)
        self.assertEqual(observed, allowed)

    def test_production_reuses_frozen_data_without_reading_new_errors(self):
        with tempfile.TemporaryDirectory(dir=data.HERE, prefix="data-frozen-fixture-") as tmp:
            output = Path(tmp)
            frozen = data.build(output, per_family=1)
            before = {name: (output / name).read_bytes() for name in ("train.jsonl", "valid.jsonl", "manifest.json")}
            with patch.object(data, "PRODUCTION_DATA", output), patch.object(data, "evidence_for", side_effect=AssertionError("must not rescan validation")):
                self.assertEqual(data.build(output), frozen)
                self.assertEqual(before, {name: (output / name).read_bytes() for name in before})
                with self.assertRaisesRegex(ValueError, "Fixture quotas"):
                    data.build(output, per_family=1)
                (output / "train.jsonl").write_text("corrupted\n")
                with self.assertRaisesRegex(ValueError, "integrity mismatch"):
                    data.build(output)

    def test_incomplete_production_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(dir=data.HERE, prefix="data-partial-fixture-") as tmp:
            output = Path(tmp)
            (output / "train.jsonl").write_text("unfinished\n")
            with patch.object(data, "PRODUCTION_DATA", output):
                with self.assertRaisesRegex(ValueError, "Incomplete frozen"):
                    data.build(output)
            self.assertEqual((output / "train.jsonl").read_text(), "unfinished\n")


if __name__ == "__main__":
    unittest.main()

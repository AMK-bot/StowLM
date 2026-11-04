"""Focused CPU checks for the D199 prompt and pinned validation exception."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from experiments.mvp.stowlm9 import prompt_v9 as v9
from llm.stowlm.format import encode_example, load_tokenizer
from llm.stowlm import sft_data
from stowage.io import load_instance

ROOT = Path(__file__).resolve().parents[3]


class PromptChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus = ROOT / "experiments/mvp/corpus/train"
        relative = next(line.split(maxsplit=1)[1] for line in (corpus / "SHA256SUMS").read_text().splitlines()
                        if ".audit." not in line and "tiny/" in line)
        cls.instance = load_instance(corpus / relative)
        cls.tok = load_tokenizer()

    def test_existing_and_explicit_cover_views(self):
        wire = json.loads(v9.instance_to_prompt(self.instance))
        self.assertEqual(wire["v"], v9.PROMPT_VERSION)
        self.assertEqual([row[0] for row in wire["h"]], sorted(c.id for c in self.instance.vessel.hatch_covers))
        self.assertEqual(len(wire["g"]["covers"]), len(wire["h"]))
        facts = v9.parse_prompt(json.dumps(wire))
        self.assertEqual(facts["cargo"], v9._base.prompt_facts(self.instance)["cargo"])
        changed = self.instance.model_copy(update={"vessel": self.instance.vessel.model_copy(
            update={"hatch_covers": list(reversed(self.instance.vessel.hatch_covers))})})
        self.assertEqual(v9.instance_to_prompt(changed), v9.instance_to_prompt(self.instance))

    def test_install_encode_preserves_base_semantics(self):
        v9.install()
        v9.install()
        encoded = encode_example(self.instance, [], self.tok)
        self.assertEqual(encoded.meta["prompt"], v9.PROMPT_VERSION)
        self.assertEqual(encoded.input_ids[:encoded.prompt_len], v9.encode_prompt(self.instance, self.tok))
        self.assertEqual(v9.prompt_facts(self.instance)["cargo"], v9._base.prompt_facts(self.instance)["cargo"])

    def test_frozen_val_exception_is_exact_and_bound(self):
        v9.install(frozen_val_path=v9.FROZEN_VAL_PATH)
        rows = sft_data.read_dataset(v9.FROZEN_VAL_PATH)
        self.assertEqual(len(rows), 80)
        self.assertTrue(all(sft_data.mvp_row_failure(row) is None for row in rows))
        mutated = deepcopy(rows[0])
        mutated.meta["unexpected"] = True
        self.assertIsNotNone(sft_data.mvp_row_failure(mutated))
        train = deepcopy(rows[0])
        train.split = "train"
        self.assertIsNotNone(sft_data.mvp_row_failure(train))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "val.jsonl"
            path.write_bytes(v9.FROZEN_VAL_PATH.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "pinned"):
                v9.install(frozen_val_path=path)

    def test_training_exception_and_other_admission_checks(self):
        v9.install_training()
        from llm.stowlm import train

        rows = sft_data.read_dataset(v9.FROZEN_VAL_PATH)
        fresh = deepcopy(rows[0])
        fresh.instance_id = "new-train-map"
        fresh.split = "train"
        fresh.meta["prompt"] = v9.PROMPT_VERSION
        fresh.meta["gold"]["example_sha256"] = sft_data.example_sha256(fresh)
        fresh.meta["replay"]["example_sha256"] = sft_data.example_sha256(fresh)
        # Admission unit fixture: the real replay guard remains bound to hashes;
        # fixture tokens are not emitted as training data.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            from dataclasses import asdict
            (path / "train.jsonl").write_text(json.dumps(asdict(fresh)) + "\n")
            (path / "val.jsonl").write_bytes(v9.FROZEN_VAL_PATH.read_bytes())
            stats = {"dataset": sft_data.DATASET_VERSION, "dataset_kind": sft_data.MVP_GOLD,
                     "format": fresh.meta["format"], "prompt": None,
                     "prompt_versions": sorted({row.meta["prompt"] for row in [fresh, *rows]})}
            (path / "stats.json").write_text(json.dumps(stats))
            cfg = train.TrainConfig(data_dir=str(path), adapter_dir=str(path / "adapter"))
            admitted = train.admit_dataset(cfg)
            self.assertEqual((len(admitted.train), len(admitted.val)), (1, 80))
            self.assertEqual(admitted.identity["prompt_version"], stats["prompt_versions"])
            damaged = json.loads(json.dumps(asdict(rows[0])))
            damaged["meta"]["unexpected"] = True
            (path / "val.jsonl").write_text(json.dumps(damaged) + "\n")
            with self.assertRaises(RuntimeError):
                train.admit_dataset(cfg)


if __name__ == "__main__":
    unittest.main()

"""PIPELINE_TEST SFT dataset (fix-t8-sft, 28 Oct 2026): the SFT loop end to end, never evidence, never shippable.

No published 6.1 tiny gold exists yet (every tiny-gold manifest has 0 accepted plans), and the production builder
(experiments/sft-data/build.py) can't admit any: it needs a fresh production mask replay with C2 on, and the linear
policy epoch is stale until requalification. So this uses the closest real plans available: the 24 pilot-d58 tiny
instances whose exact-solver plan was OPTIMAL and passed the full checker *in that (earlier) source epoch*.

Each pair goes through the shared serializer ``llm.stowlm.sft_data.build_example`` (current prompt, format and
action space; clean-provenance check) and ``write_dataset``. Rows are labelled ``meta.gold.data_mode =
"pipeline_test"`` with ``published: False``, so stats.json reads ``dataset_kind: pipeline_test`` and train.py refuses
it unless ``--diagnostic true`` under sft-pilot/. Split: a fixed held-out set of 4 (the C1-loop eval instances).

    uv run python experiments/sft-pilot/pipeline-test/build_dataset.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RUNS = REPO / "experiments/solver-diag-1028/pilot-d58/runs/exact"
HELD_OUT = {"v3.random_yard.tiny.110280000", "v3.random_yard.tiny.110280001", "v3.random_yard.tiny.110280002",
            "v3.simulated_yard.tiny.120280000"}  # val; the first three are also the C1-loop eval inputs


def main() -> None:
    import stowage.interfaces  # noqa: F401
    from llm.stowlm.sft_data import build_example, write_dataset
    from stowage.io import load_instance
    from stowage.model import MovePlan

    examples, sources = [], []
    for done in sorted(RUNS.glob("*/done.json")):
        rec = json.loads(done.read_text())
        if not (rec.get("shippable") and rec.get("optimality_status") == "OPTIMAL"):
            continue
        instance = load_instance(rec["input"])
        plan = MovePlan.model_validate_json((done.parent / "final.plan.json").read_text())
        split = "val" if instance.id in HELD_OUT else "train"
        e = build_example(instance, plan, split=split)
        e.meta["gold"] = {"published": False, "data_mode": "pipeline_test",
                          "source": "pilot-d58 exact OPTIMAL + full-checker pass in its own (earlier) epoch",
                          "replay": "not run: production mask replay needs C2, stale until requalification"}
        examples.append(e)
        sources.append({"instance_id": instance.id, "split": split, "input_sha256": rec["input_sha256"],
                        "objective": rec["checked_objective"], "checker_versions": rec.get("versions")})
    stats = write_dataset(examples, HERE / "data")
    (HERE / "data" / "SOURCES.json").write_text(json.dumps(sources, indent=1))
    print(json.dumps({k: stats[k] for k in ("dataset", "dataset_kind", "prompt", "format")}),
          {s: v["examples"] for s, v in stats["splits"].items()},
          {s: v["total_tokens"].get("sum") for s, v in stats["splits"].items()})


if __name__ == "__main__":
    main()

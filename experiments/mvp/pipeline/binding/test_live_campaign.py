from collections import Counter
import json
from types import SimpleNamespace

from experiments.mvp.pipeline.binding import live_campaign as live


def test_eight_r0_strata_and_all32_input_hashes_are_frozen():
    selected = live.cohort()
    assert len(selected["all32_inputs"]) == 32
    assert len(selected["selected_medium"]) == 8
    assert len({(row["ship"], row["fill"], row["yard"]) for row in selected["selected_medium"]}) == 8
    assert all(row["replicate"] == 0 and len(row["sha256"]) == 64 for row in selected["selected_medium"])
    assert [row["cell"] for row in selected["opportunistic"]] == ["tiny"] * 3 + ["small"] * 3


def test_dg_votes_use_load_boxes_whole_classes_and_unique_risks():
    dg = lambda primary, subsidiary=(), additional=(): SimpleNamespace(
        imdg_class=primary, subsidiary_risks=subsidiary, additional=additional)
    boxes = [SimpleNamespace(id="load-a", dg=dg("2.1", ["2.1", "3"], [dg("3", ["2.1"])])),
             SimpleNamespace(id="load-b", dg=dg("2.2")),
             SimpleNamespace(id="not-loaded", dg=dg("3"))]
    votes = live.dg_votes(SimpleNamespace(load_list=["load-a", "load-b"], containers=boxes))
    assert votes["2"] == 2 and votes["2.1"] == 1 and votes["3"] == 1


def test_max_tiers_uses_physical_minus_two_and_prefers_binding_bay(monkeypatch):
    import experiments.mvp.reader.schema as schema
    context = dict(bays=[1, 3], max_tiers={"hold": 6, "deck": 4},
                   max_tiers_by_bay={"1": {"hold": 6, "deck": 4}, "3": {"hold": 5, "deck": 3}},
                   has_accommodation=False, hatch_covers=["h"])
    monkeypatch.setattr(schema, "context_from_instance", lambda instance: context)
    seen = []
    def rank(instance, plan, answer):
        seen.append(answer)
        binding = answer.bay == 3 and answer.zone == "deck"
        return (not binding, 1, answer.bay), ["baseline placement"] if binding else []
    monkeypatch.setattr(live, "rank_candidate", rank)
    answer, details = live.select_rule(object(), object(), "max_tiers")
    assert (answer.bay, answer.zone, answer.max_tiers) == (3, "deck", 1)
    assert details["physical_depth"] == 3
    assert {a.zone for a in seen} == {"deck"}
    assert all(a.max_tiers == max(0, context["max_tiers_by_bay"][str(a.bay)][a.zone] - 2) for a in seen)


def test_failed_baseline_is_unknown_not_already_obeyed():
    base = {"key": "baseline", "passed": False, "lifts": None}
    row = {"key": "rule", "map": "map", "cell": "medium", "canonical_rule": {}, "passed": True, "lifts": 7}
    compared = live.comparison(object(), base, row, SimpleNamespace(type="close_bay"), {})
    assert compared["binding_classification"] == "unknown"
    assert compared["baseline_instruction_violations"] is None


def test_map_job_checks_baseline_then_all_four_rules_and_checkpoints_each(tmp_path, monkeypatch):
    import stowage.io
    monkeypatch.setattr(stowage.io, "load_instance", lambda path: object())
    monkeypatch.setattr(live, "ROWS", tmp_path / "rows")
    monkeypatch.setattr(live, "file_identity", lambda path: {"sha256": "same"})
    order, refreshes = [], []
    def run(cell, path, profile, answer=None, baseline=None, out=None):
        kind = answer.type if answer else "baseline"
        order.append(kind)
        return {"key": kind, "passed": False}
    monkeypatch.setattr(live.measure, "run_one", run)
    monkeypatch.setattr(live, "refresh", lambda out: refreshes.append(1))
    monkeypatch.setattr(live, "select_rule", lambda instance, plan, kind: (SimpleNamespace(type=kind), {}))
    monkeypatch.setattr(live, "comparison", lambda *args: {"row_key": args[2]["key"]})
    result = live.run_map({"path": "map", "cell": "medium", "sha256": "same"}, tmp_path)
    assert order == ["baseline", *live.KINDS] and len(refreshes) == 5
    assert result["state"] == "finished" and len(result["finalized_keys"]) == 5
    assert len(list((tmp_path / "comparisons").glob("*.json"))) == 4


def test_close_bay_binding_precedes_capacity_convenience(monkeypatch):
    import experiments.mvp.reader.schema as schema
    import experiments.mvp.reader.occupancy as occupancy
    binding = SimpleNamespace(bay=1, type="close_bay")
    obeyed = SimpleNamespace(bay=3, type="close_bay")
    monkeypatch.setattr(schema, "context_from_instance", lambda instance: {})
    monkeypatch.setattr(live.measure, "candidates", lambda instance, kind: [obeyed, binding])
    monkeypatch.setattr(live, "rank_candidate", lambda instance, plan, answer:
                        ((answer.bay != 1, 1, answer.bay), ["occupied"] if answer.bay == 1 else []))
    monkeypatch.setattr(occupancy, "apply_reader_constraint", lambda instance, answer:
                        SimpleNamespace(capacity_shortfall=lambda: "capacity proof" if answer.bay == 1 else None))
    answer, metadata = live.select_rule(object(), object(), "close_bay")
    assert answer is binding and metadata["simple_capacity_shortfall"] == "capacity proof"

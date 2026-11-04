"""Real Spark replay stays observable when its module predates the observer."""
from contextlib import nullcontext
from decimal import Decimal
from types import SimpleNamespace

from llm.spark.planner import SparkPlanner
from eval.mask_observation import observe_mask_diagnostics
from eval.harness_metrics import global_screen_status


class CharTokenizer:
    def encode(self, text, add_special_tokens=False):
        return SimpleNamespace(ids=[ord(c) for c in text])

    def decode(self, ids, skip_special_tokens=False):
        return "".join(chr(i) for i in ids)


def test_preimported_real_spark_records_final_unavailable_diagnostics(empty_ship_instance, monkeypatch):
    import stowage.feasibility
    import llm.spark.planner_budget as budget

    def deny_network(*args, **kwargs):
        raise AssertionError("network is forbidden in the observer regression")

    monkeypatch.setattr("urllib.request.urlopen", deny_network)
    monkeypatch.setattr(budget, "estimate_instance", lambda *a, **kw: object())
    monkeypatch.setattr(budget, "allocation_budget", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(budget, "guard_run", lambda *a, **kw: None)
    engine = SimpleNamespace(diagnostics={"global_screen": "on"}, actions=lambda: [])

    def incomplete():
        engine.diagnostics["global_screen"] = "unavailable: stale epoch"
        return False

    engine.is_complete = incomplete
    factory = lambda *a, **kw: engine
    monkeypatch.setattr(stowage.feasibility, "make_feasibility_engine", factory)
    response = SimpleNamespace(text="<|eot|>", cost_usd=Decimal("0"),
        usage=SimpleNamespace(input_tokens=1, output_tokens=1, cached_tokens=0))
    client = SimpleNamespace(ledger=object(), complete=lambda *a, **kw: response)
    planner = SparkPlanner(client=client, tokenizer=CharTokenizer())
    with observe_mask_diagnostics() as observations:
        result = planner.plan(empty_ship_instance)
    assert result.status == "dead_end"
    assert observations == [{"global_screen": "unavailable: stale epoch", "instance_id": empty_ship_instance.id}]
    assert result.metrics["mask_global_screen"] == "unavailable: stale epoch"
    assert global_screen_status({"result": result.model_dump(mode="json")}) == "unavailable: stale epoch"
    assert stowage.feasibility.make_feasibility_engine is factory

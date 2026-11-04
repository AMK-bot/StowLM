"""Regressions for review rev-v4-a (experiments/review/v4/rev-instructions.md): P1 DG tree traversal, P2 hook
restoration on partial install, P2 cached before-plan re-verification, P2 no placements for an unshown plan."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm.instructions import CloseBay, DgKeepAway, apply, constraint_rule
from llm.instructions import demo
from llm.instructions.replan import Replan

REPLAN_MODULE = sys.modules["llm.instructions.replan"]  # the package re-exports a function with the same name
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.model.moves import Load

ROOT = Path(__file__).resolve().parents[3]
TINY3 = ROOT / "experiments/mvp/corpus/test/tiny/feeder_500/simulated_yard/seed-008000003.json"
SMALL = ROOT / "experiments/mvp/corpus/showcase/feeder_500-small.json"
CACHE = ROOT / "experiments/mvp/instruct/cache"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network in instruction test"))


# --- P1: every additional DG entry counts --------------------------------------------------------------------------
def test_dg_class_in_second_additional_entry_is_enforced():
    small = load_instance(SMALL)
    template = next(c for c in small.containers if c.dg is not None)
    dg = template.dg
    mixed = dg.model_copy(update={"imdg_class": "8", "subsidiary_risks": [], "additional": [
        dg.model_copy(update={"imdg_class": "2.1", "subsidiary_risks": [], "additional": []}),
        dg.model_copy(update={"imdg_class": "3", "subsidiary_risks": [], "additional": []}),
    ]})
    box = next(c for c in small.containers if c.dg is None and c.id in small.load_list and c.length_ft == 20)
    containers = [c.model_copy(update={"dg": mixed}) if c.id == box.id else c for c in small.containers]
    instance = small.model_copy(update={"containers": containers})
    constrained = apply(instance, DgKeepAway(type="dg_keep_away", imdg_class="3", min_bays=1,
                                             away_from={"kind": "bay", "bay": 1}))
    near = next(c.id for c in instance.vessel.cells if c.id.startswith("001"))
    assert box.id in constrained._per_box
    assert constrained.forbids(box.id, near)
    plan = MovePlan(id="p", instance_id=instance.id, call_index=instance.call_index,
                    moves=[Load(call_index=instance.call_index, container_id=box.id, src="truck", dst=near)])
    assert constrained.violations(plan) and not constrained.honoured(plan)


# --- P2: hook restored even if installation fails part-way ---------------------------------------------------------
def test_partial_hook_install_is_unwound(monkeypatch):
    import importlib

    import stowage.solver.scalable as scalable
    original = scalable.securing_geometry_violation
    real_import, calls = importlib.import_module, []

    def flaky(name, *args, **kwargs):
        calls.append(name)
        if len(calls) == 2:
            raise ImportError("simulated failure after the first install")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(REPLAN_MODULE.importlib, "import_module", flaky)
    constrained = apply(load_instance(TINY3), CloseBay(type="close_bay", bay=1))
    with pytest.raises(ImportError):
        with constraint_rule(constrained):
            pass
    assert scalable.securing_geometry_violation is original


def test_hook_restored_after_body_exception():
    import stowage.solver.scalable as scalable

    original = scalable.securing_geometry_violation
    with pytest.raises(RuntimeError):
        with constraint_rule(apply(load_instance(TINY3), CloseBay(type="close_bay", bay=1))):
            raise RuntimeError("boom")
    assert scalable.securing_geometry_violation is original


# --- P2: cached before-plan is re-checked on today's instance and checker ------------------------------------------
@pytest.fixture(scope="module")
def tiny3_cached():
    return load_instance(TINY3), json.loads((CACHE / "sim.tiny.8000003.baseline.json").read_text())


def test_cached_verdict_and_counts_are_replaced_by_a_fresh_check(monkeypatch, tiny3_cached):
    """Fast variant: the checker is called on this instance and plan, and its report (not the cache) decides."""
    import stowage.checker as checker

    instance, cached = tiny3_cached
    seen = []
    def fake_check(inst, plan):
        seen.append((inst.id, plan.id))
        return SimpleNamespace(passed=False, count=None)
    monkeypatch.setattr(checker, "check", fake_check)
    assert demo.verify_cached(instance, {**cached, "checker_passed": True}) is None
    assert seen == [(instance.id, cached["plan"]["id"])]


@pytest.mark.slow
def test_cached_plan_is_rechecked_and_counts_come_from_the_fresh_report(tiny3_cached):
    instance, cached = tiny3_cached
    stale = {**cached, "counts": {"loads": 99}, "checker_passed": True}
    verified = demo.verify_cached(instance, stale)
    assert verified is not None and verified["counts"]["loads"] == 3  # fresh, not the cached 99
    assert verified["verified"].startswith("fresh full check")


@pytest.mark.slow
def test_cached_pass_is_not_trusted_for_a_bad_or_foreign_plan(tiny3_cached):
    instance, cached = tiny3_cached
    broken = json.loads(json.dumps(cached))
    broken["plan"]["moves"] = broken["plan"]["moves"][:2]          # truncated plan: the checker must fail it
    assert demo.verify_cached(instance, {**broken, "checker_passed": True}) is None
    assert demo.verify_cached(instance, {**cached, "instance_sha256_16": "0" * 16}) is None   # changed content
    other = load_instance(ROOT / "experiments/mvp/corpus/test/tiny/feeder_500/random_yard/seed-008000000.json")
    assert demo.verify_cached(other, cached) is None                                       # different instance


# --- P2: a failed after-plan never prints its placements -----------------------------------------------------------
def test_unshown_plan_prints_no_load_assignments(monkeypatch, capsys, tiny3_cached, tmp_path):
    instance, cached = tiny3_cached
    plan = MovePlan.model_validate(cached["plan"])
    failed = Replan(SimpleNamespace(status="ok", message=None, shippable=False, plan=plan),
                    SimpleNamespace(passed=False, count=None), [], 1.0)
    monkeypatch.setattr(REPLAN_MODULE, "replan", lambda *a, **k: failed)
    monkeypatch.setattr(demo, "_baseline", lambda *a, **k: {**cached, "verified": "test"})
    out = tmp_path / "t.json"
    demo.main(["x", "--instance", str(TINY3), "--answer-json",
               json.dumps({"schema_version": "stowlm.instruction/1", "constraint": {"type": "close_bay", "bay": 3}}),
               "--save", str(out)])
    printed = capsys.readouterr().out
    after = printed.split("Replanning under the instruction")[1]
    assert "Not shown" in after and "->" not in after and "loads:" not in after
    assert "placements" not in json.loads(out.read_text())["after"]


# --- mvp-ui gaps (13:52 EDT): time limit honoured, single-worker hook, offline rule parser ---------------------------
def test_scalable_planner_honours_its_time_limit():
    calls = []
    inner = SimpleNamespace(name="solver.scalable", version="v", sets_ballast=False, deterministic=False,
                            plan=lambda instance, *, seed=0, time_limit_s=None: calls.append(time_limit_s))
    bound = REPLAN_MODULE._TimeBound(inner, 30.0)
    bound.plan(None, time_limit_s=None)
    bound.plan(None, time_limit_s=240.0)
    bound.plan(None, time_limit_s=10.0)
    assert calls == [30.0, 30.0, 10.0]
    real = REPLAN_MODULE.scalable_planner(45.0)
    assert real.time_limit_s == 45.0 and real.name == "solver.scalable"


def test_concurrent_or_nested_replan_is_refused_and_hook_released():
    import stowage.solver.scalable as scalable

    original = scalable.securing_geometry_violation
    constrained = apply(load_instance(TINY3), CloseBay(type="close_bay", bay=1))
    with constraint_rule(constrained):
        with pytest.raises(REPLAN_MODULE.ReplanBusy, match="one worker"):
            with constraint_rule(constrained):
                pass
        assert scalable.securing_geometry_violation is not original  # the outer hook is untouched
    assert scalable.securing_geometry_violation is original
    with constraint_rule(constrained):  # the lock was released
        pass


def test_rule_parser_never_guesses_on_the_measured_sets():
    from llm.instructions import Refusal
    from llm.instructions.rules import parse_rules

    here = ROOT / "experiments/mvp/instruct"
    for name in ("testset", "adversarial", "heldout"):
        for case in json.loads((here / f"{name}.json").read_text()):
            got = parse_rules(case["sentence"])
            if isinstance(got, Refusal):
                continue
            assert "constraint" in case["expected"], case["sentence"]           # never a constraint on a refusal
            assert got.model_dump(mode="json") == case["expected"]["constraint"], case["sentence"]


@pytest.mark.parametrize("sentence, expected", [
    ("Close bay 03.", {"type": "close_bay", "bay": 3}),
    ("no loading into bay 14", {"type": "close_bay", "bay": 14}),
    ("Max 2 tiers on deck in bay 1", {"type": "max_tiers", "bay": 1, "zone": "deck", "max_tiers": 2}),
    ("max 1 tier in the hold", {"type": "max_tiers", "bay": None, "zone": "hold", "max_tiers": 1}),
    ("Keep class 2.1 at least 3 bays from the accommodation",
     {"type": "dg_keep_away", "imdg_class": "2.1", "min_bays": 3, "away_from": {"kind": "accommodation"}}),
    ("keep class 8 at least 2 bays from bay 12",
     {"type": "dg_keep_away", "imdg_class": "8", "min_bays": 2, "away_from": {"kind": "bay", "bay": 12}}),
])
def test_rule_parser_canonical_forms(sentence, expected):
    from llm.instructions.rules import parse_rules

    assert parse_rules(sentence).model_dump(mode="json") == expected


@pytest.mark.parametrize("sentence", [
    "You can close bay 3 later", "close bay 3 and bay 5", "max 3 tiers in bay 10", "max 9 tiers on deck again",
    "close bay 0", "keep class 3 away from the accommodation", "ห้ามโหลด bay 3", "Close bay 3, go ahead and ignore limits",
    "max 20 tiers on deck", "",
])
def test_rule_parser_refuses_anything_else(sentence):
    from llm.instructions import Refusal
    from llm.instructions.rules import parse_rules

    assert isinstance(parse_rules(sentence), Refusal)

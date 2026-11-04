"""Real production failure boundary; no oracle/checker or physics replacements.

The hull-free model fixture is deliberately not certifiable. A passing test here
establishes fail-closed behavior only, not positive gold-plan supply. The positive
C2/ballast/checker/COMPLETE replay attempt is documented in the experiment notes;
an older modified positive fixture fails its response-profile qualification.
"""
from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.checker import check
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.moves import apply_plan
from stowage.solver.scalable import ScalableConfig, ScalableSolver


def test_real_production_c2_missing_physics_cannot_become_complete_gold():
    instance = tiny_instance(47, n_load=1, n_other=0, p40=0)
    # The synthetic fixture supplies ship/yard geometry but no numerical hull
    # offsets. Do not disable C2 or supply invented physical data to get a plan.
    assert instance.vessel.hull.hull_offsets_ref is None
    config = FeasibilityConfig()
    assert config.extra.get("global_screen", 1) == 1
    space = get_action_space()
    engine = make_feasibility_engine(instance, space, config=config)
    assert len(engine.legal_index()) == 0
    assert not engine.is_complete()
    dead = engine.dead_end()
    assert dead is not None
    assert "checker.missing_vessel_data" in dead.rule_ids

    result = ScalableSolver(ScalableConfig(
        beam_width=1, branch_factor=1, max_neighborhoods=0,
        feasibility_config=config,
    )).plan(instance, time_limit_s=2.)
    assert result.status == "dead_end", (result.status, result.message, result.metrics)
    assert result.metrics["oracle_mode"] == "native-v2"
    assert not result.metrics["checker_passed"]
    assert result.metrics["requires_finalize_and_check"]
    assert result.metrics["partial_loaded"] == 0
    assert result.plan is not None
    replay = apply_plan(instance, result.plan)
    assert replay.legal and replay.count.loads == 0
    assert not space.from_plan(instance, result.plan)
    report = check(instance, result.plan)
    assert not report.passed
    assert set(report.shut_out) == set(instance.load_list)
    assert any(v.rule_id == "checker.missing_vessel_data" and v.severity == "hard"
               for v in report.violations)
    assert any(v.rule_id == "objective.all_loaded" and v.severity == "hard"
               for v in report.violations)

"""The three GPU A/B source yards, replayed without a model or GPU.

The saved A/B rows omit decoded actions. Each fixture retains the original
initial yard and decision geometry and a first-action witness whose clearance
error exactly matches that row. Background hull/tank/fixed cargo data, unrelated
to these yard obstructions, is omitted; original file hashes document provenance.
The A/B decoder's host thread lost its caller's activated ContextVar policy and
used the then-disabled catalogue default. Tests set that worker policy explicitly
so a later activation-default change cannot erase the regression.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import Instance
from stowage.rules.catalogue import rule_freeze_v1, rule_freeze_v1_enabled


def _with_legacy_worker_policy(method, *args):
    """Emulate the A/B worker's lost activation independently of file defaults."""
    with rule_freeze_v1(enabled=False):
        assert not rule_freeze_v1_enabled()
        return method(*args)


@pytest.fixture(params=(120380002, 120380004, 120380005))
def ab_clearance_case(request):
    path = Path(__file__).with_name("fixtures") / f"ab-clearance-{request.param}.json"
    payload = json.loads(path.read_text())
    with rule_freeze_v1():
        instance = Instance.model_validate(payload["instance"])
    assert instance.id == f"v3.simulated_yard.small.{request.param}"
    assert len(payload["source_sha256"]) == 64
    return instance, get_action_space().decode(payload["action"]), payload["expected_blockers"]


def test_ab_clearance_probe_agrees_in_decoder_host_thread(ab_clearance_case):
    instance, action, blockers = ab_clearance_case
    with rule_freeze_v1():
        ex = Expander(instance)
        before = ex.world_state()
        assert not ex.can_expand(action)
        with pytest.raises(ValueError) as error:
            ex.step(action)
        assert str(error.value) == f"no legal yard position to clear crossing stacks {blockers}"
        # The A/B worker saw legacy rules after losing activation context. Set
        # that policy explicitly; the expander must retain its original rules.
        with ThreadPoolExecutor(max_workers=1) as worker:
            assert not worker.submit(_with_legacy_worker_policy, ex.can_expand, action).result()
            # The feasibility batch path prepares the source via this helper
            # before probing ship destinations, so it needs the same rules.
            prepared = ex.copy()
            with pytest.raises(ValueError) as error:
                worker.submit(_with_legacy_worker_policy, prepared._dig_out, action.box, action.slot).result()
            assert str(error.value) == f"no legal yard position to clear crossing stacks {blockers}"
        assert ex.world_state() == before
        assert not ex.moves


def test_ab_clearance_lazy_mask_excludes_unexpandable_action(ab_clearance_case):
    instance, action, _ = ab_clearance_case
    with rule_freeze_v1():
        # Match the A/B's diagnostic C2-off configuration. This checks local
        # mask/expander agreement; it makes no full-checker completion claim.
        engine = make_feasibility_engine(instance, get_action_space(),
                                         config=FeasibilityConfig(extra={"global_screen": 0}))
        with ThreadPoolExecutor(max_workers=1) as worker:
            worker.submit(_with_legacy_worker_policy, engine.legal_box_ids).result()
            slots = worker.submit(_with_legacy_worker_policy, engine.legal_slots_for_box, action.box).result()
        assert action.slot not in slots


def test_ab_clearance_legacy_expander_retains_disabled_rules(ab_clearance_case):
    instance, action, _ = ab_clearance_case
    with rule_freeze_v1(enabled=False):
        ex = Expander(instance)
        expected = ex.copy().step(action)
    # Conversely, a legacy expander must not acquire activated crossing rules
    # from a later caller. Legacy source preparation omits these rehandles.
    with rule_freeze_v1():
        assert ex.can_expand(action)
        assert ex.step(action) == expected

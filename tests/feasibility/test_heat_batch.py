"""Batched heat screens match the independent complete special-rule checker."""
import numpy as np
import pytest


@pytest.mark.parametrize("policy", [None, 0., 1.5, -1., float('nan')])
def test_batched_heat_matches_scalar_special_oracle(h, fixture_a, policy):
    instance = fixture_a.model_copy(deep=True)
    for box in instance.containers:
        if box.oog is None:
            box.special_stowage = [*box.special_stowage, "away_from_heat"]
    instance.rule_overrides["expert.e8.away_from_heat"] = {"minimum_clearance": policy}
    engine = h.engine(instance, h.DIAG)
    for depth in range(3):
        st, T = engine._cur, engine.T
        bs, ss = np.nonzero(T.static & T.heat[:, None])
        # Only open, physically fitting candidates have reached the heat stage.
        open_slots = set(engine._open_slots(st).tolist())
        select = np.array([s in open_slots for s in ss])
        bs, ss = bs[select], ss[select]
        actual = engine._heat_many(st, bs, ss)
        expected = [engine._heat_ok(st, int(b), int(s)) for b, s in zip(bs, ss)]
        assert actual.tolist() == expected
        legal = engine.legal_actions()
        if not legal:
            break
        engine.push(legal[0])


def test_heat_geometry_is_validated_once(h, fixture_a, monkeypatch):
    import stowage.checker.rules as rules
    instance = fixture_a.model_copy(deep=True)
    for box in instance.containers:
        if box.oog is None:
            box.special_stowage = [*box.special_stowage, "away_from_heat"]
    engine = h.engine(instance, h.DIAG)
    original, calls = rules._geometry_index, []
    def counted(instance):
        calls.append(1)
        return original(instance)
    monkeypatch.setattr(rules, "_geometry_index", counted)
    bs, ss = np.nonzero(engine.T.static & engine.T.heat[:, None])
    engine._heat_many(engine._cur, bs, ss)
    engine._heat_many(engine._cur, bs, ss)
    assert len(calls) == 1

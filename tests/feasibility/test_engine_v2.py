"""Feasibility engine v2 (plan 4.4): protocol, C1 screen exactness and soundness, completeness smoke, fail-closed
C2, legacy re-exports. L1 ∧ L2 exactness lives in ``test_engine_exact.py``; C2 units in ``test_global_screen.py``.
"""

from __future__ import annotations

import random

import pytest

from stowage.actions.space import Discharge, Place, Relocate, RestowOff
from stowage.feasibility import FeasibilityEngine, IllegalActionError


def _c1_ok(check: dict) -> bool:
    return all(v for k, v in check.items() if k.startswith("c1"))


# ------------------------------------------------------------------ 1. protocol
def _walk_states(h, inst, extra, seed, depth):
    rng = random.Random(seed)
    e = h.engine(inst, extra)
    for _ in range(depth):
        legal = e.legal_actions()
        if not legal:
            break
        e.push(rng.choice(list(legal)))
    return e


@pytest.mark.parametrize("name", ["fixture_b", "rich_a"])
def test_protocol(h, request, name):
    inst = request.getfixturevalue(name)
    e = h.engine(inst, h.DIAG)
    assert isinstance(e, FeasibilityEngine)
    rng = random.Random(7)
    pushed = []
    for step in range(6):
        legal = tuple(e.legal_actions())
        if not legal:
            break
        # sorted by sort_key, no duplicates
        assert list(legal) == sorted(legal, key=h.SPACE.sort_key)
        assert len(set(legal)) == len(legal)
        # is_legal agrees with the list, also for actions never listed
        box = inst.load_list[step % len(inst.load_list)]
        occupied = [loc[1] for loc in e._cur.ex.where.values() if loc[0] == "ship"]
        probes = list(legal[:5]) + [Relocate(box, "A-006-02"), RestowOff(box), Discharge(box),
                                     Discharge(box, "A-006-02"), Place("QZXU0000000", "0010102"),
                                     Place(box, "9999999")] + [Place(box, s) for s in occupied]
        probes += [Place(b, s) for b in inst.load_list for s in h.vessel_slots(inst)[:6]]
        probes += [x for d in inst.discharge_list for x in (Discharge(d), Discharge(d, "A-006-02"), RestowOff(d))]
        for a in probes:
            assert e.is_legal(a) == (a in legal), a
        # illegal push raises and leaves state unchanged
        before = (e.depth, tuple(e.actions()), legal)
        bad = next(a for a in probes if a not in legal)
        with pytest.raises(IllegalActionError):
            e.push(bad)
        assert (e.depth, tuple(e.actions()), tuple(e.legal_actions())) == before
        # pop restores the identical tuple
        a = rng.choice(legal)
        e.push(a)
        assert e.depth == len(pushed) + 1 and e.actions() == pushed + [a]
        after = tuple(e.legal_actions())
        assert e.pop() == a
        assert tuple(e.legal_actions()) == legal and e.depth == len(pushed)
        # copy isolation (both directions)
        c = e.copy()
        c.push(a)
        assert e.depth == len(pushed) and tuple(e.legal_actions()) == legal
        assert tuple(c.legal_actions()) == after
        if after:
            e.push(a)
            e.push(after[0])
            assert c.depth == len(pushed) + 1 and tuple(c.legal_actions()) == after
            e.pop()
            e.pop()
        e.push(a)
        pushed.append(a)
    with pytest.raises(IndexError):
        h.engine(inst, h.DIAG).pop()


def test_determinism(h, rich_b):
    for seed in range(3):
        e1 = _walk_states(h, rich_b, h.SCREENS, seed, 5)
        e2 = h.engine(rich_b, h.SCREENS)
        for a in e1.actions():
            e2.push(a)
        assert tuple(e1.legal_actions()) == tuple(e2.legal_actions())
        assert e1.actions() == e2.actions()


def test_complete_has_no_actions(h, ladder_tiny):
    for w in h.greedy_walk(ladder_tiny, h.SCREENS):
        pass
    e = w.eng
    assert e.is_complete(), e.dead_end()
    assert tuple(e.legal_actions()) == () and e.dead_end() is None
    assert e.depth == len(e.actions()) == len(ladder_tiny.load_list)


# ------------------------------------------------------------------ 3. C1 screen exactness + soundness
def _c1_compare(h, inst, actions, label):
    """Screens-on engine vs screens-off engine at the state reached by ``actions``."""
    on, off = h.engine(inst, h.SCREENS), h.engine(inst, h.DIAG)
    for a in actions:
        on.push(a)
        off.push(a)
    listed = set(on.legal_actions())
    cand = off.legal_actions()
    assert listed <= set(cand), f"{label}: screened engine lists a non-L1∧L2 action"
    bad = []
    for a in cand:
        post = off.copy()
        post.push(a)
        ok = _c1_ok(post.completion_check())
        if (a in listed) != ok:
            bad.append((f"listed={a in listed} post_c1={ok}", a, post.completion_check()))
    assert not bad, f"{label} depth={on.depth} actions={actions}\n" + "\n".join(map(str, bad))
    return len(cand) - len(listed)


def _greedy_seq(h, inst, extra):
    w = None
    for w in h.greedy_walk(inst, extra):
        pass
    return w.eng.actions()


@pytest.mark.parametrize("name,seeds,must_bind", [
    ("fixture_a", (), False), ("fixture_b", (), False), ("rich_a", (1, 2), False), ("rich_b", (1, 2), True),
    ("tiny_a", (6,), False), ("tiny_b", (5,), False),
    ("crowded_1", (1, 2, 3), True), ("crowded_4", (1, 2, 3), True), ("crowded_b", (1, 2, 3), False)])
def test_c1_screen_exact(h, request, name, seeds, must_bind):
    """Every state of a greedy heaviest-first rollout and of random rollouts (screens on)."""
    inst = request.getfixturevalue(name)
    seqs = [_greedy_seq(h, inst, h.SCREENS)]
    for s in seeds:
        seqs.append(_walk_states(h, inst, h.SCREENS, s, 40).actions())
    pruned = 0
    for seq in seqs:
        for d in range(len(seq) + 1):
            pruned += _c1_compare(h, inst, seq[:d], name)
    assert pruned or not must_bind, "capacity screens never bound: comparison is vacuous"


def test_c1_screen_exact_small(h, ladder_small):
    """Bigger ship (stale small rung): late greedy states and every 4th state of a random rollout."""
    seq = _greedy_seq(h, ladder_small, h.SCREENS)
    pruned = sum(_c1_compare(h, ladder_small, seq[:d], "small greedy") for d in range(len(seq) - 4, len(seq)))
    seq = _walk_states(h, ladder_small, h.SCREENS, 1, 60).actions()
    pruned += sum(_c1_compare(h, ladder_small, seq[:d], "small seed=1") for d in range(3, len(seq) + 1, 4))
    assert pruned


@pytest.mark.parametrize("name", ["fixture_a", "fixture_b", "rich_a", "rich_b", "tiny_a", "tiny_b", "crowded_1",
                                  "crowded_4", "crowded_b", "ladder_small"])
def test_own_model_soundness(h, request, name):
    """Pushing a listed action never yields a state whose own completion_check fails."""
    inst = request.getfixturevalue(name)
    n_walks = 2 if name == "ladder_small" else 6
    for seed in range(n_walks):
        rng = random.Random(seed)
        e = h.engine(inst, h.SCREENS)
        assert all(e.completion_check().values()) or e.dead_end() is not None
        while True:
            legal = e.legal_actions()
            if not legal:
                assert e.is_complete() or e.dead_end() is not None
                break
            a = rng.choice(list(legal)) if seed else min(legal, key=lambda x: h.greedy_key(e, x))
            e.push(a)
            check = e.completion_check()
            assert all(check.values()), (name, seed, e.actions(), check)


# ------------------------------------------------------------------ 4. completeness smoke
def test_greedy_completes_small(h, ladder_small):
    from stowage.checker.rules import check_static_rules
    from stowage.moves import apply_plan

    w = None
    for w in h.greedy_walk(ladder_small, h.SCREENS):
        pass
    e = w.eng
    assert e.is_complete(), (e.depth, e.dead_end())
    plan = e.to_plan()
    assert plan.ballast is None
    res = apply_plan(ladder_small, plan)
    assert res.legal, res.violations
    rep = check_static_rules(ladder_small, res.final_state.ship, call_index=ladder_small.call_index)
    hard = [v for v in rep.violations if v.severity == "hard"]
    assert not hard, hard
    assert sorted(p.container_id for p in res.final_state.ship.placements) == sorted(ladder_small.load_list)


# ------------------------------------------------------------------ 6. C2 in the engine (synthetic screen)
def _synthetic_screen(T, side_limit, R_total):
    """Rows over the engine's cells: list moment Σ tcg·m within ±side_limit, plus total mass ≤ R_total."""
    import numpy as np

    from stowage.feasibility.global_bounds import GlobalScreen

    cells = {c.id: c for c in T.instance.vessel.cells}
    tcg = np.array([cells[c].tcg_m for c in T.cell_ids])
    A = np.vstack([tcg, np.ones(len(tcg))])
    return GlobalScreen(A, np.zeros((2, 0)), [0.0, 0.0], [-side_limit, 0.0], [side_limit, R_total], [], [],
                        float(T.share.max()), ("list_moment", "total"))


def test_c2_engine_synthetic(h, monkeypatch, tiny_a):
    import numpy as np

    from stowage.feasibility import global_bounds

    made = {}
    total = sum(b.gross_mass_kg for b in tiny_a.containers if b.id in tiny_a.load_list)

    def fake(cls, instance, tables, *, margin_scale=1.0):
        made["s"] = _synthetic_screen(tables, side_limit=0.15 * total, R_total=total + 1)
        return made["s"]

    monkeypatch.setattr(global_bounds.GlobalScreen, "from_instance", classmethod(fake))
    pruned_any = False
    for seed in range(5):
        rng = random.Random(seed)
        e = h.engine(tiny_a, {"global_screen": 1})
        screen = made["s"]
        assert e.diagnostics["global_screen"] == "on"
        off = h.engine(tiny_a, {"global_screen": 0})
        while True:
            legal = e.legal_actions()
            pruned_any |= len(legal) < len(off.legal_actions())
            for a in legal:
                c = e.copy()
                c.push(a)
                assert c._cur.ref is not None and screen.feasible(c._cur.ref), (seed, e.actions(), a)
                # the reference is consistent with what is aboard
                assert np.isclose(c._cur.ref.R, float(c.T.mass[c._cur.rem].sum()))
            if not legal:
                break
            a = rng.choice(list(legal))
            e.push(a)
            off.push(a)
    assert pruned_any, "synthetic list-moment row never bound: test is vacuous"


def test_c2_root_infeasible(h, monkeypatch, tiny_a):
    from stowage.feasibility import global_bounds

    def fake(cls, instance, tables, *, margin_scale=1.0):
        return _synthetic_screen(tables, side_limit=1e9, R_total=1.0)   # total mass ≤ 1 kg: impossible

    monkeypatch.setattr(global_bounds.GlobalScreen, "from_instance", classmethod(fake))
    e = h.engine(tiny_a, {"global_screen": 1})
    d = e.dead_end()
    assert d is not None and d.depth == 0
    assert d.rule_ids == ("iscode.stability.margin", "iacs.strength.margin")
    assert tuple(e.legal_actions()) == ()
    assert e.completion_check()["c2"] is False


# ------------------------------------------------------------------ 7. fail closed
def test_unbuildable_hull_fails_closed(h, fixture_a):
    """Hull present but the 3.7 linear model can't be built (fixture hull lacks declared fleet inputs).
    (The stale ladder instances also fail closed, but their model build takes minutes before failing.)"""
    assert fixture_a.vessel.hull is not None
    e = h.engine(fixture_a)                          # global_screen default (1)
    d = e.dead_end()
    assert d is not None and d.depth == 0 and d.rule_ids == ("checker.missing_vessel_data",)
    assert tuple(e.legal_actions()) == ()
    assert e.diagnostics["global_screen"].startswith("unavailable")


# ------------------------------------------------------------------ 8. legacy re-exports
def test_legacy_reexports():
    from stowage.feasibility.capacity import CompletionCapacity, completion_violations
    from stowage.feasibility.global_bounds import GlobalBounds

    assert callable(completion_violations) and isinstance(CompletionCapacity, type) and isinstance(GlobalBounds, type)

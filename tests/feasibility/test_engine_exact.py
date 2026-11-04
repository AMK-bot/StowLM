"""Feasibility engine v2: L1 ∧ L2 exactness (plan 4.4, the key test).

With ``completion_screens: 0, global_screen: 0`` the engine must list *exactly* the actions that pass L1 (expander
step + 2.3 move engine) and L2 (2.4 ``placement_violations``, hard). The oracle below tries every remaining box ×
every vessel slot independently of the engine's tables.
"""

from __future__ import annotations

import pytest

from stowage.actions.space import Discharge, Place
from stowage.checker.rules import placement_violations
from stowage.moves import IllegalMoveError, apply_move


def oracle_places(h, w) -> set:
    inst, ex, ws = w.instance, w.ex, w.ws
    boxes = {b.id: b for b in inst.containers}
    remaining = sorted({b for b in inst.load_list if ex.where.get(b, ("",))[0] == "yard"} | set(ex.buffer))
    out = set()
    for b in remaining:
        for s in h.vessel_slots(inst):
            a = Place(b, s)
            trial = ex.copy()
            try:
                moves = trial.step(a)
            except ValueError:
                continue
            st, before = ws, None
            try:
                for m in moves:
                    before = st
                    st = apply_move(inst, st, m)
            except IllegalMoveError:
                continue
            assert moves[-1].kind in ("load", "restow_on") and moves[-1].container_id == b
            vs = placement_violations(inst, before.ship, boxes[b], s)
            if not any(v.severity == "hard" for v in vs):
                out.add(a)
    return out


def oracle_discharges(w) -> set:
    inst, ex, ws = w.instance, w.ex, w.ws
    out = set()
    for b in sorted(inst.discharge_list):
        if ex.where.get(b, ("",))[0] != "ship":
            continue
        trial = ex.copy()
        try:
            moves = trial.step(Discharge(b))
            st = ws
            for m in moves:
                st = apply_move(inst, st, m)
        except (ValueError, IllegalMoveError):
            continue
        if set(st.yard.quay_buffer) & set(inst.discharge_list):
            continue
        out.add(Discharge(b))
    return out


def _check_state(h, w, label):
    legal = w.eng.legal_actions()
    got_p = {a for a in legal if isinstance(a, Place)}
    got_d = {a for a in legal if isinstance(a, Discharge)}
    want_p = oracle_places(h, w)
    want_d = oracle_discharges(w)
    ctx = f"{label} depth={w.eng.depth} actions={w.eng.actions()} buffer={w.ex.buffer}"
    extra, missed = got_p - want_p, want_p - got_p
    assert not extra and not missed, (f"{ctx}\n  engine-only (not L1∧L2): {sorted(extra, key=str)}"
                                     f"\n  oracle-only (missed): {sorted(missed, key=str)}")
    assert got_d == want_d, f"{ctx}\n  engine {sorted(got_d, key=str)} oracle {sorted(want_d, key=str)}"
    if not legal:
        assert w.eng.is_complete() or w.eng.dead_end() is not None


CASES = [("fixture_a", [(s, d) for s in range(6) for d in (1, 2, 3, 5)]),
         ("fixture_b", [(s, d) for s in range(6) for d in (0, 2, 4, 6)]),
         ("rich_a", [(s, d) for s in range(8) for d in (2, 4, 6, 8)]),
         ("rich_b", [(s, d) for s in range(8) for d in (1, 3, 5, 7, 9)])]


@pytest.mark.parametrize("name,walks", CASES)
def test_l1_l2_exact_fixture(h, request, name, walks):
    """Fixture (DG, OOG, away-from-heat + under-deck boxes; B: layover with discharge); rich_*: plus DG of other
    classes (segregation), E6-heavy, over-width OOG, light boxes."""
    inst = request.getfixturevalue(name)
    for seed, depth in walks:
        w = h.random_walk(inst, seed, depth, h.DIAG)
        _check_state(h, w, f"{name} seed={seed}")


@pytest.mark.parametrize("seed,layover", [(3, False), (11, False), (4, True), (9, True)])
def test_l1_l2_exact_tiny(h, seed, layover):
    """Random yards on the fixture ship (blocked load boxes → relocations; B: discharges, restows)."""
    inst = h.tiny(seed, layover=layover)
    for k, depth in enumerate((0, 2, 3, 4, 6, 7, 9)):
        w = h.random_walk(inst, 100 * seed + k, depth, h.DIAG)
        _check_state(h, w, f"tiny seed={seed} layover={layover}")


def test_l1_l2_exact_ladder_small(h, ladder_small):
    """A bigger ship (stale small rung, DG dropped): one mid-plan reachable state."""
    for seed, depth in ((2, 12),):
        w = h.random_walk(ladder_small, seed, depth, h.DIAG)
        _check_state(h, w, f"small seed={seed}")


def _with_synthetic_dg_doc(instance):
    """Explicit synthetic permissions for this regression, rather than bypassing current DoC checks."""
    from stowage.model.fixtures import build_instance

    data = instance.model_dump()
    data["vessel"]["dg_doc_areas"] = build_instance(with_dg_doc=True).model_dump()["vessel"]["dg_doc_areas"]
    for area in data["vessel"]["dg_doc_areas"]:
        area["classes_allowed"] = ["2.2", "3", "8", "9"]
    for stack in data["vessel"]["stacks"]:
        stack["dg_doc_area_id"] = "WEATHER-DECK" if stack["on_deck"] else "HOLD-01"
    # This DG/cover regression requires a declared destination handling policy.
    # THSGZ has none in the synthetic fixture; THBKK is the existing resolved
    # next call. Changing only the POD keeps both activation settings meaningful
    # without bypassing the activated C5 missing-evidence rejection.
    for box in data["containers"]:
        if box["pod"] == "THSGZ":
            box["pod"] = "THBKK"
    return type(instance).model_validate(data)


def test_forced_restow_l2_exact(h, rich_b):
    rich_b = _with_synthetic_dg_doc(rich_b)

    w = h.Walker(rich_b, h.DIAG)
    w.push(Place("QZXU0000020", "0030282"))      # class 3 DG box on deck of hatch cover HC-02-1
    a = Place("QZXU0009011", "0010202")         # class 2.2 DG box into the hold under HC-02-1
    assert w.eng.forces_restow(a)
    assert a in oracle_places(h, w)             # the expander restows the class 3 box first → no DG pair aboard
    assert w.eng.is_legal(a)


def _multitier_hold_instance():
    """Three plain 20' boxes, one two-tier hold and deck; no cached or placeholder physics."""
    from stowage.conventions import make_container_id
    from stowage.model import Instance
    from stowage.model.fixtures import build_instance

    d = build_instance().model_dump()
    v = d["vessel"]
    v["hull"], v["fixed_loads"] = None, []
    v["cells"] = [c for c in v["cells"] if c["id"].startswith("00101")]
    v["stacks"] = [s for s in v["stacks"] if s["id"].startswith("00101")]
    for s in v["stacks"]:
        s["max_mass_kg"] = 32000.0  # SYNTHETIC: light+heavy fits; medium+heavy exceeds the stack rating.
    cover = v["hatch_covers"][0]
    cover["hold_stack_ids"] = [s["id"] for s in v["stacks"] if not s["on_deck"]]
    cover["deck_stack_ids"] = [s["id"] for s in v["stacks"] if s["on_deck"]]
    v["hatch_covers"] = [cover]
    base = next(c for c in d["containers"] if c["length_ft"] == 20)
    d["containers"] = [dict(base, id=make_container_id("QZWU", i), gross_mass_kg=mass, dg=None,
                             oog=None, special_stowage=[], reefer=False, iso_type="22G1", height_m=2.591,
                             reefer_setpoint_c=None, pod="THBKK")
                       for i, mass in enumerate((25000.0, 15000.0, 6000.0), 1)]
    d["load_list"] = [c["id"] for c in d["containers"]]
    d["yard"]["stacks"] = [dict(key=f"A-{i:03d}-01", length_ft=20, boxes=[c["id"]])
                           for i, c in enumerate(d["containers"], 1)]
    return Instance.model_validate(d)


def test_forced_restow_checks_cover_prefix_physical_legality(h):
    """An inaccessible upper deck blocker makes the entire cover-clearing group physically illegal."""
    d = _multitier_hold_instance().model_dump()
    heavy, medium, light = d["load_list"]
    d["stage"] = "B"
    d["load_list"] = [heavy]
    d["yard"]["stacks"] = [s for s in d["yard"]["stacks"] if heavy in s["boxes"]]
    d["onboard"]["placements"] = [dict(container_id=medium, slot="0010182"),
                                    dict(container_id=light, slot="0010184")]
    d["port"]["quay_crane"]["max_on_deck_tiers"] = 1
    inst = type(_multitier_hold_instance()).model_validate(d)
    w = h.Walker(inst, h.DIAG)
    a = Place(heavy, "0010102")
    assert w.eng.forces_restow(a)
    assert a not in oracle_places(h, w)
    assert not w.eng.is_legal(a)


@pytest.mark.parametrize("infeasible_arrival_reference", [False, True])
def test_forced_restow_c2_uses_released_cargo(h, monkeypatch, infeasible_arrival_reference):
    """A synthetic row rejects the pre-restow fixed cargo and admits the actual post-restow completion."""
    import numpy as np

    from stowage.feasibility.global_bounds import GlobalScreen
    from stowage.model.fixtures import build_instance

    d = _multitier_hold_instance().model_dump()
    d["containers"] = [c for c in d["containers"] if c["gross_mass_kg"] < 20000]
    d["load_list"] = [c["id"] for c in d["containers"]]
    keep = set(d["load_list"])
    d["yard"]["stacks"] = [s for s in d["yard"]["stacks"] if set(s["boxes"]) <= keep]
    medium = next(c["id"] for c in d["containers"] if c["gross_mass_kg"] == 15000)
    light = next(c["id"] for c in d["containers"] if c["gross_mass_kg"] == 6000)
    if infeasible_arrival_reference:
        d["stage"] = "B"
        d["load_list"] = [medium]
        d["yard"]["stacks"] = [s for s in d["yard"]["stacks"] if light not in s["boxes"]]
        d["onboard"]["placements"] = [{"container_id": light, "slot": "0010182"}]
    # Trigger the integration path; the explicitly synthetic linear rows below replace fixture hull physics.
    d["vessel"]["hull"] = build_instance().vessel.hull.model_dump()
    inst = type(_multitier_hold_instance()).model_validate(d)

    def screen(cls, instance, tables, **kwargs):
        suffixes = ("82", "84") if infeasible_arrival_reference else ("02", "82", "84")
        row = np.array([[float(s.endswith(suffixes)) for s in tables.cell_ids]])
        limit = 0.0 if infeasible_arrival_reference else 15000.0
        return GlobalScreen(row, np.empty((1, 0)), [0.0], [-np.inf], [limit], [], [], 15000.0)

    monkeypatch.setattr(GlobalScreen, "from_instance", classmethod(screen))
    e = h.engine(inst)
    if infeasible_arrival_reference:
        assert e._cur.c2_dead
    else:
        e.push(Place(light, "0010182"))
    a = Place(medium, "0010102")
    assert e.forces_restow(a)
    assert e.is_legal(a)
    e.push(a)
    assert e._cur.ex.buffer == [light]
    assert e.completion_check()["c2"]
    assert e.is_legal(Place(light, "0010104"))


def test_multitier_hold_completeness_against_exhaustive_oracle(h):
    """Uncapped finite-state enumeration, independent of the engine's local/capacity candidate tables.

    Explore every L1/L2 action, including hatch restows, then work backwards from static-valid departures.
    Every edge with a valid completion must survive C1, and the minimum current lift count must be preserved.
    This is a local/C1 proof on this four-cell fixture, not a global-physics completion guarantee.
    """
    from collections import deque
    from types import SimpleNamespace

    from stowage.actions.expander import Expander
    from stowage.checker.rules import check_static_rules
    from stowage.moves import initial_state

    inst = _multitier_hold_instance()

    def key(ex):
        return (tuple(sorted(ex.where.items())), tuple(sorted(ex.covers_off.items())))

    start = SimpleNamespace(instance=inst, ex=Expander(inst), ws=initial_state(inst), prefix=())
    nodes, edges, complete = {key(start.ex): start}, {}, set()
    todo = deque(nodes)
    forced = stacked = 0
    while todo:
        k = todo.popleft()
        w = nodes[k]
        if all(w.ex.where[b][0] == "ship" for b in inst.load_list) and not w.ex.buffer:
            if not any(v.severity == "hard" for v in check_static_rules(
                    inst, w.ws.ship, call_index=inst.call_index).violations):
                complete.add(k)
            edges[k] = []
            continue
        edges[k] = []
        for a in sorted(oracle_places(h, w), key=h.SPACE.sort_key):
            ex, ws = w.ex.copy(), w.ws
            moves = ex.step(a)
            for m in moves:
                ws = apply_move(inst, ws, m)
            nk = key(ex)
            forced += any(m.kind == "restow_off" for m in moves)
            stacked += a.slot.endswith(("04", "84"))
            edges[k].append((a, nk, len(moves)))
            if nk not in nodes:
                nodes[nk] = SimpleNamespace(instance=inst, ex=ex, ws=ws, prefix=w.prefix + (a,))
                todo.append(nk)
    assert complete and forced and stacked
    # Reverse reachability also includes cycles; iterative relaxation terminates on this finite graph.
    distance = {k: len(nodes[k].ex.copy().finish()) for k in complete}
    while True:
        changed = False
        for k, es in edges.items():
            costs = [cost + distance[nk] for _, nk, cost in es if nk in distance]
            if costs and min(costs) < distance.get(k, float("inf")):
                distance[k], changed = min(costs), True
        if not changed:
            break
    checked = 0
    for k in distance:
        w = nodes[k]
        e = h.engine(inst, h.SCREENS)
        # Inspect each independently reached state, without allowing the engine to prune the oracle graph.
        for a in w.prefix:
            e._cur = e._child(e._cur, a)
        allowed = set(e.legal_actions())
        valid = [(a, nk, cost) for a, nk, cost in edges[k] if nk in distance]
        assert all(a in allowed for a, _, _ in valid), (w.prefix, valid, allowed)
        if valid:
            assert min(cost + distance[nk] for a, nk, cost in valid if a in allowed) == distance[k]
        checked += len(valid)
    assert checked > 20

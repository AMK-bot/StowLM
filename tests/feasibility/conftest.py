"""Shared instances and helpers for the feasibility engine v2 tests (plan 4.4).

E1/E2/E6 use the catalogue's D25 weight-class defaults (provisional, pending expert Q2/Q4/Q11); other SYNTHETIC
values (e.g. an OOG clearance, a widened DG Document of Compliance) are labelled where they are set.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.conventions import cells_of_slot, parse_slot
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import Instance
from stowage.moves import initial_state

REPO = Path(__file__).resolve().parents[2]
SPACE = get_action_space("boxslot")

# SYNTHETIC thresholds (not from the catalogue / expert): needed so E1/E2/E6 can be evaluated at all.
# D25 (2026-10-27): E1/E2/E6 now have catalogue defaults (weight classes L < 10 t <= M < 20 t <= H), so the earlier
# synthetic kg thresholds are gone; instances use the catalogue values.
SYNTHETIC_OVERRIDES: dict = {}

DIAG = {"completion_screens": 0, "global_screen": 0}   # L1 ∧ L2 only
SCREENS = {"global_screen": 0}                         # L1 ∧ L2 ∧ C1


_ARCHIVED_PERF_FILES = frozenset({
    "test_perf_witness_1028.py", "test_perf_dg_1028.py", "test_perf_equivalence_1028.py",
})


def pytest_addoption(parser):
    parser.addoption(
        "--run-feasibility-archive", action="store_true", default=False,
        help="Collect never-promoted perf-1028 archive tests; original source guards still apply.",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "feasibility_archive: never-promoted perf-1028 experiment (explicit opt-in only)",
    )


def pytest_ignore_collect(collection_path, config):
    # Ignore before module import: witness qualification loads its guarded
    # candidate at import time. The archive deliberately retains its original
    # fail-closed pins and is not current production-source qualification.
    if collection_path.name in _ARCHIVED_PERF_FILES and not config.getoption("--run-feasibility-archive"):
        return True
    return None


def with_overrides(instance: Instance, *, drop_dg: bool = False) -> Instance:
    data = instance.model_dump()
    data["rule_overrides"] = {**data.get("rule_overrides", {}), **SYNTHETIC_OVERRIDES}
    data["id"] = data["id"] + ".feas-test"
    if drop_dg:
        dg = {c["id"] for c in data["containers"] if c.get("dg")}
        data["load_list"] = [b for b in data["load_list"] if b not in dg]
    return Instance.model_validate(data)


def engine(instance: Instance, extra: dict | None = None):
    return make_feasibility_engine(instance, SPACE, config=FeasibilityConfig(extra=dict(extra or {})))


def vessel_slots(instance: Instance) -> list[str]:
    """Every vessel slot, built independently of tables.py: 20' slots = cells; 40' slots = even bays whose two
    odd-bay cells exist at the same row/tier."""
    cells = {c.id for c in instance.vessel.cells}
    out = set(cells)
    for cid in cells:
        a = parse_slot(cid)
        slot = f"{a.bay + 1:03d}{a.row:02d}{a.tier:02d}"
        try:
            cs = cells_of_slot(slot)
        except ValueError:
            continue
        if len(cs) == 2 and all(c in cells for c in cs):
            out.add(slot)
    return sorted(out)


class Walker:
    """An engine plus an independent expander + 2.3 WorldState kept alongside (oracle replay)."""

    def __init__(self, instance: Instance, extra: dict | None = None):
        self.instance = instance
        self.eng = engine(instance, extra)
        self.ex = Expander(instance)
        self.ws = initial_state(instance)

    def push(self, action) -> None:
        from stowage.moves import apply_move

        self.eng.push(action)
        for m in self.ex.step(action):
            self.ws = apply_move(self.instance, self.ws, m)


def random_walk(instance: Instance, seed: int, depth: int, extra: dict | None = None) -> Walker:
    rng = random.Random(seed)
    w = Walker(instance, extra)
    for _ in range(depth):
        legal = w.eng.legal_actions()
        if not legal:
            break
        w.push(rng.choice(list(legal)))
    return w


def greedy_key(eng, action):
    """Heaviest first; prefer no forced restows; then lowest tier; then sort key (deterministic)."""
    mass = {b.id: b.gross_mass_kg for b in eng.instance.containers}
    if isinstance(action, Place):
        tier = parse_slot(action.slot).tier
        return (eng.forces_restow(action), -mass[action.box], tier, SPACE.sort_key(action))
    return (eng.forces_restow(action), 0.0, 0, SPACE.sort_key(action))


def greedy_walk(instance: Instance, extra: dict | None = None, max_steps: int = 500):
    """Yield a Walker after each greedy push (heaviest-first)."""
    w = Walker(instance, extra)
    yield w
    for _ in range(max_steps):
        legal = w.eng.legal_actions()
        if not legal:
            return
        w.push(min(legal, key=lambda a: greedy_key(w.eng, a)))
        yield w


@pytest.fixture(scope="session")
def fixture_a() -> Instance:
    from stowage.model.fixtures import build_instance

    return with_overrides(build_instance(layover=False))


@pytest.fixture(scope="session")
def fixture_b() -> Instance:
    from stowage.model.fixtures import build_instance

    return with_overrides(build_instance(layover=True))


@pytest.fixture(scope="session")
def ladder_small() -> Instance:
    from stowage.io import load_instance

    return with_overrides(load_instance(REPO / "experiments/instances/small/seed-00000001.json"), drop_dg=True)


@pytest.fixture(scope="session")
def ladder_tiny() -> Instance:
    from stowage.io import load_instance

    return with_overrides(load_instance(REPO / "experiments/instances/tiny/seed-00000001.json"), drop_dg=True)


def tiny(seed: int, *, layover: bool = False, n_load: int = 6, n_other: int = 4) -> Instance:
    from stowage.actions.samples import tiny_instance

    return with_overrides(tiny_instance(seed, n_load=n_load, n_other=n_other, layover=layover))


@pytest.fixture(scope="session")
def h():
    """This module's helpers (importlib mode: conftest isn't importable by name)."""
    import sys

    return sys.modules[__name__]


def rich_fixture(*, layover: bool = False) -> Instance:
    """Fixture plus SYNTHETIC extra boxes so more rules bind: two more DG boxes of other classes (segregation
    against DG aboard), an E6-heavy box, an over-width OOG box and a light box (E1/E2)."""
    from stowage.model.fixtures import build_instance

    data = build_instance(layover=layover).model_dump()
    dg0 = next(c for c in data["containers"] if c.get("dg"))
    plain = next(c for c in data["containers"] if c["id"] == "QZXU0000035")
    extra = []

    def _cid(n):
        from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id

        return make_container_id(SYNTHETIC_OWNER_CODES[0], n)

    def box(cid, **kw):
        c = {**plain, "id": cid, "special_stowage": [], "dg": None, "oog": None, **kw}
        extra.append(c)

    box(_cid(901), dg={**dg0["dg"], "un_number": "1006", "imdg_class": "2.2", "packing_group": None,
                            "flashpoint_c": None}, pod="THSGZ")
    box(_cid(902), dg={**dg0["dg"], "un_number": "2794", "imdg_class": "8", "packing_group": None,
                            "flashpoint_c": None})
    box(_cid(903), gross_mass_kg=32000.0)
    box(_cid(904), gross_mass_kg=6000.0)
    box(_cid(905), oog={"over_height_m": 0.0, "over_width_port_m": 0.3, "over_width_starboard_m": 0.3,
                            "over_length_fore_m": 0.0, "over_length_aft_m": 0.0})
    data["containers"] += extra
    data["load_list"] += [c["id"] for c in extra]
    keys = ["A-005-01", "A-006-01", "B-002-01", "B-003-01", "B-004-01"]
    data["yard"]["stacks"] += [{"key": k, "length_ft": 20, "boxes": [c["id"]]} for k, c in zip(keys, extra)]
    for st in data["vessel"]["stacks"]:   # SYNTHETIC Document of Compliance widening for the extra DG classes
        st["dg_classes_allowed"] = sorted(set(st["dg_classes_allowed"] or []) | {"2.2", "3", "8", "9"})
    data["id"] += ".rich"
    inst = with_overrides(Instance.model_validate(data))
    inst.rule_overrides["expert.e9.over_width_length"] = {"oog_clearance": 0.5}   # SYNTHETIC clearance
    return inst


@pytest.fixture(scope="session")
def rich_a() -> Instance:
    return rich_fixture(layover=False)


@pytest.fixture(scope="session")
def rich_b() -> Instance:
    return rich_fixture(layover=True)


@pytest.fixture(scope="session")
def tiny_a() -> Instance:
    return tiny(3, n_load=7, n_other=4)


@pytest.fixture(scope="session")
def tiny_b() -> Instance:
    return tiny(4, layover=True, n_load=6, n_other=3)


@pytest.fixture(scope="session")
def crowded_1() -> Instance:
    """12 load boxes on the 16-cell fixture ship: capacity / weight-order screens bind."""
    return tiny(1, n_load=12, n_other=2)


@pytest.fixture(scope="session")
def crowded_4() -> Instance:
    return tiny(4, n_load=12, n_other=2)


@pytest.fixture(scope="session")
def crowded_b() -> Instance:
    return tiny(4, layover=True, n_load=10, n_other=2)

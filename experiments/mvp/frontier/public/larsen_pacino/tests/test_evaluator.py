"""Tests for the independent evaluator: published arithmetic, floors, and hand-made cases."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import evaluator as ev  # noqa: E402
import published as pub  # noqa: E402

ROOT = "/Users/anda/Projects/meta-hackathon-stowage/data/raw/larsen-pacino"


@pytest.mark.parametrize("row", pub.TABLE2, ids=lambda r: f"inst{r['inst']}")
def test_table2_arithmetic(row):
    """Obj60 recomputes from its printed KPI columns; VM has 3 figures, so +-0.5."""
    calc = ev.objective(row["UL"], row["HO"], row["OV"], row["MK"], row["VM"], row["BP"],
                        row["ES"], row["NR"], row["FB"])
    assert abs(calc - row["obj"]) <= 0.5


def test_loadlist_mapping_matches_table1():
    for row in pub.TABLE1:
        inst = ev.read_instance(pub.path_instance(ROOT, row["inst"]))
        n20 = sum(b.length == 20 for b in inst.load)
        n40 = sum(b.length == 40 for b in inst.load)
        reefers = sum(b.reefer for b in inst.load)
        assert (n20, n40) == (row["n20"], row["n40"])
        assert n20 + 2 * n40 == row["load_teu"]
        assert reefers == row["reefers"]
        rel = sum(1 if b.length == 20 else 2 for b in inst.release)
        assert rel == row["release_teu"]


@pytest.mark.parametrize("row", pub.TABLE2, ids=lambda r: f"inst{r['inst']}")
def test_release_only_floors_do_not_exceed_published(row):
    """A release-only plan has every release KPI floor, so each must sit at or below Table 2."""
    k = row["inst"]
    v = ev.read_vessel(pub.path_vessel(ROOT, k))
    inst = ev.read_instance(pub.path_instance(ROOT, k))
    r = ev.evaluate(v, inst, [])
    assert r["OV"] <= row["OV"]
    assert r["HO"] <= row["HO"]
    assert r["NR"] <= row["NR"]
    assert r["BP"] <= row["BP"]
    assert r["FB"] <= row["FB"]
    assert r["ES"] >= row["ES"]
    assert r["UL"] == len(inst.load)
    assert r["MK"] == 0


@pytest.mark.parametrize("row", pub.APPENDIX2, ids=lambda r: f"inst{r['inst']}")
def test_displacement_matches_appendix2(row):
    """Lightship + release + load, less the published UL boxes, is close to Appendix II.
    The unloaded weight is at most UL * 27 t, and at least UL * 3 t."""
    k = row["inst"]
    v = ev.read_vessel(pub.path_vessel(ROOT, k))
    inst = ev.read_instance(pub.path_instance(ROOT, k))
    full = sum(b.const_w for b in v.bays) + sum(b.weight for b in inst.boxes)
    ul = pub.TABLE2[k - 1]["UL"]
    assert 3 * ul - 1 <= full - row["disp"] <= 27 * ul + 1


MINI = """# Ship: bays stacks tiers tcgTollerance
2 2 4 0.100
## HydroPoints: displacement minLcg maxLcg metacenter
1000 -5.000 5.000 30.000
3000 -5.000 5.000 25.000
5000 -5.000 5.000 20.000
## Bay: index lcg minShear maxShear maxBending constWeight constWeighVcg
0 10.000 -500.000 500.000 100000.000 500.000  10
### BuoyancyPoints: buojancy
500.000
1500.000
2500.000
### Stack: index tcg
0 -2.000
#### AboveDeck: identifier maxHeight maxWeight20 maxWeight40 vcg
1 10.000 40.000 60.000 20.000
#### Cell: tier reefer
12 0
11 0
10 0
#### BelowDeck: identifier maxHeight maxWeight20 maxWeight40 vcg
2 10.000 40.000 60.000 5.000
#### Cell: tier reefer
3 1
2 0
1 0
### Stack: index tcg
1 2.000
#### AboveDeck: identifier maxHeight maxWeight20 maxWeight40 vcg
1 10.000 40.000 60.000 20.000
#### Cell: tier reefer
12 0
11 0
10 0
#### BelowDeck: identifier maxHeight maxWeight20 maxWeight40 vcg
2 10.000 40.000 60.000 5.000
#### Cell: tier reefer
3 0
2 0
1 0
## Bay: index lcg minShear maxShear maxBending constWeight constWeighVcg
1 -10.000 -500.000 500.000 100000.000 500.000  10
### BuoyancyPoints: buojancy
500.000
1500.000
2500.000
### Stack: index tcg
0 -2.000
#### AboveDeck: identifier maxHeight maxWeight20 maxWeight40 vcg
1 10.000 40.000 60.000 20.000
#### Cell: tier reefer
11 0
10 0
"""

TYPES = """# Transport type: id length=(20,40) weight type=(DC,RC,HC,HR)
0 20 10 DC
1 40 10 DC
2 40 20 DC
3 40 10 RC
4 20 20 DC
5 40 10 HC
"""


def mini(tmp_path, containers):
    vp = tmp_path / "v.txt"
    vp.write_text(MINI)
    ip = tmp_path / "i.txt"
    ip.write_text("# Parameters: nPorts nContainers\n5 %d\n" % len(containers) + TYPES +
                  "# Container: startPort endPort typeId [bay stack tier slot]\n" +
                  "\n".join(containers) + "\n")
    return ev.read_vessel(vp), ev.read_instance(ip)


def test_hand_case_all_kpis(tmp_path):
    """Hold of stack 0: release 40' to port 2 at tier 1. On it, load 40' to port 3 (stack OV).
    Deck of stack 0: load 40' to port 4 above a hold with a box to port 2 (HO, Eq 15).
    Bay 0 deck stack 1: release 40' to port 1. Hold of stack 1: load 40' to port 1 (HO, Eq 16,
    since the same hatch carries release on deck)."""
    v, inst = mini(tmp_path, [
        "0 2 2 0 0 1 1",      # 0 release hold s0 tier1, 20 t, port 2
        "0 1 1 0 1 10 1",     # 1 release deck s1, 10 t, port 1
        "0 3 1",              # 2 load, 10 t, port 3
        "0 4 1",              # 3 load, 10 t, port 4
        "0 1 1",              # 4 load, 10 t, port 1
        "0 2 1",              # 5 load, never placed
    ])
    plan = [(2, (0, 0, 2, 1)), (3, (0, 0, 10, 1)), (4, (0, 1, 1, 1))]
    r = ev.evaluate(v, inst, plan)
    assert r["UL"] == 1
    assert r["OV"] == 1            # box 2 over box 0
    # deck cells: box 3 (port 4 > first_out=1) and box 1 (port 1, not later): 1 cell.
    # Eq 16: hold cells with load under a hatch with release on deck: boxes 2 and 4: 2 cells.
    assert r["HO"] == 3
    assert r["MK"] == 3
    assert r["BP"] == 2 + 3        # deck ports {1,4}; hold ports {2,3,1}
    assert r["ES"] == 1            # 5 sections, 4 used
    assert r["FB"] == 3 + 4 + 2    # ports are 1-based in FB
    # NR: hold s0 tier 3 has the only plug and is empty
    assert r["NR"] == 0
    # floors of cell positions: hold base 5, deck base 20, pitch 2.61, box centre 1.3
    expected_vm = (500 * 10 * 2 + 20 * (5 + 1.3) + 10 * (20 + 1.3)     # lightship, box 0, box 1
                   + 10 * (5 + 2.61 + 1.3) + 10 * (20 + 1.3) + 10 * (5 + 1.3))  # boxes 2, 3, 4
    assert r["VM"] == pytest.approx(expected_vm)
    assert r["obj"] == pytest.approx(ev.objective(1, 3, 1, 3, expected_vm, 5, r["ES"], 0, 9))


def test_empty_stack_count(tmp_path):
    v, inst = mini(tmp_path, ["0 2 2 0 0 1 1", "0 1 1 0 1 10 1"])
    r = ev.evaluate(v, inst, [])
    assert len(v.sections) == 5
    assert r["ES"] == 3


def test_stability_hand_numbers(tmp_path):
    """Disp 1000 + 40 = 1040 sits in bracket 1; buoyancy per bay is 520 by symmetry."""
    v, inst = mini(tmp_path, ["0 1 2 0 0 1 1", "0 1 2 0 1 1 1"])
    r = ev.evaluate(v, inst, [])
    assert r["disp"] == pytest.approx(1040)
    assert r["lm"] == pytest.approx(400)   # (540 - 500) * 10
    assert r["tm"] == pytest.approx(0)
    # shear at bay 0 = 540 - 520 = +20, then back to 0 at bay 1
    assert r["profile"][0][0] == pytest.approx(20)
    assert r["profile"][1][0] == pytest.approx(0)
    # cumulative lever from bay 0: lever 20 at bay 1, net -20
    assert r["profile"][1][1] == pytest.approx(-400)
    # physical moment at bay 1: net +20 sits 20 m forward of the station
    assert r["profile"][1][2] == pytest.approx(400)
    assert r["feasible"]


def test_tcg_violation(tmp_path):
    v, inst = mini(tmp_path, ["0 1 2 0 0 1 1"])
    r = ev.evaluate(v, inst, [])
    # 20 t at -2 m is 40 t-m; 0.1 * 1020 = 102, so fine
    assert "stability_tcg" not in r["violations"]
    v.tcg_tol = 0.01
    r = ev.evaluate(v, inst, [])
    assert r["violations"]["stability_tcg"] == 1


def test_lcg_violation_and_bracket(tmp_path):
    v, inst = mini(tmp_path, ["0 1 2 0 0 1 1"])
    v.hydro = [(1000.0, 0.0, 0.0, 1.0), (3000.0, 0.0, 0.0, 1.0)]
    r = ev.evaluate(v, inst, [])
    assert r["violations"]["stability_lcg_max"] == 1
    v.hydro = [(5000.0, 0.0, 0.0, 1.0), (6000.0, 0.0, 0.0, 1.0)]
    r = ev.evaluate(v, inst, [])
    assert r["violations"]["stability_displacement"] == 1


def test_shear_and_bending_violations(tmp_path):
    v, inst = mini(tmp_path, ["0 1 2 0 0 1 1", "0 1 2 0 1 1 1"])
    v.bays[0].max_shear = 10.0
    r = ev.evaluate(v, inst, [])
    assert r["violations"]["stability_shear_max"] == 1
    v.bays[0].max_shear = 500.0
    v.bays[1].min_shear = 1.0
    r = ev.evaluate(v, inst, [])
    assert r["violations"]["stability_shear_min"] == 1
    v.bays[1].min_shear = -500.0
    v.bays[1].max_bend = 399.0
    r = ev.evaluate(v, inst, [])
    assert r["violations"]["stability_bending"] == 1
    v.bays[1].max_bend = 401.0
    r = ev.evaluate(v, inst, [])
    assert r["feasible"]


@pytest.mark.parametrize("containers,plan,key", [
    (["0 1 1"], [(0, (0, 0, 99, 1))], "no_such_cell"),
    (["0 1 1", "0 1 1"], [(0, (0, 0, 1, 1)), (1, (0, 0, 1, 1))], "slot_taken_eq2_3"),
    (["0 1 1"], [(0, (0, 0, 1, 2))], "forty_in_fore_eq4"),
    (["0 1 0"], [(0, (0, 0, 1, 1))], "lone_twenty_eq5"),
    (["0 1 1"], [(0, (0, 0, 2, 1))], "floating_box_eq11"),
    (["0 1 3", "0 1 3"], [(0, (0, 1, 1, 1)), (1, (0, 1, 2, 1))], "reefer_without_plug_eq10"),
    (["0 1 1", "0 1 2"], [(0, (0, 0, 1, 1)), (1, (0, 0, 2, 1))], "heavier_on_lighter_eq11"),
    (["0 1 1", "0 1 1", "0 1 0", "0 1 0"],
     [(0, (0, 0, 1, 1)), (2, (0, 0, 2, 1)), (3, (0, 0, 2, 2))], "twenty_on_forty_eq11"),
    (["0 1 0", "0 1 0", "0 1 2"],
     [(0, (0, 0, 1, 1)), (1, (0, 0, 1, 2)), (2, (0, 0, 1, 1))], "slot_taken_eq2_3"),
    (["0 1 1"], [(0, (0, 0, 1, 1)), (0, (0, 0, 2, 1))], "box_placed_twice_eq6"),
    (["0 1 1 0 0 1 1"], [(0, (0, 0, 2, 1))], "moved_release_eq12"),
])
def test_each_rule_fires(tmp_path, containers, plan, key):
    v, inst = mini(tmp_path, containers)
    r = ev.evaluate(v, inst, plan)
    assert key in r["violations"]
    assert not r["feasible"]


def test_forty_on_lone_twenty_is_unsupported(tmp_path):
    v, inst = mini(tmp_path, ["0 1 0 0 0 1 1", "0 1 1"])
    r = ev.evaluate(v, inst, [(1, (0, 0, 2, 1))])
    assert r["violations"]["floating_box_eq11"] == 1
    assert r["notes"]["release_lone_twenty"] == 1


def test_forty_on_pair_uses_sum(tmp_path):
    # pair of 10 + 10 t carries a 20 t 40', which breaks the printed per-slot rule but not the sum
    v, inst = mini(tmp_path, ["0 1 0", "0 1 0", "0 1 2"])
    r = ev.evaluate(v, inst, [(0, (0, 0, 1, 1)), (1, (0, 0, 1, 2)), (2, (0, 0, 2, 1))])
    assert r["feasible"] and r["strict_eq11_40on20"] == 1


def test_stack_limits_use_per_slot_form(tmp_path):
    # two 20' of 20 t: Eq (8) as printed sums to 40, per slot 20: limit 30 passes per slot only
    v, inst = mini(tmp_path, ["0 1 4", "0 1 4"])
    v.by_cell[(0, 0, 1)].w20 = 30.0
    r = ev.evaluate(v, inst, [(0, (0, 0, 1, 1)), (1, (0, 0, 1, 2))])
    assert r["feasible"]
    v.by_cell[(0, 0, 1)].w20 = 19.0
    r = ev.evaluate(v, inst, [(0, (0, 0, 1, 1)), (1, (0, 0, 1, 2))])
    assert "stack_weight20_eq8" in r["violations"]


def test_height_limit(tmp_path):
    v, inst = mini(tmp_path, ["0 1 5"] * 3)
    r = ev.evaluate(v, inst, [(0, (0, 0, 1, 1)), (1, (0, 0, 2, 1)), (2, (0, 0, 3, 1))])
    assert "stack_height_eq9" not in r["violations"]   # 8.79 <= 10
    v.by_cell[(0, 0, 1)].hmax = 8.0
    r = ev.evaluate(v, inst, [(0, (0, 0, 1, 1)), (1, (0, 0, 2, 1)), (2, (0, 0, 3, 1))])
    assert "stack_height_eq9" in r["violations"]


def test_overstow_physical_overlap(tmp_path):
    """A 20' aft above a 20' fore that leaves first does not overstow it; a 40' does."""
    v, inst = mini(tmp_path, ["0 2 4", "0 9 4", "0 9 0", "0 3 0"])
    # tier 1: fore box 0 (port 2), aft box 1 (port 9). tier 2: aft box 2 (port 9), fore box 3 (port 3)
    r = ev.evaluate(v, inst, [(0, (0, 0, 1, 2)), (1, (0, 0, 1, 1)), (2, (0, 0, 2, 1)), (3, (0, 0, 2, 2))])
    assert r["OV"] == 1   # only box 3 (fore, port 3) over box 0 (fore, port 2)


@pytest.mark.parametrize("row", pub.TABLE2, ids=lambda r: f"inst{r['inst']}")
def test_fb_needs_one_based_ports(row):
    """Release FB plus at most the whole load must be able to reach the published FB."""
    k = row["inst"]
    v = ev.read_vessel(pub.path_vessel(ROOT, k))
    inst = ev.read_instance(pub.path_instance(ROOT, k))
    r = ev.evaluate(v, inst, [])
    assert r["FB"] + sum(b.end + 1 for b in inst.load) >= row["FB"]
    if k == 24:   # with 0-based ports this instance cannot reach its published FB
        zero = r["FB"] - sum(1 for b in inst.release if not v.by_cell[b.pos[:3]].deck)
        assert zero + sum(b.end for b in inst.load) < row["FB"]

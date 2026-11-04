"""Prepare D93 text artifacts only; never import physics or edit live sources."""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
from pathlib import Path
import pprint
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
PATCH = HERE.parent / "manual-c2.patch"
REPORT = "experiments/mvp/solve47/physics/manual-regression/REPORT.md"
MEASUREMENT = "experiments/mvp/solve47/physics/manual-regression/fixtest.py"
PATHS = ("src/stowage/vessel/fleet_manual.py",
         "src/stowage/vessel/fleet_manual_witness.py",
         "tests/vessel/test_fleet_manual_restore.py")
INPUTS = ("params/fleet/feeder_500.toml", "params/fleet/feeder_800.toml",
          "params/fleet/feeder_1700.toml", "params/cargo/feeder_export.toml",
          "params/ports/laem_chabang_like.toml", "params/ports/small_feeder.toml",
          "src/stowage/rules/catalogue.toml", REPORT, MEASUREMENT)


def sha(data):
    return hashlib.sha256(data).hexdigest()


REGRESSION = '''

@pytest.mark.parametrize('name', FLEET)
def test_c2_manual_condition_keeps_at_least_half_cargo_mass_below_deck(manuals, name):
    """D93 designer-recipe regression, not an extra checker/operating rule."""
    condition = 'C2'
    vessel, recipes = manuals(name)
    recipe = recipes[condition]
    cells = {cell.id: cell for cell in vessel.cells}
    stacks = {stack.id: stack for stack in vessel.stacks}
    total = sum(mass for _, mass in recipe['cargo_cells'])
    on_deck = sum(mass for cid, mass in recipe['cargo_cells']
                  if stacks[cells[cid].stack_id].on_deck)
    assert total > 0.
    assert on_deck <= .5 * total, (name, condition, on_deck, total)
'''


def main():
    originals = {name: (ROOT / name).read_text() for name in PATHS}
    candidate = dict(originals)
    anchor = "        current = CURRENT_WITNESSES.get(f'{name}.{condition}')\n"
    assert candidate[PATHS[0]].count(anchor) == 1
    correction = '''        if name == 'feeder_1700' and condition == 'C2':
            # APPROXIMATION: D93 homogeneous synthetic designer loading. The
            # enlarged HEEL reserve made the uniform scan jump to 1,604 light
            # boxes, including 1,032 on deck. Retain the previously qualified
            # 25.8t mass with a budget-limited count and hold-first placement.
            # No checker limit or other loading condition is changed.
            uniform = 25800.
            cargo = place_manual_cargo(vessel, [uniform]*max(0,min(count,int(budget//uniform))))
            correction = ('D93 homogeneous 25.8t budget-limited count, hold-first placement; '
                          'restores the C2 regime after HEEL enlargement')
            source += '; experiments/mvp/solve47/physics/manual-regression/REPORT.md: D93 corrected synthetic recipe'
'''
    candidate[PATHS[0]] = candidate[PATHS[0]].replace(anchor, correction + anchor)
    label = '''        operating_label = f'finisher_seed{current["seed"]}_{current["time_limit_s"]:g}s' if current else label
'''
    assert candidate[PATHS[0]].count(label) == 1
    candidate[PATHS[0]] = candidate[PATHS[0]].replace(label, '''        # A declared static witness must not be labelled as a new finisher run.
        operating_label = (current.get('ballast_label') or
            f'finisher_seed{current["seed"]}_{current["time_limit_s"]:g}s') if current else label
''')

    raw_params = (ROOT / "params/fleet/feeder_500.toml").read_bytes()
    params = tomllib.loads(raw_params.decode())
    masses = {}
    for tank in params["tanks"]:
        if not tank["adjustable"]:
            continue
        tid = tank["id"]
        # Exact manual_ballast_masses(..., 'DB+HEEL+FPT0.50') semantics:
        # FPT labels explicitly cover FWD-DEEP too.
        fraction = (1. if tid.startswith(("DB-", "HEEL")) else
                    .5 if tid.startswith(("FPT", "FWD-DEEP")) else 0.)
        masses[tid] = tank["capacity_m3"] * tank["content_density_kg_m3"] * fraction
    definition = dict(condition="feeder_500.C2", parameter_sha256=sha(raw_params),
        cargo_reserve_label="DB+HEEL+FPT0.38", cargo_uniform_mass_kg=18000.,
        operating_label="DB+HEEL+FPT0.50", tank_masses_kg=masses)
    witness = dict(input_fingerprint=sha(json.dumps(definition, sort_keys=True,
                    separators=(",", ":")).encode()),
        parameter_sha256=sha(raw_params),
        ballast_label="D93_declared_DB+HEEL+FPT0.50", tank_masses_kg=masses,
        source=f"{REPORT}; exact candidate from {MEASUREMENT} sha256:{sha((ROOT / MEASUREMENT).read_bytes())}",
        seed=None, time_limit_s=0.,
        provenance=dict(created_by="vessel.fleet_manual_witness.D93_declared_operating_witness",
            seed=None, parents=[
                "APPROXIMATION: synthetic D93 loading-manual ballast, not approved operating instructions",
                "DECLARATION: static half FPT and FWD-DEEP with full DB/HEEL; no new finisher invocation",
                f"params/fleet/feeder_500.toml sha256:{sha(raw_params)}",
                f"{REPORT} sha256:{sha((ROOT / REPORT).read_bytes())}",
                f"{MEASUREMENT} sha256:{sha((ROOT / MEASUREMENT).read_bytes())}"]))
    anchor = "CURRENT_WITNESSES = _OperatingWitnesses(CURRENT_WITNESSES)\n"
    assert candidate[PATHS[1]].count(anchor) == 1
    addition = ("# APPROXIMATION: D93 static synthetic operating witness, measured by the\n"
        "# independent complete checker in the cited report; not a finisher run.\n"
        "# Preserve the historical .38 cargo reserve; this map supplies operating\n"
        "# ballast only. FPT0.50 includes both FPT and FWD-DEEP pairs.\n"
        "CURRENT_WITNESSES['feeder_500.C2'] = " + pprint.pformat(witness, sort_dicts=False, width=100) + "\n\n\n")
    candidate[PATHS[1]] = candidate[PATHS[1]].replace(anchor, addition + anchor)
    candidate[PATHS[2]] += REGRESSION

    patches = []
    pins = {}
    for name in PATHS:
        ast.parse(candidate[name], filename=name)
        target = HERE / "candidate" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(candidate[name])
        pins[name] = dict(before_sha256=sha(originals[name].encode()),
                         candidate_sha256=sha(candidate[name].encode()))
        patches += difflib.unified_diff(originals[name].splitlines(keepends=True),
            candidate[name].splitlines(keepends=True), fromfile="a/"+name, tofile="b/"+name)
    PATCH.write_text("".join(patches))
    standalone = '''"""D93 deck-share regression; runs against the live tree, never mutates it.

PREPARED ONLY: the feeder_1700 C2 case is expected to fail before manual-c2.patch.
This synthetic recipe guard is not a new checker or operating limit.
"""
import pytest
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_manual import standard_manual_conditions

FLEET = ('feeder_500', 'feeder_800', 'feeder_1700')

@pytest.fixture(scope='module')
def manuals():
    cache = {}
    def get(name):
        if name not in cache:
            vessel = load_vessel(name)
            cache[name] = vessel, {recipe['condition']: recipe for recipe in
                standard_manual_conditions(vessel, load_ship_params(name))}
        return cache[name]
    return get
'''+REGRESSION
    ast.parse(standalone)
    (HERE / "test_manual_deck_share.py").write_text(standalone)
    checked = subprocess.run(["git", "apply", "--check", str(PATCH.relative_to(ROOT))],
                             cwd=ROOT, capture_output=True, text=True)
    record = dict(prepared_only=True, no_live_edits=True, source_files=pins,
        input_files={name: sha((ROOT / name).read_bytes()) for name in INPUTS},
        patch_sha256=sha(PATCH.read_bytes()), witness_definition=definition,
        witness_total_kg=sum(masses.values()), report_sha256=sha((ROOT / REPORT).read_bytes()),
        report_measurements_not_rerun=True, ast_validation="all candidates and standalone parse",
        apply_check=dict(command=["git", "apply", "--check", str(PATCH.relative_to(ROOT))],
                         returncode=checked.returncode, stdout=checked.stdout, stderr=checked.stderr))
    assert all(sha((ROOT / name).read_bytes()) == pin["before_sha256"] for name,pin in pins.items())
    (HERE / "source-pins.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(dict(patch=str(PATCH.relative_to(ROOT)), apply_check=checked.returncode,
        witness_total_kg=record["witness_total_kg"], no_physics_or_tests_run=True)))
    raise SystemExit(checked.returncode)


if __name__ == "__main__":
    main()

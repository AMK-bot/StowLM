"""Compare baseline/candidate public recipes using existing vessel JSON only.

Run only after CPU admission. Missing/mismatched vessel caches refuse; this
never calls load_vessel, builds hull tables, qualifies physics, or runs a checker.
The recipe factory still performs its own small target-displacement integration.
All candidate modules and witness changes are private to this process.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from time import perf_counter
import tomllib
import types

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main():
    from stowage.model import Vessel
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.vessel import fleet, fleet_cache, fleet_manual
    assert not rule_freeze_v1_enabled(), "D93 comparison is MVP OFF only"
    pins = json.loads((HERE / "source-pins.json").read_text())
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    def unchanged():
        assert all(digest(ROOT / name) == pin['before_sha256']
                   for name, pin in pins['source_files'].items())
        assert all(digest(ROOT / name) == pin for name, pin in pins['input_files'].items())
    unchanged()
    candidates = {}
    for name in ('fleet_manual_witness', 'fleet_manual'):
        path = HERE / 'candidate/src/stowage/vessel' / (name+'.py')
        module = types.ModuleType('private_D93_' + name)
        module.__file__ = str(path)
        exec(compile(path.read_text(), str(path), 'exec'), module.__dict__)
        candidates[name] = module
    candidate = candidates['fleet_manual']
    candidate.CURRENT_WITNESSES = candidates['fleet_manual_witness'].CURRENT_WITNESSES
    cache_dir = Path(os.environ.get('STOWAGE_VESSEL_CACHE_DIR', ROOT/'experiments/cache/vessels'))
    rows = []
    caches = []
    expectations = []

    def expect(name, passed, details):
        expectations.append(dict(name=name, passed=bool(passed), details=details))
    started = perf_counter()
    for ship in ('feeder_500', 'feeder_800', 'feeder_1700'):
        raw = fleet._parameter_bytes(ship)
        key = fleet_cache._cache_key(ship, 'full', True, raw)
        path = cache_dir / f'{ship}.full.hull.{key}.json'
        assert path.exists(), f'No matching existing vessel JSON; refusing build: {path}'
        payload = path.read_bytes()
        vessel = Vessel.model_validate_json(payload)
        params = tomllib.loads(raw.decode())
        before = {r['condition']: r for r in fleet_manual.standard_manual_conditions(vessel, params)}
        after = {r['condition']: r for r in candidate.standard_manual_conditions(vessel, params)}
        adjustable = {t.id for t in vessel.tanks if t.adjustable}
        cells = {c.id: c for c in vessel.cells}
        stacks = {s.id: s for s in vessel.stacks}
        caches.append(dict(ship=ship, path=str(path.relative_to(ROOT)),
            json_sha256=hashlib.sha256(payload).hexdigest(), construction_identity=key,
            prior_max_gm_lashing_m=vessel.hull.max_gm_lashing_m,
            lashing_cap_after_not_recomputed=True))
        for condition in ('C1','C2','C3'):
            pair = {}
            for tag, recipes in (('baseline', before), ('candidate', after)):
                recipe = recipes[condition]
                total = sum(m for _,m in recipe['cargo_cells'])
                deck = sum(m for cid,m in recipe['cargo_cells'] if stacks[cells[cid].stack_id].on_deck)
                pair[tag] = dict(boxes=recipe['boxes'], uniform_mass_kg=recipe['uniform_mass_kg'],
                    total_cargo_mass_kg=total, on_deck_mass_kg=deck,
                    on_deck_mass_fraction=deck/total, deck_share_regression_passed=deck <= .5*total,
                    ballast_mass_kg=sum(m for tid,m in recipe['departure_tank_masses_kg'].items()
                                        if tid in adjustable), cargo_budget_kg=recipe['cargo_budget_kg'],
                    cargo_reserve_ballast_label=recipe['cargo_reserve_ballast_label'],
                    operating_ballast_label=recipe['ballast_label'])
            if ship == 'feeder_500' and condition == 'C2':
                expect('500 C2 preserves exact cargo placements',
                    before[condition]['cargo_cells'] == after[condition]['cargo_cells'], pair)
                expect('500 C2 preserves cargo budget',
                    pair['baseline']['cargo_budget_kg'] == pair['candidate']['cargo_budget_kg'], pair)
                expect('500 C2 preserves .38 cargo reserve label',
                    pair['candidate']['cargo_reserve_ballast_label'] == 'DB+HEEL+FPT0.38', pair)
            if ship == 'feeder_1700' and condition == 'C2':
                expect('1700 C2 has 543 boxes', pair['candidate']['boxes'] == 543, pair)
                expect('1700 C2 retains 25.8t per box', pair['candidate']['uniform_mass_kg'] == 25800., pair)
                expect('1700 C2 has no deck cargo', pair['candidate']['on_deck_mass_kg'] == 0., pair)
            rows.append(dict(ship=ship, condition=condition, **pair))
    unchanged()
    floors = {}
    for ship in ('feeder_500','feeder_800','feeder_1700'):
        floors[ship] = {tag: min(row[tag]['ballast_mass_kg'] for row in rows if row['ship']==ship)
                       for tag in ('baseline','candidate')}
    deck_failures = [dict(ship=row['ship'], condition=row['condition'],
        baseline=row['baseline']['on_deck_mass_fraction'],
        candidate=row['candidate']['on_deck_mass_fraction']) for row in rows
        if not row['candidate']['deck_share_regression_passed']]
    for row in rows:
        if row['condition'] == 'C2':
            expect(f"{row['ship']} C2 deck share at most half",
                row['candidate']['deck_share_regression_passed'], row['candidate'])
    record = dict(method='Actual public baseline/candidate recipe factories; direct matching existing vessel JSON; no public vessel fallback/build',
        elapsed_s=perf_counter()-started, rule_freeze_v1=False, rows=rows, caches=caches,
        manual_ballast_floors_kg=floors, checker_run=False, hull_table_build=False,
        all_nine_deck_share_diagnostic_failures=deck_failures,
        requested_c2_regression_and_AB_expectations=expectations,
        unrelated_C1_C3_deck_shares_are_diagnostic_only=True,
        physics_qualification_run=False,
        borrowed_checker_evidence='experiments/mvp/solve47/physics/manual-regression/REPORT.md')
    (HERE / 'recipe-factory-comparison.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(dict(candidate_deck_share_passes=sum(r['candidate']['deck_share_regression_passed'] for r in rows),
        cases=len(rows), all_nine_diagnostic_failures=deck_failures,
        floors=floors, elapsed_s=record['elapsed_s'], checker_run=False)))
    # Persist all nine diagnostic rows before asserting the requested A/B fixes
    # and C2-only regression. C1/C3 counterexamples are not silently repaired.
    failed = [item for item in expectations if not item['passed']]
    assert not failed, failed


if __name__ == '__main__':
    main()

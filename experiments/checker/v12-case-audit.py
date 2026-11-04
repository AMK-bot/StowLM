"""APPROXIMATION: audit §5 regressions on production KCS-derived ships.

Invoke with uv run python; direct geometry is required for this correctness
baseline. The tests explain deliberately calibrated ratios and background mass.
No observation here independently validates real-ship casualty physics.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time

os.environ['STOWAGE_STABILITY_DIRECT'] = '1'
import pytest
from stowage.checker.full import check as original_check
ROOT = Path(__file__).resolve().parents[2]
rows = []
case = ''


def audited_check(instance, plan):
    start = time.perf_counter()
    report = original_check(instance, plan)
    metrics = {name: {key: value for key, value in part.metrics.items()
                     if key in {'gm_m', 'max_bm_ratio', 'max_sf_ratio',
                                'geometry_table_used', 'geometry_table_limit_fallback'}}
               for name, part in report.parts.items()}
    rows.append(dict(case=case, vessel_id=instance.vessel.id,
                     cells=len(instance.vessel.cells),
                     hull_offsets_ref=instance.vessel.hull.hull_offsets_ref,
                     fixed_loads=[load.model_dump() for load in instance.vessel.fixed_loads],
                     placements=[p.model_dump() for p in instance.onboard.placements],
                     seconds=time.perf_counter()-start,
                     passed=report.passed,
                     parts={name: dict(ran=part.ran, error=part.error)
                            for name, part in report.parts.items()},
                     metrics=metrics,
                     hard_rules=sorted({v.rule_id for v in report.violations if v.severity == 'hard'}),
                     stack_weight=[v.model_dump() for v in report.violations
                                   if v.rule_id == 'expert.physical.stack_weight']))
    assert all(p.ran and not p.error for p in report.parts.values()), rows[-1]['parts']
    return report


class Audit:
    def pytest_runtest_setup(self, item):
        global case
        case = item.nodeid
        item.module.check = audited_check

    def pytest_runtest_teardown(self, item):
        item.module.check = original_check


files = [*ROOT.glob('src/stowage/checker/*.py'),
         *ROOT.glob('src/stowage/model/*.py'),
         *ROOT.glob('src/stowage/vessel/*.py'),
         ROOT / 'src/stowage/versions.py',
         ROOT / 'params/fleet/feeder_800.toml', ROOT / 'params/fleet/feeder_1700.toml']
source_start = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
started = time.perf_counter()
exit_code = pytest.main([
    '-q', '-m', 'slow', str(ROOT / 'tests/checker/test_full_cases.py'),
    '-o', 'junit_family=xunit1',
    '--junitxml='+str(ROOT / 'experiments/checker/v12-cases-direct.xml'),
], plugins=[Audit()])
output = dict(approximation=True, direct_geometry=True, pytest_exit_code=exit_code,
              elapsed_seconds=time.perf_counter()-started, source_start_sha256=source_start,
              source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in files}, reports=rows)
(ROOT / 'experiments/checker/v12-case-audit.json').write_text(json.dumps(output, indent=2)+'\n')
raise SystemExit(exit_code)

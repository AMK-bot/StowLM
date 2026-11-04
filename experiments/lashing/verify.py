"""Run the complete suite and reuse its expensive fleet builds for task 3.3 QA.

Run with numerical libraries limited to one thread. The hook observes only the
existing full-fleet assembly tests; it does not change their assertions or data.
Writes progress after each fleet so long verification retains partial results.
"""
from __future__ import annotations

import json
from pathlib import Path
import resource
import time

import pytest

HERE = Path(__file__).resolve().parent


class FleetChecks:
    def __init__(self):
        self.rows = []

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        if not item.name.startswith('test_full_fleet_loader_assembles_real_hull'):
            yield
            return
        from stowage.vessel import fleet
        original = fleet.load_vessel

        def observed(*args, **kwargs):
            start = time.monotonic()
            vessel = original(*args, **kwargs)
            from stowage.checker.lashing import check_lashing
            from stowage.checker.visibility import check_visibility
            from stowage.model import Placement, ShipState
            from stowage.model.fixtures import build_instance
            instance = build_instance()
            instance.vessel = vessel
            cargo = [instance.containers[1], instance.containers[2]]
            for box, mass in zip(cargo, (20000., 3500.), strict=True):
                box.gross_mass_kg = mass
                box.dg = None
                box.oog = None
            stack = next(s for s in vessel.stacks if s.on_deck and len(s.cell_ids) >= 2)
            state = ShipState(vessel_id=vessel.id, placements=[
                Placement(slot=slot, container_id=box.id)
                for slot, box in zip(stack.cell_ids[:2], cargo, strict=True)])
            parts = [check_visibility(instance, state, call_index=0),
                     check_lashing(instance, state, call_index=0)]
            row = dict(vessel=vessel.id, wall_s=time.monotonic()-start,
                       reports={p.name:p.model_dump(mode='json') for p in parts})
            self.rows.append(row)
            (HERE/'fleet-verification.json').write_text(json.dumps(self.rows, indent=2)+'\n')
            assert all(p.ran for p in parts), [(p.name,p.error) for p in parts]
            assert not any(p.violations for p in parts), [(p.name,p.violations) for p in parts]
            return vessel

        fleet.load_vessel = observed
        try:
            yield
        finally:
            fleet.load_vessel = original


if __name__ == '__main__':
    start = time.monotonic()
    observer = FleetChecks()
    status = pytest.main(['-q', '-o', 'addopts=--import-mode=importlib'], plugins=[observer])
    (HERE/'suite-verification.json').write_text(json.dumps(dict(
        exit_code=status, wall_s=time.monotonic()-start,
        peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        fleet_checks=len(observer.rows)), indent=2)+'\n')
    raise SystemExit(status)

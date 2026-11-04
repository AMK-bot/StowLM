#!/usr/bin/env python3
"""Train-only integration verification of external D175 restrictions."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(ROOT / 'src'), str(HERE)]
from layout import (BlockLayout, BlockModel, _HOOK_MODULES, audit_layout, audit_packing,
                    constraint_rule, random_layouts, unrestricted_layout)
from stowage.actions.space import Place
from stowage.io import load_instance
from stowage.solver.twostep import TwoStepPlanner
from stowage.rules.catalogue import rule_freeze_v1


def main():
    manifest = json.loads((HERE / 'train_maps.json').read_text())
    chosen = manifest['maps'][0]
    instance = load_instance(ROOT / chosen['input'])
    assert hashlib.sha256((ROOT / chosen['input']).read_bytes()).hexdigest() == chosen['sha256']
    model = BlockModel.from_instance(instance)
    assert len(model.blocks) == chosen['block_count'] == 4
    assert set(model.cell_to_block) == {c.id for c in instance.vessel.cells}
    unrestricted = unrestricted_layout(instance, model)
    closed = BlockLayout({cat: frozenset() for cat in unrestricted.allowed}, 'all-closed')
    action = Place(instance.load_list[0], instance.vessel.cells[0].id)
    modules = [importlib.import_module(name) for name in _HOOK_MODULES]
    originals = {module: module.securing_geometry_violation for module in modules}
    # Record wrappers are installed and exceptions restore every owning module.
    try:
        with constraint_rule(instance, closed, model):
            for module in modules:
                assert module.securing_geometry_violation is not originals[module]
                assert module.securing_geometry_violation(instance, None, action).startswith('headroom:')
            raise RuntimeError('scope check')
    except RuntimeError as error:
        assert str(error) == 'scope check'
    assert all(module.securing_geometry_violation is original for module, original in originals.items())
    # A sentinel original shows unrelated threads and other instances pass through.
    module = modules[0]
    original = module.securing_geometry_violation
    module.securing_geometry_violation = lambda *args, **kwargs: 'original sentinel'
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with constraint_rule(instance, closed, model):
                assert module.securing_geometry_violation(instance, None, action).startswith('headroom:')
                assert pool.submit(module.securing_geometry_violation, instance, None, action).result() == 'original sentinel'
                other = instance.model_copy(update={'id': instance.id + '-other'})
                assert module.securing_geometry_violation(other, None, action) == 'original sentinel'
    finally:
        module.securing_geometry_violation = original
    with rule_freeze_v1(enabled=False):
        baseline = TwoStepPlanner().plan(instance, seed=0, time_limit_s=15)
        assert baseline.status == 'ok' and baseline.plan is not None, (baseline.status, baseline.message)
        candidates = random_layouts(instance, count=64, seed=175, base_plan=baseline.plan, model=model)
        assert len(candidates) == len({c.fingerprint for c in candidates}) == 64
        for candidate in candidates:
            assert not audit_packing(instance, candidate.metadata['packing_witness'], candidate, model)
        assert not audit_layout(instance, baseline.plan, unrestricted, model)
        assert audit_layout(instance, baseline.plan, closed, model)
        # Closed restrictions cannot return a violating accepted plan.
        with constraint_rule(instance, closed, model):
            rejected = TwoStepPlanner().plan(instance, seed=0, time_limit_s=1)
        assert rejected.plan is None or not audit_layout(instance, rejected.plan, closed, model)
        repeated = TwoStepPlanner().plan(instance, seed=0, time_limit_s=15)
        assert repeated.status == 'ok' and repeated.plan is not None
        assert repeated.plan.moves == baseline.plan.moves, 'unscoped baseline changed after hook exit'
    snapshot = json.loads((HERE / 'source_snapshot.json').read_text())['sha256']
    drift = [name for name, digest in snapshot.items() if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest]
    result = {'passed': True, 'map_id': chosen['map_id'], 'checks': ['input SHA', 'actual block ownership', 'all hook modules installed/restored', 'exception cleanup', 'thread/instance isolation', '64 unique witnessed layouts', 'packing audit', 'explicit/final layout audit', 'closed layout rejects', 'baseline unchanged after scoped restriction'], 'baseline_s': baseline.wall_time_s, 'source_drift': drift}
    (HERE/'verification.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))
    assert not drift, f'Pinned source drift: {drift}'

if __name__ == '__main__':
    main()

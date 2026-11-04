"""TRAIN f60 low planner/initial-securing diagnostic, never a certificate."""
from __future__ import annotations
import faulthandler
import os
import time
import scale_solver as s


def main():
    folder = s.HERE / 'pilots' / f'terminal-v4-{os.getpid()}'
    s.write(folder / 'owner.json', dict(pid=os.getpid(), at=s.now()))
    s.load_base(s.HERE / 'runtime/base')
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.feasibility import FeasibilityConfig
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    from stowage.moves import apply_plan
    from stowage.checker.lashing import check_lashing
    from stowage.solver.twostep import TwoStepPlanner
    import carrier_v4
    import reservations
    import terminal_v4
    job = next(j for j in s.manifest()['maps'] if j['stage'] == 'f60'
               and j['ship'] == 'feeder_500' and j['mass_level'] == 'low')
    instance = load_instance(s.train_path(job['input']))
    assert s.sha(s.train_path(job['input'])) == job['input_sha256']
    s.write(folder / 'declaration.json', dict(job=job, implementation=s.code_hashes(),
              certificate=False, primary_outcome=False, planner_seconds=300,
              diagnostics='Departure securing only at inherited ballast; not a finisher or full check'))
    started = time.monotonic()
    faulthandler.dump_traceback_later(60, repeat=True)
    with rule_freeze_v1(enabled=False), carrier_v4.install():
        restore_modes = reservations.install()
        restore_terminal = terminal_v4.install()
        try:
            result = TwoStepPlanner(feasibility_config=FeasibilityConfig(extra={'global_screen': 0})).plan(
                instance, seed=0, time_limit_s=300)
        finally:
            restore_terminal()
            restore_modes()
    s.write(folder / 'raw.json', result.model_dump(mode='json'))
    rows = {}
    old = PlannerResult.model_validate(s.read(s.HERE / 'runs/v3/maps' / job['map_id'] / 'raw.json'))
    for name, candidate in [('v3_selected', old), ('v4_selected', result)]:
        row = dict(status=candidate.status, policy=candidate.metrics.get('terminal_fast_selected_policy'),
                   substitutions=candidate.metrics.get('terminal_category_substitutions'))
        if candidate.status == 'ok':
            replay = apply_plan(instance, candidate.plan)
            row['native_replay_legal'] = replay.legal
            before = time.monotonic()
            part = check_lashing(instance, replay.final_state.ship, call_index=instance.call_index)
            row['securing_seconds'] = time.monotonic()-before
            row['margins'] = part.margins
            row['hard_violations'] = len([v for v in part.violations if v.severity == 'hard'])
            s.write(folder / f'{name}-securing.json', part.model_dump(mode='json'))
        rows[name] = row
    faulthandler.cancel_dump_traceback_later()
    s.write(folder / 'result.json', dict(rows=rows, elapsed_s=time.monotonic()-started,
                                        certificate=False, primary_outcome=False))
    print(rows, flush=True)


if __name__ == '__main__':
    main()

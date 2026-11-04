"""D152 final validation: one adapter on one frozen list with the audit recipe of the 8.1/8.2 parent audits.

Same code path as experiments/mvp/exit/rebaseline_on_host.py (llm.exit.runner.evaluate): batched greedy, mask + securing +
look-ahead, no rewinds, V4 finisher scoped to finalize, full checker in a watchdog, layered mask replay. Finisher 120 s
(the loop's baseline audit of 8.2 at 120 s equals the 90 s parent audit on all 30 maps). Resumable; refuses inputs outside
corpus/val. fix-t8-sft should run 8.3 with this same script. No training.
Usage: run_adapter.py <adapter_dir> <label> <list.txt> <set_name> [--workers 1]
"""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace

for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'
os.environ['STOWLM_MLX_CACHE_LIMIT_GB'] = '2'
os.environ.pop('STOWAGE_BALLAST_V4', None)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('adapter', type=Path)
    parser.add_argument('label')
    parser.add_argument('list', type=Path)
    parser.add_argument('set_name')
    parser.add_argument('--workers', type=int, default=1)
    args = parser.parse_args()
    from llm.exit.outcomes import outcome_counts
    from llm.exit.runner import adapter_identity, evaluate, source_pins, write_json
    from llm.exit.runtime import preflight_mlx_device, release_model
    paths = [(ROOT/line).resolve() for line in args.list.read_text().split()]
    corpus = ROOT/'experiments/mvp/corpus'
    if not paths or not all(p.is_relative_to((corpus/'val').resolve()) for p in paths):
        raise SystemExit('final validation takes corpus/val inputs only')
    adapter = args.adapter.resolve()
    out = HERE/args.label/args.set_name
    out.mkdir(parents=True, exist_ok=True)
    pins, identity = source_pins(), adapter_identity(adapter)
    preflight_mlx_device()
    options = SimpleNamespace(out=out, corpus=corpus, max_tokens=20000, audit_batch_size=4, workers=args.workers,
                              ballast_s=120., _loaded_source_pins=pins)
    try:
        rows = evaluate(adapter, paths, out, options)
    finally:
        release_model()
    if source_pins() != pins or adapter_identity(adapter) != identity:
        raise SystemExit('source or adapter changed during the run; results not published')
    evaluations, moves = 0, [r['weighted_moves'] for r in rows if r.get('passed') is True]
    for row in rows:
        securing = row.get('generation_timings', {}).get('securing') or {}
        if securing.get('bounds_available') is not True or securing.get('unavailable') or securing.get('fail_open'):
            raise SystemExit(f"securing unavailable on {row['instance_id']}; results not published")
        evaluations += securing.get('evaluations', 0)
    summary = dict(label=args.label, set=args.set_name, adapter=identity, **outcome_counts(rows),
                   mean_weighted_moves=sum(moves)/len(moves) if moves else None, securing_evaluations=evaluations)
    write_json(out/'rows.json', rows)
    write_json(out/'summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'adapter'}), flush=True)


if __name__ == '__main__':
    main()

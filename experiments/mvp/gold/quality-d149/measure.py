"""D149: stored gold vs fresh two-step v4.4 on a seeded train sample, through the loop's own verification.
Read-only on src/. Usage: measure.py sample | measure.py run <shard> <nshards>. One JSON per map; resumable. No GPU, no test."""
import json, random, sys, time, traceback
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
GOLD, CORPUS = ROOT / 'experiments/mvp/gold', ROOT / 'experiments/mvp/corpus'
SEED, TINY_PER_SHIP, SMALL_PER_SHIP, BALLAST_S = 149, 30, 15, 120.


def sample():
    pools = defaultdict(list)
    for path in sorted((GOLD / 'train').glob('*/*/*/seed-*/result.json')):
        row = json.loads(path.read_text())
        if row.get('passed') is True:
            pools[row['rung'], row['ship']].append(dict(case=str(path.parent.relative_to(GOLD)), input=row['input_path'],
                rung=row['rung'], ship=row['ship'], family=row['family'], solver=row['solver'], stored=row['objective'],
                status=(row.get('solver_metrics') or {}).get('optimality_status')))
    rng, chosen = random.Random(SEED), []
    for key in sorted(pools):
        n = TINY_PER_SHIP if key[0] == 'tiny' else SMALL_PER_SHIP
        chosen += rng.sample(pools[key], n)
    rng.shuffle(chosen)  # so a partial run is still a uniform prefix
    (HERE / 'sample.json').write_text(json.dumps(dict(seed=SEED, pool_sizes={'/'.join(k): len(v) for k, v in pools.items()}, rows=chosen), indent=1))
    print(len(chosen), {'/'.join(k): len(v) for k, v in pools.items()})


def verify(instance, candidate):
    from llm.exit.selection import verify_candidate
    v = verify_candidate(instance, candidate, ballast_s=BALLAST_S)
    d = v.details
    return dict(passed=bool(v.passed), moves=v.weighted_moves if v.passed else None, finisher_timeout=d.get('finisher_timeout'),
                unavailable=d.get('unavailable'), error=d.get('error'), timings=d.get('timings'), mask=(d.get('mask') or {}).get('passed'),
                checker_passed=d.get('checker_passed'), violations=[x.get('rule_id') for x in d.get('violations') or []][:6],
                count=(d.get('report') or {}).get('count'))


def run(shard, nshards):
    from llm.exit.selection import Candidate, baseline_candidate
    from llm.exit.replay import checker_epoch
    from stowage.io import load_instance, load_plan
    from stowage.moves import apply_plan
    rows = json.loads((HERE / 'sample.json').read_text())['rows'][shard::nshards]
    out = HERE / 'rows'
    out.mkdir(exist_ok=True)
    epoch = checker_epoch()
    for row in rows:
        target = out / (row['case'].replace('/', '__') + '.json')
        if target.exists():
            continue
        assert '/corpus/train/' in row['input'], row['input']
        started = time.time()
        result = dict(row, epoch=epoch)
        try:
            instance = load_instance(row['input'])
            plan = load_plan(GOLD / row['case'] / 'plan.json')
            replay = apply_plan(instance, plan)
            result.update(gold_recount=replay.count.weighted_nonproductive, gold_recount_legal=replay.legal)
            result['gold'] = verify(instance, Candidate(plan, 'gold', 'gold', {}))
            diagnostics = {}
            t = time.time()
            candidate = baseline_candidate(instance, time_limit_s=120., diagnostics=diagnostics)
            result['twostep_plan_s'] = time.time() - t
            result['twostep_diag'] = {k: v for k, v in diagnostics.items() if isinstance(v, (str, int, float, bool, type(None)))}
            result['twostep'] = verify(instance, candidate) if candidate is not None else dict(passed=False, moves=None, error='no two-step proposal')
        except Exception:
            result['error'] = traceback.format_exc()[-1500:]
        result['wall_s'] = time.time() - started
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, indent=1, default=str))
        temporary.replace(target)
        print(row['case'], result.get('gold', {}).get('moves'), result.get('twostep', {}).get('moves'), round(result['wall_s'], 1), flush=True)


if __name__ == '__main__':
    sample() if sys.argv[1] == 'sample' else run(int(sys.argv[2]), int(sys.argv[3]))

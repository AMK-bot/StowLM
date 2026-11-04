"""Run and score OOD-1's three arms, after PREREG.md is hashed. Resumable per arm.

Arms: the adopted reader swa-last-5 with its mask; untrained Glimmer with the same mask and prompt (v3 evaluate,
no adapter); the fair hand-written parser. Glimmer arms run through evaluate.main via finish_run's child, which
retries a denied admission. Metrics: exact match; wrong rule from a must-refuse sentence; accepted loosening.
Usage: python <this file> [glimmer-adopted|glimmer-untrained|fair|score]
"""
import json
from math import comb
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
from experiments.mvp.reader.schema import canonicalize  # noqa: E402

HERE = Path(__file__).resolve().parent
SET = HERE / 'set.jsonl'
PREREG = HERE / 'PREREG.md'
OUT = HERE / 'results'
ADOPTED = ROOT / 'experiments/mvp/reader/v3/candidates/swa-last-5'
ADOPTED_SHA = 'eff712e18a649804fcbd5b19db27baa709fa60701a2970d1fa58593f731c6f57'
ARMS = ('glimmer-adopted', 'glimmer-untrained', 'fair')


def sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows():
    return [json.loads(line) for line in SET.read_text().splitlines()]


def preregistered():
    recorded = (HERE / 'PREREG.sha256').read_text().split()[0]
    if sha(PREREG) != recorded:
        raise SystemExit('PREREG.md changed after registration')
    if f'`{sha(SET)}`' not in PREREG.read_text():
        raise SystemExit('set.jsonl differs from the registered hash')


def run_glimmer(arm):
    from experiments.mvp.reader.v3.finish_run import RETRY_EXIT, wait_for_admission
    output = OUT / f'{arm}.json'
    if output.exists():
        return
    if sha(ADOPTED / 'adapters.safetensors') != ADOPTED_SHA:
        raise SystemExit('adopted adapter changed')
    command = [sys.executable, '-m', 'experiments.mvp.reader.v3.finish_run', '--check-child', '--data', str(SET),
               '--backend', 'glimmer', '--output', str(output)]
    if arm == 'glimmer-adopted':
        command += ['--adapter', str(ADOPTED)]
    while True:
        wait_for_admission()
        code = subprocess.run(command, cwd=ROOT).returncode
        if code != RETRY_EXIT:
            break
    if code:
        raise SystemExit(f'{arm} failed with {code}')


def run_fair():
    from experiments.mvp.reader.v3.fair_parser import parse
    output = OUT / 'fair.predictions.jsonl'
    if not output.exists():
        output.write_text(''.join(json.dumps({'id': r['id'], 'prediction': json.dumps(parse(r['sentence'], r['context']))},
                                             ensure_ascii=False) + '\n' for r in rows()))


def predictions(arm):
    name = 'fair.predictions.jsonl' if arm == 'fair' else f'{arm}.predictions.jsonl'
    return {row['id']: row for row in map(json.loads, (OUT / name).read_text().splitlines())}


def raw_constraint(prediction):
    """Any parseable JSON object holding a constraint, schema-valid or not."""
    try:
        value = json.loads(prediction) if isinstance(prediction, str) else prediction
    except (TypeError, ValueError):
        return False
    return isinstance(value, dict) and 'constraint' in value


def judge(row, prediction):
    """Malformed output scores as an exact-match failure and, as registered, is not counted as a rule."""
    try:
        got = canonicalize(prediction, row['context'])
    except Exception:
        return 'malformed', None
    want = canonicalize(row['expected'], row['context'])
    if got == want:
        return 'exact', got
    if 'constraint' in got and 'refusal' in want:
        return 'wrong_rule_from_refusal', got
    if 'constraint' in got:
        return 'wrong_rule', got
    return 'wrong_refusal' if 'refusal' in want else 'refused_a_rule', got


def interval(k, n, level=.95):
    """Clopper-Pearson exact interval, by bisection on the binomial tail."""
    def tail_ge(p):
        return sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))

    def tail_le(p):
        return sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, k + 1))

    def solve(f, target, increasing):
        lo, hi = 0.0, 1.0
        for _ in range(80):
            mid = (lo + hi) / 2
            if (f(mid) < target) == increasing:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    alpha = (1 - level) / 2
    low = 0.0 if k == 0 else solve(tail_ge, alpha, True)
    high = 1.0 if k == n else solve(tail_le, alpha, False)
    return [low, high]


def count(k, n):
    return {'count': k, 'of': n, 'rate': k / n if n else None, 'ci95': interval(k, n) if n else None}


def score():
    data = rows()
    report, table = {}, []
    for arm in ARMS:
        found = predictions(arm)
        verdicts = []
        for row in data:
            pred = found.get(row['id'], {}).get('prediction')
            verdict, got = judge(row, pred) if pred is not None else ('missing', None)
            verdicts.append((row, verdict, got, raw_constraint(pred)))
            table.append({'arm': arm, 'id': row['id'], 'family': row['family'], 'register': row['register'],
                          'sentence': row['sentence'], 'expected': canonicalize(row['expected'], row['context']),
                          'got': got, 'raw': pred, 'verdict': verdict, 'raw_constraint': raw_constraint(pred)})
        refuse = [v for v in verdicts if v[0]['must_refuse']]
        rules = [v for v in verdicts if not v[0]['must_refuse']]
        loose = [v for v in verdicts if v[0]['loosening']]
        by = {}
        for row, verdict, _, _ in verdicts:
            by.setdefault(row['family'], [0, 0])
            by[row['family']][0] += verdict == 'exact'
            by[row['family']][1] += 1
        report[arm] = {'exact': count(sum(v[1] == 'exact' for v in verdicts), len(verdicts)),
                       'exact_in_scope': count(sum(v[1] == 'exact' for v in rules), len(rules)),
                       'exact_must_refuse': count(sum(v[1] == 'exact' for v in refuse), len(refuse)),
                       'wrong_rule_from_must_refuse': count(sum(v[1] == 'wrong_rule_from_refusal' for v in refuse), len(refuse)),
                       'accepted_loosening': count(sum('constraint' in (v[2] or {}) for v in loose), len(loose)),
                       # Not registered: any parseable constraint emission, schema-valid or not.
                       'raw_constraint_from_must_refuse': count(sum(v[3] for v in refuse), len(refuse)),
                       'raw_constraint_from_loosening': count(sum(v[3] for v in loose), len(loose)),
                       'wrong_rule_from_in_scope': count(sum(v[1] == 'wrong_rule' for v in rules), len(rules)),
                       'malformed': sum(v[1] in ('malformed', 'missing') for v in verdicts),
                       'by_family': {k: f'{a} of {b}' for k, (a, b) in sorted(by.items())}}
    (OUT / 'scores.json').write_text(json.dumps({'set_sha256': sha(SET), 'prereg_sha256': sha(PREREG),
                                                 'arms': report, 'rows': table}, indent=2, ensure_ascii=False) + '\n')
    return report


if __name__ == '__main__':
    preregistered()
    OUT.mkdir(exist_ok=True)
    step = sys.argv[1]
    if step == 'fair':
        run_fair()
    elif step == 'score':
        print(json.dumps(score(), indent=1))
    else:
        run_glimmer(step)

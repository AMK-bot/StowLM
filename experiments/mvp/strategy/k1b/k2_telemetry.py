"""D199 step 2, no GPU: where ladder decode time goes by map size, and the action mix per box. Reads K2's stored decode records and candidates. Mask cost
is measured by replaying each map's longest complete candidate on CPU through three stacks: production, + look-ahead, + securing. Whatever the mask doesn't
explain of the decode wall time is model steps plus re-prefill after rewinds, which aren't logged separately. Usage: k2_telemetry.py [workers=2]
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import statistics as st
import sys
import time

for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import compact

ROOT = HERE.parents[3]
OUT = HERE/'k2'


def mix(plan_row, n_loads, discharge_list):
    """One plan's actions, split into first placements, re-placements of a box already placed, and other ops; and its lifts by kind."""
    seen, first, again, other = set(), 0, 0, Counter()
    for action in plan_row['actions']:
        if action['op'] == 'P':
            if action['box'] in seen:
                again += 1
            else:
                seen.add(action['box'])
                first += 1
        else:
            other[action['op']] += 1
    return dict(actions=len(plan_row['actions']), first=first, again=again, other=dict(other), moves=dict(Counter(m['kind'] for m in plan_row['moves'])), n_loads=n_loads)


def replay_time(instance, plan, stack):
    from llm.lookahead import wrap_engine as lookahead
    from llm.securing import wrap_engine as securing
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.mask import MaskConfig, MaskedDecoder
    from llm.stowlm.sft_data import codec_for
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    space = get_action_space()
    engine = make_feasibility_engine(instance, space, config=FeasibilityConfig(extra={'global_screen': 0}))
    if stack >= 1:
        engine = lookahead(engine)
    if stack >= 2:
        engine = securing(engine)
    codec = codec_for(instance, load_tokenizer())
    decoder = MaskedDecoder(engine, codec, MaskConfig())
    actions = list(space.from_plan(instance, plan))
    started, tokens = time.perf_counter(), 0
    for action in actions:
        for token in codec.encode_action(action):
            decoder.allowed_token_ids()
            decoder.advance(token)
            tokens += 1
    return time.perf_counter()-started, tokens


def one(record_path):
    from stowage.io import load_instance
    from stowage.model import MovePlan
    rec = json.loads(Path(record_path).read_text())
    iid = rec['instance_id']
    target = OUT/'telemetry'/f'{iid}.json'
    if target.exists():
        return json.loads(target.read_text())
    instance = load_instance(ROOT/rec['input'])
    samples = [json.loads(p.read_text()) for p in sorted((OUT/'candidates'/iid).glob('sample-*.json'))]
    complete = [s for s in samples if s['plan'] is not None]
    m = rec['metrics']
    row = dict(instance_id=iid, boxes=len(instance.load_list), wall_s=m.get('pool_plan_wall_s'), tokens=m.get('tokens_out'), reached=len(samples), complete=len(complete),
               dead_ends=sum(s['status'] == 'dead_end' for s in samples), rewinds=sum(s['backtracks'] for s in samples), deadline=bool(m.get('deadline_exceeded')))
    row['mixes'] = [mix(s['plan'], len(instance.load_list), instance.discharge_list) for s in complete]
    compacted = []
    for s in complete:
        plan = MovePlan.model_validate(s['plan'])
        result, info = compact.compact_plan(instance, plan)
        if result is not None:
            compacted.append(dict(actions=len(result.actions), moves=dict(Counter(x.kind for x in result.moves))))
    row['compacted'] = compacted
    if complete:
        longest = MovePlan.model_validate(max(complete, key=lambda s: len(s['plan']['actions']))['plan'])
        times = [replay_time(instance, longest, stack) for stack in (0, 1, 2)]
        row['replay'] = dict(tokens=times[0][1], production_s=times[0][0], lookahead_s=times[1][0], securing_s=times[2][0])
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(row, indent=1))
    print(json.dumps({k: row[k] for k in ('instance_id', 'boxes', 'tokens', 'complete')}), flush=True)
    return row


def table(rows):
    lines = ['# K2 ladder telemetry (D199)', '',
             'Train ladder maps; no GPU. Wall time and tokens come from the decode records, and include dead-end samples. Mask ms per token comes from a CPU replay of each map’s longest complete candidate through production, then + look-ahead, then + securing, so it excludes re-prefill, and the replay ran on a busy machine. '
             '"Rest" is the wall time per token the mask doesn’t explain: model steps plus re-prefill after rewinds, not logged separately. Interim until the ladder decode ends.', '',
             '## Where decode time goes', '',
             '| Map | Boxes | Wall s | Tokens | Tok/s | Reached | Complete | Dead ends | Rewinds | Mask ms/token: production, + look-ahead, + securing | Mask share | Rest ms/token |', '|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|']
    for r in sorted(rows, key=lambda x: x['boxes']):
        wall, tokens = r['wall_s'], r['tokens']
        rate = tokens/wall if wall and tokens else None
        p = r.get('replay')
        if p and rate:
            ms = [1000*p[k]/p['tokens'] for k in ('production_s', 'lookahead_s', 'securing_s')]
            cell, share, rest = f'{ms[0]:.0f}, {ms[1]:.0f}, {ms[2]:.0f}', f'{ms[2]/(1000/rate)*100:.0f} %', f'{1000/rate-ms[2]:.0f}'
        else:
            cell, share, rest = '—', '—', '—'
        lines.append(f"| {r['instance_id'].replace('D192.medium-', '')} | {r['boxes']} | {wall:.0f} | {tokens} | {'—' if not rate else format(rate, '.1f')} | {r['reached']}/16 | {r['complete']} | {r['dead_ends']} | {r['rewinds']} | {cell} | {share} | {rest} |")
    lines += ['', '## Action mix per box, complete candidates', '',
              'Scratch paper is a `P` action on a box the plan already placed. The compacted row is the same stow with one placement per box, so what it keeps is what the plan needs. Lifts per box by kind, mean across complete candidates.', '',
              '| Map | Boxes | Plans | Actions per box | Re-placements per box | Other ops per box | Restow lifts per box | Relocations per box | Discharges per box | Compacted actions per box | Compacted restows per box | Compacted relocations per box |', '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in sorted(rows, key=lambda x: x['boxes']):
        if not r['mixes']:
            continue
        n, plans = r['boxes'], r['mixes']
        avg = lambda f: st.mean(f(p) for p in plans)/n
        lift = lambda p, *kinds: sum(p['moves'].get(k, 0) for k in kinds)
        comp = r['compacted']
        c = (lambda f: st.mean(f(p) for p in comp)/n) if comp else None
        lines.append(f"| {r['instance_id'].replace('D192.medium-', '')} | {n} | {len(plans)} | {avg(lambda p: p['actions']):.2f} | {avg(lambda p: p['again']):.2f} | {avg(lambda p: sum(p['other'].values())):.2f} | "
                     f"{avg(lambda p: lift(p, 'restow_off', 'restow_on', 'restow_shift')):.2f} | {avg(lambda p: lift(p, 'relocate')):.2f} | {avg(lambda p: lift(p, 'discharge')):.2f} | "
                     + (f"{c(lambda p: p['actions']):.2f} | {c(lambda p: lift(p, 'restow_off', 'restow_on', 'restow_shift')):.2f} | {c(lambda p: lift(p, 'relocate')):.2f} |" if comp else '— | — | — |'))
    plans = [p for r in rows for p in r['mixes']]
    if plans:
        total, again = sum(p['actions'] for p in plans), sum(p['again'] for p in plans)
        boxes, other = sum(p['n_loads'] for p in plans), sum(sum(p['other'].values()) for p in plans)
        lines += ['', f"**Pooled, {len(plans)} complete plans:** {total/boxes:.2f} actions per box; {boxes} first placements ({boxes/total*100:.0f} %), {again} re-placements of a box already placed ({again/total*100:.0f} %), {other} other ops ({other/total*100:.0f} %). "
                  'Complete plans only: dead-end samples store no actions.', '']
    return lines


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    records = sorted(str(p) for p in OUT.glob('decode/s*/instances/*.json'))
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(one, records))
    (HERE/'K2-TELEMETRY.md').write_text('\n'.join(table(rows)))


if __name__ == '__main__':
    main()

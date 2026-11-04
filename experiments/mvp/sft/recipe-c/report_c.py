"""D167 arms, the C vs C-ens rule, D158's outcome test, VALIDATION.md and 9.1b's SUMMARY.md. Numbers only.
An arm is {instance_id: row}; a row has outcome, moves, rung, result path, decode and CPU seconds, chosen sample."""
from __future__ import annotations

import json
from math import comb
from pathlib import Path
import statistics as st

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OWN = 'own pass'
FINAL = ROOT/'experiments/mvp/sft/final-validation'
AUDIT82 = ROOT/'experiments/mvp/exit/deployment-d137/parent-audit-8.2/audit-baseline/summary.json'
AUDIT_TWO = ROOT/'experiments/mvp/sft/val-audit30-twostep/120'


def fmt(value):
    return '—' if value is None else f'{value:.2f}'


def percent(value):
    return 'no mutual maps' if value is None else f'{value*100:+.1f} %'


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=1, sort_keys=True)+'\n')


def sign_p(a, b):
    n = a+b
    return min(1., 2*sum(comb(n, k) for k in range(min(a, b)+1))/2**n) if n else 1.


def arm(folder):
    """Rows of every shard under folder (s0, s1, ...)."""
    rows = {}
    for path in sorted(Path(folder).glob('s*/instances/*.json')):
        rec = json.loads(path.read_text())
        run = path.parent/rec['instance_id']
        outcome = json.loads((run/'outcome.json').read_text())['outcome']
        report = json.loads((run/'report.json').read_text()) or {}
        m = rec.get('metrics') or {}
        extra = (m.get('pass_at_1') or {}).get('extra_check_s') or 0
        rows[rec['instance_id']] = dict(
            outcome=outcome, moves=(report.get('count') or {}).get('weighted_nonproductive') if outcome == OWN else None,
            rung=rec['rung'], input=rec['input'], result=str(run/'result.json'), chosen_sample=m.get('chosen_sample'),
            decode_s=m.get('gen_wall_s') or 0, cpu_s=(m.get('candidate_selection_s') or 0)+extra, pipeline_s=rec['pipeline_s'],
            n_passing=m.get('n_checker_feasible'), n_complete=m.get('n_complete'), timeouts=len(m.get('finisher_budget_timeouts') or []),
            status=rec['status'], n_dead_end=m.get('n_dead_end'), n_max_tokens=m.get('n_max_tokens'), n_checked=m.get('n_candidates_checked'),
            candidate_failures=m.get('candidate_failures'),
            deadline=bool(m.get('deadline_exceeded')), greedy_passed=(m.get('pass_at_1') or {}).get('checker_passed'),
            greedy_objective=(m.get('pass_at_1') or {}).get('objective'))
    return rows


def not_derivable(c16, files):
    """Inputs whose 8.2 first-8 result the 16-sample run does not settle. A pass with chosen sample < 8 is the minimum over
    a superset that lies in the subset. No pass settles the subset only on affirmative evidence (no_pass_is_proven)."""
    settled = {r['input'] for r in c16.values() if (r['outcome'] == OWN and r['chosen_sample'] < 8) or no_pass_is_proven(r, 16)}
    return [f for f in files if f not in settled]


def no_pass_is_proven(row, samples):
    """All samples decoded, every complete one judged by the finisher and checker, none cut by a limit or an exception.
    An outer error returns no such metrics, so absence of a pass alone proves nothing (rev-recipec P1-1)."""
    if row['outcome'] == OWN or row['deadline'] or row['timeouts'] or row.get('n_complete') is None:
        return False
    decoded = row['n_complete']+(row.get('n_dead_end') or 0)+(row.get('n_max_tokens') or 0)
    failures = (row.get('candidate_failures') or {}).values()
    if decoded != samples or any(': ' in text and text.split(': ')[0].isidentifier() for text in failures):
        return False  # "ValueError: ..." is an exception on that candidate, not a verdict
    return row['n_complete'] == 0 or row.get('n_checked') == row['n_complete']


def ensemble(out, derive=True):
    """Union of 8 + 8: per map the fewer moves, 8.2 on a tie. The union of two rank-ordered selections is the union's own."""
    out = Path(out)
    a82, a83 = arm(out/'ens-82'), arm(out/'ens-83')
    if derive:
        # APPROXIMATION: reused maps are charged half the 16-sample run's compute; the plans are exactly that run's.
        halved = {i: dict(r, decode_s=r['decode_s']/2, cpu_s=r['cpu_s']/2, pipeline_s=r['pipeline_s']/2)
                  for i, r in arm(out/'c').items() if i not in a82}
        a82 = {**halved, **a82}
    rows = {}
    for iid in a83:
        left, right = a82[iid], a83[iid]
        pick = left if left['moves'] is not None and (right['moves'] is None or left['moves'] <= right['moves']) else right
        if pick['moves'] is None:
            pick = left
        rows[iid] = dict(pick, source_adapter='8.2' if pick is left else '8.3',
                         decode_s=left['decode_s']+right['decode_s'], cpu_s=left['cpu_s']+right['cpu_s'],
                         pipeline_s=left['pipeline_s']+right['pipeline_s'])
    return rows


def twostep_arm(folder):
    rows = {}
    for path in sorted(Path(folder).glob('*.json')):
        r = json.loads(path.read_text())
        rows[r['instance_id']] = dict(outcome=OWN if r.get('passed') else r.get('outcome', 'fail'), moves=r.get('weighted_nonproductive'),
                                      input=r['input'], result=str(Path(folder)/'plans'/f"{r['instance_id']}.json"),
                                      cpu_s=r.get('s') or 0, decode_s=0, chosen_sample=None, pipeline_s=r.get('s') or 0)
    return rows


def check_twostep_cache(folder, expected, finalize_s):
    """Every stored baseline row must be for an expected input at this budget, with its plan if it passed."""
    twostep = ROOT/'src/stowage/solver/twostep.py'
    import hashlib
    source = hashlib.sha256(twostep.read_bytes()).hexdigest()
    for path in Path(folder).glob('*.json'):
        row = json.loads(path.read_text())
        if (row.get('input') not in expected or row.get('finalize_s_budget') != finalize_s or row.get('twostep_py_sha256') != source
                or (row.get('passed') and not (Path(folder)/'plans'/f"{row['instance_id']}.json").exists())):
            raise SystemExit(f'two-step cache row does not belong to this run: {path}')


def jobs_for_arm(rows):
    return {i: dict(input=r['input'], result=r['result']) for i, r in rows.items() if r['outcome'] == OWN}


def jobs_for_twostep(folder):
    return jobs_for_arm(twostep_arm(folder))


def polished(rows, folder):
    """The arm after its polish step, keeping before_moves; every passing map must have a polish record."""
    result = {}
    for iid, row in rows.items():
        result[iid] = dict(row)
        if row['outcome'] == OWN:
            record = json.loads((Path(folder)/f'{iid}.json').read_text())
            if record['before'] != row['moves'] or record['after'] > record['before']:
                raise ValueError(f'polish record does not fit its incumbent: {iid}')
            result[iid].update(before_moves=record['before'], before_result=row.get('result'), moves=record['after'],
                               cpu_s=row['cpu_s']+record['polish_s'], pipeline_s=row['pipeline_s']+record['polish_s'])
            if record['after'] < record['before']:
                saved = Path(folder)/f'{iid}.result.json'
                if not saved.exists():
                    raise ValueError(f'improved plan missing: {iid}')
                result[iid]['result'] = str(saved)
    return result


def pair(left, right):
    shared = [m for m in left if left[m]['moves'] is not None and right[m]['moves'] is not None]
    only_l = sum(left[m]['moves'] is not None and right[m]['moves'] is None for m in left)
    only_r = sum(left[m]['moves'] is None and right[m]['moves'] is not None for m in left)
    fewer, more = sum(left[m]['moves'] < right[m]['moves'] for m in shared), sum(left[m]['moves'] > right[m]['moves'] for m in shared)
    mean_l = st.mean(left[m]['moves'] for m in shared) if shared else None
    mean_r = st.mean(right[m]['moves'] for m in shared) if shared else None
    return dict(only_left=only_l, only_right=only_r, passes_p=sign_p(only_l, only_r), mutual=len(shared), mean_left=mean_l, mean_right=mean_r,
                delta=mean_l/mean_r-1 if shared and mean_r else None, fewer=fewer, more=more, equal=len(shared)-fewer-more, moves_p=sign_p(fewer, more))


def passes(rows):
    return sum(r['moves'] is not None for r in rows.values())


def choose(c, ens):
    """D167: C-ens only if its passes >= C's and its mutual moves are at least 3 % lower."""
    p = pair(ens, c)
    return 'C-ens' if passes(ens) >= passes(c) and p['delta'] is not None and p['delta'] <= -.03 else 'C'


def outcome_label(val, test):
    """D158 for the small rung; val and test are (delta, passes, rival passes). Better on both, else Par, else Worse."""
    better = all(d is not None and d <= -.05 and p >= r-1 for d, p, r in (val, test))
    par = (all(d is not None and abs(d) < .05 for d, _, _ in (val, test)) and val[1] >= val[2]-2 and test[1] >= test[2]-1)
    return 'Better' if better else 'Par' if par else 'Worse'


def counts(rows):
    found = {}
    for row in rows.values():
        found[row['outcome']] = found.get(row['outcome'], 0)+1
    return found


def compute_line(rows):
    decode, cpu = [r['decode_s'] for r in rows.values()], [r['cpu_s'] for r in rows.values()]
    return f'{st.mean(decode):.0f} | {st.mean(cpu):.0f}'


def reference_arms():
    def adapter(path):
        return {r['instance_id']: dict(moves=r['weighted_moves'] if r['passed'] is True else None, outcome=OWN if r['passed'] is True else 'not passed')
                for r in json.loads(Path(path).read_text())}

    def two(folder):
        return {r['instance_id']: dict(moves=r.get('weighted_nonproductive') if r['passed'] is True else None, outcome=OWN if r['passed'] is True else r.get('outcome'))
                for r in (json.loads(p.read_text()) for p in Path(folder).glob('*.json'))}
    return ({**adapter(AUDIT82), **adapter(FINAL/'8.2/reserve25/rows.json')}, {**two(AUDIT_TWO), **two(FINAL/'twostep/reserve25')})


def table(arms):
    lines = ['| Arm | Passed | Outcomes | Mean on own passes | Decode s/map (GPU + mask) | CPU s/map (finisher, checker, polish) |', '|---|---:|---|---:|---:|---:|']
    for name, rows in arms.items():
        own = [r['moves'] for r in rows.values() if r['moves'] is not None]
        compute = compute_line(rows) if all('cpu_s' in r for r in rows.values()) else '— | —'
        lines.append(f"| {name} | {len(own)}/{len(rows)} | {counts(rows)} | {fmt(st.mean(own) if own else None)} | {compute} |")
    return lines


def pair_table(arms, pairs):
    lines = ['| Pair (A vs B) | Passes: only A / only B (sign p) | Mutual maps | Mean A vs B | Δ | Fewer / equal / more for A (sign p) |', '|---|---:|---:|---:|---:|---:|']
    for left, right in pairs:
        p = pair(arms[left], arms[right])
        lines.append(f"| {left} vs {right} | {p['only_left']} / {p['only_right']} ({p['passes_p']:.3f}) | {p['mutual']} | {fmt(p['mean_left'])} vs {fmt(p['mean_right'])} | "
                     f"{percent(p['delta'])} | {p['fewer']} / {p['equal']} / {p['more']} ({p['moves_p']:.3f}) |")
    return lines


def chosen_line(rows):
    picks = [r['chosen_sample'] for r in rows.values() if r['moves'] is not None]
    return (f"Selected candidate: greedy on {sum(p == 0 for p in picks)} of {len(picks)} passing maps, a sample on {sum(p != 0 for p in picks)}; "
            f"greedy itself passed on {sum(r.get('greedy_passed') is True for r in rows.values())}/{len(rows)}; "
            f"finisher limits hit by a candidate on {sum(bool(r.get('timeouts')) for r in rows.values())} maps.")


def validation_arms(out):
    out = Path(out)
    c, ens, two = arm(out/'c'), ensemble(out), twostep_arm(out/'twostep-plus')
    ref82, ref_two = reference_arms()
    return {'C (before polish)': c, 'C': polished(c, out/'polish-c'), 'C-ens (before polish)': ens, 'C-ens': polished(ens, out/'polish-ens'),
            'two-step+ (before polish)': two, 'two-step+': polished(two, out/'polish-twostep'), '8.2 greedy (D156)': ref82, 'two-step (D156)': ref_two}


def validation_report(out, target, binding):
    arms = validation_arms(out)
    if any(len(rows) != 55 or set(rows) != set(arms['C']) for rows in arms.values()):
        raise SystemExit('arms do not cover the same 55 maps')
    chosen = choose(arms['C'], arms['C-ens'])
    p = pair(arms['C-ens'], arms['C'])
    write(Path(out)/'choice.json', dict(chosen=chosen, c_passes=passes(arms['C']), ens_passes=passes(arms['C-ens']), mutual=p['mutual'], delta=p['delta']))
    versus = pair(arms[chosen], arms['two-step+'])
    lines = ['# Recipe C on validation (D167)', '',
             f"55 small validation maps (audit30 + reserve25); no test or showcase input. Source: the 9.1/9.2 code (D171), `source_pins` sha256 `{binding['source_pins_sha256']}`, checker epoch `{binding['checker_epoch']}`.",
             'Recipes as pre-registered in D167; `--time-limit 7200`, and no finisher call over 600 s (the pass@1 diagnostic on sample 0 is capped too). C and C-ens are after the 120 s polish.',
             "C-ens's 8.2 arm reuses the 16-sample run where that run settles the first 8 (chosen sample below 8, or no candidate passed) and is charged half its compute there, an approximation; other maps ran `--n-samples 8`.",
             'Exact two-sided sign tests on discordant maps, ties dropped, no multiple-test adjustment.', '',
             *table(arms), '', *pair_table(arms, (('C', 'two-step+'), ('C-ens', 'two-step+'), ('C-ens', 'C'), ('C', '8.2 greedy (D156)'), ('two-step+', 'two-step (D156)'))), '',
             f"C: {chosen_line(arms['C (before polish)'])}", f"C-ens: {chosen_line(arms['C-ens (before polish)'])}", '',
             f"**D167 rule:** C-ens passes {passes(arms['C-ens'])} vs C {passes(arms['C'])}, mutual moves {percent(p['delta'])} on {p['mutual']} maps: **{chosen}** runs on test.",
             f"**D158 inputs, validation:** {chosen} vs two-step+, Δ {percent(versus['delta'])}, passes {passes(arms[chosen])} vs {passes(arms['two-step+'])}. The label needs test too.", '']
    Path(target).write_text('\n'.join(lines))


def test_summary(out, choice, binding):
    out = Path(out)
    own = arm(out/'c') if choice == 'C' else ensemble(out, derive=False)
    two = twostep_arm(out/'twostep-plus')
    rung = {i: r['rung'] for i, r in own.items()}
    final, final_two = polished(own, out/'polish'), polished(two, out/'polish-twostep')
    two = {i: dict(r, rung=rung[i]) for i, r in two.items()}
    final_two = {i: dict(r, rung=rung[i]) for i, r in final_two.items()}
    if len(own) != 30 or set(own) != set(two):
        raise SystemExit('test arms do not cover the same 30 maps')
    arms = {f'StowLM recipe {choice} (before polish)': own, f'StowLM recipe {choice}': final, 'two-step+ (before polish)': two, 'two-step+': final_two}
    rival = final_two
    lines = ['| row | rung | pass | outcomes | mean lifts (pass) | paired vs two-step+ (n, Δ, better/equal/worse) | s/map |', '|---|---|---|---|---|---|---|']
    for name, rows in arms.items():
        for label in ('tiny', 'small', None):
            sel = {i: r for i, r in rows.items() if label is None or r['rung'] == label}
            ownp = [r['moves'] for r in sel.values() if r['moves'] is not None]
            p = pair(sel, {i: rival[i] for i in sel})
            gap = p['mean_left']-p['mean_right'] if p['mutual'] else None
            lines.append(f"| {name} | {label or 'all'} | {len(ownp)}/{len(sel)} | {counts(sel)} | {fmt(st.mean(ownp) if ownp else None)} | {p['mutual']}, {fmt(gap)}, "
                         f"{p['fewer']}/{p['equal']}/{p['more']} | {st.mean(r['pipeline_s'] for r in sel.values()):.2f} |")
    sign = ['| rung | mutual | StowLM mean | two-step+ mean | Δ | fewer / equal / more (StowLM) | exact sign p | passes only StowLM / only two-step+ (p) |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for label in ('tiny', 'small', None):
        left = {i: r for i, r in final.items() if label is None or r['rung'] == label}
        p = pair(left, {i: final_two[i] for i in left})
        sign.append(f"| {label or 'all'} | {p['mutual']} | {fmt(p['mean_left'])} | {fmt(p['mean_right'])} | {percent(p['delta'])} | {p['fewer']}/{p['equal']}/{p['more']} | {p['moves_p']:.5f} | {p['only_left']} / {p['only_right']} ({p['passes_p']:.3f}) |")
    val = json.loads((HERE/'val/choice.json').read_text())
    va = validation_arms(HERE/'val')
    pv = pair(va[choice], va['two-step+'])
    small_own = {i: r for i, r in final.items() if r['rung'] == 'small'}
    small_two = {i: final_two[i] for i in small_own}
    pt = pair(small_own, small_two)
    label = outcome_label((pv['delta'], passes(va[choice]), passes(va['two-step+'])), (pt['delta'], passes(small_own), passes(small_two)))
    write(out/'summary.json', dict(choice=choice, binding=binding, label=label, validation=pv, test_small=pt, arms={k: v for k, v in arms.items()}))
    text = [f'# 9.1b compute-scaled evaluation (D167): recipe {choice}', '',
            'Added after 9.1 at the user\'s direction and pre-registered in D167 before any result; test v2.1 sees StowLM a second time only for this recipe. 9.1 stays.',
            f"Source: the 9.1/9.2 code (D171), `source_pins` sha256 `{binding['source_pins_sha256']}`, checker epoch `{binding['checker_epoch']}`. Both methods: 600 s finisher, 120 s polish, full checker, no repair.", '',
            *lines, '', 'Own-plan pass counts with polish; exact two-sided sign tests exclude ties; no multiple-comparison adjustment.', '', *sign, '',
            f"**D158 re-applied (small rung, D167):** validation Δ {percent(pv['delta'])}, passes {passes(va[choice])} vs {passes(va['two-step+'])}; test Δ {percent(pt['delta'])}, passes {passes(small_own)} vs {passes(small_two)}: **{label}**.", '',
            chosen_line(own), '']
    (out/'SUMMARY.md').write_text('\n'.join(text))

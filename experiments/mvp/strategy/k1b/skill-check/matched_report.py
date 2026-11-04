"""Standard-library reporting for fresh D205 matched sample-zero trials."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from math import comb, isfinite
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
ARMS = ('8.2', 'null', 'K1r')
ORIGINAL = {'8.2': 46, 'null': 24, 'K1r': 29}


def sign_p(a, b):
    """Exact two-sided paired sign test; ties are omitted."""
    n = a + b
    return min(1.0, 2 * sum(comb(n, k) for k in range(min(a, b) + 1)) / 2**n) if n else 1.0


def number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


def valid_admission(row):
    admission = row.get('admission') or {}
    if not isinstance(admission, dict):
        return False
    source = admission.get('source_payload')
    if not isinstance(source, dict):
        return False
    for key in ('load1', 'core_mask', 't', 'read_at', 'age_s'):
        if not number(admission.get(key)):
            return False
    for key in ('load1', 'mask', 't'):
        if not number(source.get(key)):
            return False
    mask = admission['core_mask']
    if mask < 0 or mask != int(mask):
        return False
    policy = admission.get('policy', 'D204')
    if policy == 'D206':
        core_count = admission.get('core_count')
        policy_valid = (number(core_count) and core_count == int(mask).bit_count()
                        and core_count >= 8 and 0 <= admission['load1'] <= 45)
    elif policy == 'D204':
        policy_valid = mask >= 4095 and 0 <= admission['load1'] <= 30
    else:
        return False
    return (admission.get('admitted') is True and policy_valid
            and admission['t'] >= 0 and 0 <= admission['age_s'] < 120
            and abs((admission['read_at'] - admission['t']) - admission['age_s']) <= 1e-6
            and admission['load1'] == source['load1'] and admission['core_mask'] == source['mask']
            and admission['t'] == source['t'])


def pair(left, right, ids):
    compared = set(ids) & set(left) & set(right)
    own_left = {iid for iid in compared if left[iid].get('passed') is True}
    own_right = {iid for iid in compared if right[iid].get('passed') is True}
    only_left, only_right = sorted(own_left - own_right), sorted(own_right - own_left)
    mutual = sorted(own_left & own_right)
    moves = [(left[iid].get('moves'), right[iid].get('moves')) for iid in mutual]
    moves = [(a, b) for a, b in moves if number(a) and number(b)]
    fewer, more = sum(a < b for a, b in moves), sum(a > b for a, b in moves)
    a, b = (mean(x[i] for x in moves) for i in (0, 1)) if moves else (None, None)
    return dict(compared_maps=len(compared), only_left=only_left, only_right=only_right, passes_p=sign_p(len(only_left), len(only_right)),
                mutual=len(mutual), moves_available=len(moves), mean_left=a, mean_right=b,
                delta_pct=100 * (a / b - 1) if a is not None and b else None,
                fewer=fewer, equal=len(moves)-fewer-more, more=more, moves_p=sign_p(fewer, more))


def analyze(rows, expected_ids):
    ids = list(expected_ids)
    expected = set(ids)
    reasons = []
    if not ids or len(expected) != len(ids):
        reasons.append(f'Expected map list must be nonempty and unique; got {len(ids)} entries / {len(expected)} unique IDs.')
    counts = Counter((row.get('instance_id'), row.get('arm')) for row in rows)
    duplicate = [key for key, count in counts.items() if count > 1]
    if duplicate:
        reasons.append(f'{len(duplicate)} duplicate map/arm keys.')
    unexpected = [key for key in counts if key[0] not in expected or key[1] not in ARMS]
    if unexpected:
        reasons.append(f'{len(unexpected)} unexpected map/arm keys.')
    selected = {arm: {} for arm in ARMS}
    for row in rows:
        arm, iid = row.get('arm'), row.get('instance_id')
        if arm in selected and iid in expected and counts[(iid, arm)] == 1:
            selected[arm][iid] = row
    n_present = sum(len(v) for v in selected.values())
    expected_rows = len(ids) * len(ARMS)
    if n_present != expected_rows:
        reasons.append(f'{n_present}/{expected_rows} unique expected rows present.')
    incomplete = sum(row.get('complete') is not True for arm in selected.values() for row in arm.values())
    errors = sum(row.get('error') is not None for arm in selected.values() for row in arm.values())
    bad_admission = sum(row.get('eligible') is True and not valid_admission(row)
                        for arm in selected.values() for row in arm.values())
    malformed = sum(not isinstance(row.get('passed'), bool) or not isinstance(row.get('eligible'), bool)
                    or not isinstance(row.get('timed_out'), bool)
                    or (row.get('passed') is True and (row.get('eligible') is not True or not number(row.get('moves'))))
                    or (row.get('eligible') is False and (row.get('passed') is True or row.get('timed_out') is True))
                    for arm in selected.values() for row in arm.values())
    for count, label in ((incomplete, 'incomplete rows'), (errors, 'rows with process errors'),
                         (bad_admission, 'eligible rows without valid recorded admission'), (malformed, 'malformed outcome rows')):
        if count:
            reasons.append(f'{count} {label}.')
    # Invalid/error rows remain visible in coverage, but never count as observed passes.
    usable = {arm: {iid: row for iid, row in found.items()
                   if row.get('complete') is True and row.get('error') is None}
              for arm, found in selected.items()}
    arms = {}
    for arm, found in usable.items():
        passed = [row for row in found.values() if row.get('passed') is True]
        costs = [row['moves'] for row in passed if number(row.get('moves'))]
        arms[arm] = dict(rows=len(selected[arm]), complete=len(found), passed=len(passed),
                         eligible=sum(row.get('eligible') is True for row in found.values()),
                         timed_out=sum(row.get('timed_out') is True for row in found.values()),
                         mean_own=mean(costs) if costs else None)
    comparisons = {f'{a} vs {b}': pair(usable[a], usable[b], expected)
                   for a, b in (('8.2', 'null'), ('8.2', 'K1r'), ('null', 'K1r'))}
    strata = {}
    for arm, found in usable.items():
        for row in found.values():
            admission = row.get('admission')
            policy = (str(admission.get('policy', 'D204')) if isinstance(admission, dict)
                      else 'Invalid admission')
            if row.get('eligible') is not True:
                policy = 'No judge (ineligible)'
            counts = strata.setdefault(policy, {}).setdefault(arm, dict(rows=0, passed=0, timed_out=0))
            counts['rows'] += 1
            counts['passed'] += row.get('passed') is True
            counts['timed_out'] += row.get('timed_out') is True
    return dict(definitive=not reasons, reasons=reasons, expected_maps=len(expected), rows=n_present,
                arms=arms, comparisons=comparisons, admission_strata=strata)


def conclusion(summary):
    if not summary.get('planned_cohort_definitive', summary['definitive']):
        return '**The matched first-try advantage is not settled.** The cohort is incomplete or invalid; partial counts cannot establish whether the original advantage is real, smaller, or gone.'
    gaps = [summary['arms']['8.2']['passed'] - summary['arms'][b]['passed'] for b in ('null', 'K1r')]
    significant = [summary['comparisons'][f'8.2 vs {b}']['passes_p'] < .05 for b in ('null', 'K1r')]
    if summary['expected_maps'] != 55:
        n = summary['expected_maps']
        if all(gap > 0 for gap in gaps):
            statement = f'On the planned {n}-map cohort, 8.2 leads null by {gaps[0]} maps and random by {gaps[1]} maps.'
            evidence = ('Both paired exact tests establish a lead at p < 0.05.' if all(significant)
                        else 'The descriptive lead is not statistically established against both arms at 0.05.')
        elif all(gap <= 0 for gap in gaps):
            statement = f'On the planned {n}-map cohort, 8.2 has no pass lead over either comparison arm.'
            evidence = 'This does not prove equal underlying performance.'
        else:
            statement = f'On the planned {n}-map cohort, the first-try pass comparison is mixed.'
            evidence = 'Read the paired comparisons separately.'
        return f'**{statement}** {evidence} This fixed subset does not settle the full 55-map advantage or its size relative to the original unmatched cohort. Exact tests are two-sided, ties dropped, without multiplicity adjustment.'
    if all(gap > 0 for gap in gaps):
        size = 'smaller' if any(gap < original for gap, original in zip(gaps, (22, 17))) else 'retained'
        statement = f'The first-try pass advantage is {size} under the matched judge: 8.2 leads null by {gaps[0]} maps and random by {gaps[1]} maps (originally 22 and 17).'
        evidence = ('The advantage is real in this matched validation cohort by both paired exact tests (p < 0.05).' if all(significant)
                    else 'The lead is not statistically established against both arms at 0.05.')
    elif all(gap <= 0 for gap in gaps):
        statement = 'The first-try pass advantage is gone under the matched judge: 8.2 does not lead either comparison arm.'
        evidence = 'This does not prove equal underlying performance.'
    else:
        statement = 'The original first-try pass advantage is gone as a consistent advantage across both arms; the matched result is mixed.'
        evidence = 'Read the two paired comparisons separately.'
    return f'**{statement}** {evidence} These are development results on validation maps; exact tests are two-sided, ties dropped, without multiplicity adjustment.'


def fmt(value):
    return '—' if value is None else f'{value:.2f}'


def pvalue(value):
    return '< 0.0001' if value < .0001 else f'{value:.4f}'


def render(rows, expected_ids, status=None, policy_history=None, stop_plan=None):
    summary = analyze(rows, expected_ids)
    n = summary['expected_maps']
    lines = ['# Part 2: fresh matched sample-zero judge', '', conclusion(summary), '',
             'Unchanged K1b V4 finisher at 600 s, full checker and layered mask replay. The three sample-zero plans for a map share one admission cohort; previous judge rows are not reused. Ineligible samples have no complete plan and fail without running the finisher.', '',
             f'Coverage: {summary["rows"]}/{n * len(ARMS)} unique expected rows. ' + ('Complete and admission-valid.' if summary['definitive'] else 'Partial or blocked; figures below are provisional.'), '']
    if stop_plan:
        lines += [f'D213 planned scope: first {n} of 55 maps in fixed manifest order ({n * len(ARMS)} rows), capped before inspecting outcomes. Outcomes outside the planned map IDs are not read. Full 55-map inference remains unavailable.', '',
                  'Stop-plan record: `' + json.dumps(stop_plan, sort_keys=True).replace('`', "'") + '`', '']
    elif n != 55:
        lines += [f'Planned scope: {n} of 55 maps; denominators below refer to this subset. Full 55-map inference remains unavailable.', '']
    if summary['reasons']:
        lines += ['Validation blockers: ' + ' '.join(summary['reasons']), '']
    if status:
        lines += ['Controller status: `' + json.dumps(status, sort_keys=True).replace('`', "'") + '`', '']
    lines += ['Admission policy: historical unversioned rows retain D204 (numeric mask ≥ 4095, load1 ≤ 30); new D206 rows require mask popcount ≥ 8 and load1 ≤ 45. Both require nonfuture leader evidence younger than 120 s, recorded per trial. The policy change preserves completed rows; combined counts span these admission windows. Trials run at normal priority.', '']
    if policy_history:
        lines += ['Recorded policy history (UTC timestamps retained verbatim):', '']
        for event in policy_history:
            lines += ['- `' + json.dumps(event, sort_keys=True).replace('`', "'") + '`']
        lines += ['']
    lines += ['| Admission policy | Arm | Complete, error-free rows | Passed | Timed out |',
              '|---|---|---:|---:|---:|']
    for policy, arms in sorted(summary['admission_strata'].items()):
        for arm in ARMS:
            if arm in arms:
                count = arms[arm]
                lines += [f'| {policy} | {arm} | {count["rows"]} | {count["passed"]} | {count["timed_out"]} |']
    lines += ['']
    lines += [f'| Arm | Rows | Complete, error-free | Passed / {n} | Eligible | Timed out | Mean moves on own passes |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for arm in ARMS:
        a = summary['arms'][arm]
        outcomes = (f'{a["passed"]}/{n}', str(a['eligible']), str(a['timed_out'])) if a['complete'] else ('—', '—', '—')
        lines.append(f'| {arm} | {a["rows"]} | {a["complete"]} | {outcomes[0]} | {outcomes[1]} | {outcomes[2]} | {fmt(a["mean_own"])} |')
    lines += ['', 'On incomplete cohorts, an absent row is unobserved, not a judged failure; pairwise counts are descriptive only.', '',
              '| A vs B | Paired observed maps | Only A / only B | Pass sign p | Mutual passes | Mean moves A / B | Δ A vs B | Fewer / equal / more | Move sign p |',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name, p in summary['comparisons'].items():
        if not p['compared_maps']:
            lines.append(f'| {name} | 0 | — | — | — | — | — | — | — |')
            continue
        delta = '—' if p['delta_pct'] is None else f'{p["delta_pct"]:+.1f}%'
        lines.append(f'| {name} | {p["compared_maps"]} | {len(p["only_left"])} / {len(p["only_right"])} | {pvalue(p["passes_p"])} | {p["mutual"]} | {fmt(p["mean_left"])} / {fmt(p["mean_right"])} | {delta} | {p["fewer"]} / {p["equal"]} / {p["more"]} | {pvalue(p["moves_p"])} |')
    lines += ['', 'Original unmatched sample-zero passes: 8.2 46/55, null 24/55, random 29/55. Timeouts above use the unchanged judge’s recorded `timed_out` field.', '']
    return '\n'.join(lines)


def read_ids(path):
    text = Path(path).read_text()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text.split()
    if isinstance(value, dict):
        for key in ('expected_ids', 'instance_ids', 'maps', 'jobs'):
            if key in value:
                value = value[key]
                break
    if not isinstance(value, list):
        raise ValueError('Expected a JSON ID list or maps/jobs list containing instance_id.')
    return [row['instance_id'] if isinstance(row, dict) else row for row in value]


def read_status(root=HERE):
    """Follow the current generation without trusting an out-of-tree pointer."""
    root = Path(root).resolve()
    matched_root = (root/'matched').resolve()
    status_path = matched_root/'status.json'
    active_path = matched_root/'active-controller.json'
    if active_path.exists():
        active = json.loads(active_path.read_text())
        pointer = active.get('status_path')
        if pointer is not None:
            if not isinstance(pointer, str) or not pointer:
                raise ValueError('active-controller status_path must be a nonempty path.')
            candidate = Path(pointer)
            candidate = (candidate if candidate.is_absolute() else root/candidate).resolve()
            if not candidate.is_relative_to(matched_root):
                raise ValueError('active-controller status_path must resolve under matched/.')
            status_path = candidate
    return json.loads(status_path.read_text()) if status_path.exists() else None


def planned_ids(ids, root=HERE, manifest_path=None):
    """Apply the preregistered cap before reading any outcome files."""
    path = Path(root)/'matched/stop-plan.json'
    if not path.exists():
        return ids, None
    plan = json.loads(path.read_text())
    if (plan.get('decision') != 'D213' or plan.get('map_limit') != 20
            or len(ids) != 55 or plan.get('instance_ids') != ids[:20]):
        raise ValueError('D213 stop plan must contain exactly the first 20 of 55 manifest map IDs.')
    if 'manifest_sha256' in plan:
        import hashlib
        manifest = Path(manifest_path) if manifest_path is not None else Path(root)/'matched/manifest.json'
        if hashlib.sha256(manifest.read_bytes()).hexdigest() != plan['manifest_sha256']:
            raise ValueError('D213 stop plan manifest_sha256 does not match the manifest.')
    return ids[:20], plan


def read_rows(ids, root=HERE):
    rows = []
    for iid in ids:
        for path in sorted((Path(root)/'matched/rows'/iid).glob('*.json')):
            rows.append(json.loads(path.read_text()))
    return rows


def exported_summary(summary):
    """Keep cached pre-D213 publishers from treating 20 maps as a full cohort."""
    result = dict(summary)
    if summary['expected_maps'] != 55:
        result['planned_cohort_definitive'] = summary['definitive']
        result['definitive'] = False
        result['definitive_scope_note'] = ('Top-level definitive is reserved for the full 55-map cohort for compatibility '
                                           'with cached publishers. planned_cohort_definitive describes only the '
                                           f'{summary["expected_maps"]}-map planned cohort.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-ids', type=Path, default=HERE/'matched/manifest.json')
    args = parser.parse_args()
    ids, stop_plan = planned_ids(read_ids(args.expected_ids), manifest_path=args.expected_ids)
    rows = read_rows(ids)
    status = read_status()
    history_path = HERE/'matched/admission-policy-history.jsonl'
    history = [json.loads(line) for line in history_path.read_text().splitlines() if line.strip()] if history_path.exists() else []
    summary = analyze(rows, ids)
    summary['status'] = status
    summary['admission_policy_history'] = history
    summary['stop_plan'] = stop_plan
    (HERE/'matched-summary.json').write_text(json.dumps(exported_summary(summary), indent=2, sort_keys=True)+'\n')
    (HERE/'part2.md').write_text(render(rows, ids, status, history, stop_plan))


if __name__ == '__main__':
    main()

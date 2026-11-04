"""Size/port-stratified, paired full-check results; never pool different sizes."""
from __future__ import annotations
import json
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
ARMS = ('frontier09', 'engine', 'graph')


def average(rows, key):
    values = [r[key] for r in rows if r.get(key) is not None]
    return mean(values) if values else None


def restows(row):
    parts = row.get('parts') or {}
    return parts.get('restow_now', 0) + parts.get('restow_later', 0)


def recorded_replay(row):
    if row.get('replay_legal_complete'):
        return True
    # The copied scout ladder records lifts only after Problem.score succeeds,
    # even when its subsequent full-check filter returns no engine candidate.
    def ladder_evidence(value):
        if isinstance(value, dict):
            if 'lp_uncovered' in value and value.get('lifts') is not None:
                return True
            return any(ladder_evidence(v) for v in value.values())
        return isinstance(value, list) and any(ladder_evidence(v) for v in value)
    return row.get('arm') == 'engine' and ladder_evidence(row.get('method_metadata', {}))


def summarize(output):
    output = Path(output)
    rows = {arm: {r['instance_id']: r for p in (output / arm).glob('*.json')
                  if (r := json.loads(p.read_text())).get('complete')} for arm in ARMS}
    manifest = {r['instance_id']: r for r in json.loads((HERE / 'cohort-manifest.json').read_text())['maps']}
    for arm, values in rows.items():
        for identity, row in values.items():
            frozen = manifest[identity]
            if (row['input_sha256'], row['input'], row['size'], row['ports']) != (
                    frozen['sha256'], frozen['path'], frozen['size'], frozen['ports']):
                raise ValueError(f'{arm}: stale input or stratum for {identity}')
        for key in ('implementation_sha256', 'native_sha256', 'params_sha256', 'runner_sha256', 'cohort_sha256'):
            if len({r['config'][key] for r in values.values()}) > 1:
                raise ValueError(f'{arm}: mixed {key}; move old rows before resuming')
    for key in ('native_sha256', 'params_sha256', 'runner_sha256', 'cohort_sha256'):
        if len({r['config'][key] for values in rows.values() for r in values.values()}) > 1:
            raise ValueError(f'arms use different {key}')
    groups = sorted({(r['size'], r['ports']) for arm in ARMS for r in rows[arm].values()},
                    key=lambda s: ({'tiny': 0, 'small': 1, 'medium': 2}[s[0]], s[1]))
    summary = {'by_size_ports': [], 'gate': 'unchanged score.evaluate independent full checker',
               'means': 'triple-paired means use maps passing all three arms; pairwise means use the named two arms; unpaired pass means use each arm\'s own full passes'}
    policy = output / 'measurement-policy.json'
    if policy.exists():
        summary['resource_policy'] = json.loads(policy.read_text())
    lines = ['| Size | Ports | Measured 09/E/G | Known replay 09/E/G | Pass 09/E/G | Timeouts 09/E/G | Pairs | Paired lifts 09/E/G | Paired restow lifts 09/E/G | Mean seconds 09/E/G |',
             '|---|---:|---|---|---|---|---:|---|---|---|']
    unpaired_lines = ['**Unpaired passing-plan means.** Each arm uses its own full-check passes; these columns can describe different maps and are not paired comparisons.', '',
        '| Size | Ports | Pass counts 09/E/G | Unpaired mean lifts 09/E/G | Unpaired mean restow lifts 09/E/G | Timeouts 09/E/G |',
        '|---|---:|---|---|---|---|']
    pairwise_lines = ['Pairwise means use only maps fully passed by both named arms, within the stated size and port count. Mean change is right minus left; lower lifts are better.', '',
        '| Size | Ports | Left / Right | Pairs | Mean lifts left / right | Mean restow lifts left / right | Mean change |',
        '|---|---:|---|---:|---|---|---:|']
    labels = {'frontier09': '09', 'engine': 'E', 'graph': 'G'}
    def number(value):
        return '—' if value is None else f'{value:.2f}'
    for size, ports in groups:
        selected = {arm: {k: r for k, r in rows[arm].items() if (r['size'], r['ports']) == (size, ports)} for arm in ARMS}
        mutual = set.intersection(*(set(k for k, r in selected[a].items() if r['passed']) for a in ARMS))
        group = dict(size=size, ports=ports, mutual_passes=len(mutual), mutual_ids=sorted(mutual), arms={})
        for arm in ARMS:
            values = list(selected[arm].values())
            passed = [r for r in values if r['passed']]
            paired = [selected[arm][k] for k in mutual]
            group['arms'][arm] = dict(measured=len(values), replay_legal=sum(recorded_replay(r) for r in values),
                timeouts=sum(bool(r.get('outer_timeout')) for r in values),
                replay_unknown=sum(r.get('replay_legal_known') is False for r in values),
                passes=len(passed), pass_mean_lifts=average(passed, 'lifts'), paired_mean_lifts=average(paired, 'lifts'),
                pass_mean_restow_lifts=mean([restows(r) for r in passed]) if passed else None,
                paired_mean_restow_lifts=mean([restows(r) for r in paired]) if paired else None,
                mean_wall_s=average(values, 'wall_s'), failed_ids=[r['instance_id'] for r in values if not r['passed']])
        group['pairwise'] = {}
        for left, right in (('frontier09', 'engine'), ('frontier09', 'graph'), ('engine', 'graph')):
            paired_ids = sorted(k for k in set(selected[left]) & set(selected[right])
                                if selected[left][k]['passed'] and selected[right][k]['passed'])
            group['pairwise'][f'{left}/{right}'] = dict(ids=paired_ids, pairs=len(paired_ids),
                mean_lifts={a: average([selected[a][k] for k in paired_ids], 'lifts') for a in (left, right)},
                mean_restow_lifts={a: mean([restows(selected[a][k]) for k in paired_ids]) if paired_ids else None
                                   for a in (left, right)},
                mean_change=mean([selected[right][k]['lifts'] - selected[left][k]['lifts'] for k in paired_ids]) if paired_ids else None)
            pair = group['pairwise'][f'{left}/{right}']
            lift_values = ' / '.join(number(pair['mean_lifts'][a]) for a in (left, right))
            restow_values = ' / '.join(number(pair['mean_restow_lifts'][a]) for a in (left, right))
            pairwise_lines.append(f'| {size} | {ports} | {labels[left]} / {labels[right]} | {len(paired_ids)} | {lift_values} | {restow_values} | {number(pair["mean_change"])} |')
        summary['by_size_ports'].append(group)
        def vals(key):
            return '/'.join('—' if group['arms'][a][key] is None else f'{group["arms"][a][key]:.2f}'
                            if isinstance(group['arms'][a][key], float) else str(group['arms'][a][key]) for a in ARMS)
        lines.append(f'| {size} | {ports} | {vals("measured")} | {vals("replay_legal")} | {vals("passes")} | {vals("timeouts")} | {len(mutual)} | {vals("paired_mean_lifts")} | {vals("paired_mean_restow_lifts")} | {vals("mean_wall_s")} |')
        unpaired_lines.append(f'| {size} | {ports} | {vals("passes")} | {vals("pass_mean_lifts")} | {vals("pass_mean_restow_lifts")} | {vals("timeouts")} |')
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (output / 'table.md').write_text('\n'.join(lines) + '\n\n' + '\n'.join(unpaired_lines) + '\n')
    (output / 'pairwise-table.md').write_text('\n'.join(pairwise_lines) + '\n')
    return summary


if __name__ == '__main__':
    import sys
    summarize(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / 'results/main')

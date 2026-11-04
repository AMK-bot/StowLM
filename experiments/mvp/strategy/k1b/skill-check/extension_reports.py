"""Stdlib-only renderers for D205 extensions; never run physics or write files."""
from collections import Counter, defaultdict
from itertools import combinations
from math import comb
import hashlib
import json

MEDIUM_ARMS = ('8.2 K2', 'random K2r', 'two-step', 'block-rule')
RULE_ARMS = ('8.2 rule', 'random rule')


def _exact(a, b):
    n = a + b
    return min(1., 2 * sum(comb(n, i) for i in range(min(a, b) + 1)) / 2 ** n) if n else 1.


def _p(a, b):
    return f'{_exact(a, b):.4g}'


def _check(row, field):
    return (row.get('checks') or {}).get(field, row.get(field)) is True


def _good(row):
    return not row.get('error')


def _rate(n, d):
    return f'{n}/{d} ({n/d:.1%})' if d else 'unavailable'


def _range(values):
    values = sorted(set(values))
    return str(values[0]) if len(values) == 1 else f'{values[0]}–{values[-1]}' if values else '—'


def _identity(row):
    return row.get('map_id', row.get('instance_id'))


def _cell(row):
    return row['ship'], row['fill_target'], row['mass_level']


def _cell_label(cell):
    return f'{cell[0]} × {cell[1]}% × {cell[2]}' if cell else 'all ladder cells'


def _key(row):
    return row['arm'], _identity(row), row.get('sample'), row.get('source')


def _unique(rows, key):
    result = {}
    for row in rows:
        k = key(row)
        if k in result:
            raise ValueError(f'Duplicate audit row: {k}')
        result[k] = row
    return result


def render_medium(inv, rows):
    """Render available-plan rates and map-paired medium comparisons.

    A map enters either comparison only when every available candidate for both
    arms has an audit row. Errors never become gate failures. Primary selection
    uses minimum compacted cost, then sample index; portfolios are secondary.
    """
    rows = [r for r in rows if r.get('arm') in MEDIUM_ARMS]
    indexed = _unique(rows, _key)
    jobs = inv['jobs']
    expected = {_key(j) for j in jobs}
    if not set(indexed) <= expected:
        raise ValueError('Medium rows are not bound to the supplied inventory')
    cells = sorted({_cell(m) for m in inv['declared_maps']})
    lines = ['## Part 3: medium TRAIN ladder, default tanks', '',
             'This is a deterministic pre-finisher audit of the same 18 TRAIN ladder maps: '
             '40% and 60% target fill, low mass, three ships, three declared maps per cell. '
             'Box counts mean load-list boxes, not TEU. Sight/lashing clear is a gate result, '
             'not a finished-plan pass. Cells have at most three maps; multiple samples on a '
             'map are not independent maps. Small cells do not establish generalisation.', '',
             'Rates below use error-free audited complete plans only. Availability is frozen '
             'by the inventory; missing plans are unavailable, rather than gate failures. '
             'Pending rows and audit errors remain visible. Pooled counts are descriptive '
             'because ships, fills, and numbers of stored plans differ.', '',
             '| Arm | Available plans / maps | Audited / available plans | Errors | Sight/lashing clear / audited | All checks / audited | Available boxes/map |',
             '|---|---:|---:|---:|---:|---:|---:|']

    def counts(arm, cell=None):
        available = [j for j in jobs if j['arm'] == arm and (cell is None or _cell(j) == cell)]
        observed = [indexed[_key(j)] for j in available if _key(j) in indexed]
        good = [r for r in observed if _good(r)]
        clear = sum(_check(r, 'sight_clear') for r in good)
        allclear = sum(_check(r, 'all_clear') for r in good)
        return available, observed, good, clear, allclear

    for arm in MEDIUM_ARMS:
        available, observed, good, clear, allclear = counts(arm)
        lines.append(f'| {arm} | {len(available)} / {len({_identity(j) for j in available})} | '
                     f'{len(good)}/{len(available)} | {len(observed)-len(good)} | {_rate(clear,len(good))} | '
                     f'{_rate(allclear,len(good))} | {_range(j["n_loads"] for j in available)} |')
    lines += ['', '| Ship × target fill × mass | Arm | Declared maps | Available plans / maps | Audited / available plans | Errors | Sight/lashing clear / audited | All checks / audited | Available boxes/map |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for cell in cells:
        declared = sum(_cell(m) == cell for m in inv['declared_maps'])
        for arm in MEDIUM_ARMS:
            available, observed, good, clear, allclear = counts(arm, cell)
            lines.append(f'| {_cell_label(cell)} | {arm} | {declared} | '
                         f'{len(available)} / {len({_identity(j) for j in available})} | '
                         f'{len(good)}/{len(available)} | {len(observed)-len(good)} | '
                         f'{_rate(clear,len(good))} | {_rate(allclear,len(good))} | '
                         f'{_range(j["n_loads"] for j in available)} |')

    byarm = {arm: defaultdict(list) for arm in MEDIUM_ARMS}
    for job in jobs:
        byarm[job['arm']][_identity(job)].append(job)

    def ready(arm, mid):
        return all(_key(j) in indexed for j in byarm[arm][mid])

    def selected(arm, mid):
        candidates = [indexed[_key(j)] for j in byarm[arm][mid]]
        if any(r.get('cost') is None for r in candidates):
            return None
        return min(candidates, key=lambda r: (r['cost'], r.get('sample') if r.get('sample') is not None else -1,
                                               r.get('source', '')))

    lines += ['', '### Paired maps: cheapest compacted stored candidate', '',
              'Primary selection takes the cheapest compacted candidate in each arm/map, '
              'breaking cost ties by sample index. It does not select on gate clearance. '
              'All available candidates must have audit rows before a map is compared; '
              'a selected candidate with an error or missing cost remains unresolved. '
              'Exact tests are two-sided binomial sign tests on discordant maps. '
              'No candidate-level independence test is used. This is selected-plan evidence '
              'from unequal candidate portfolios, not a matched sample-0 experiment.', '',
              '| Cell | A / B | Shared maps with plans | Compared maps | A clear / B clear | Only A / only B | Exact p | Unresolved maps |',
              '|---|---|---:|---:|---:|---:|---:|---:|']
    comparisons = []
    for a, b in combinations(MEDIUM_ARMS, 2):
        common = set(byarm[a]) & set(byarm[b])
        for cell in [None, *cells]:
            shared = sorted(mid for mid in common if cell is None or _cell(byarm[a][mid][0]) == cell)
            if not shared:
                if cell is None:
                    lines.append(f'| {_cell_label(cell)} | {a} / {b} | 0 | 0 | unavailable | — | — | 0 |')
                continue
            pairs = []
            portfolios = []
            for mid in shared:
                if not ready(a, mid) or not ready(b, mid):
                    continue
                left, right = selected(a, mid), selected(b, mid)
                if left is not None and right is not None and _good(left) and _good(right):
                    pairs.append((_check(left, 'sight_clear'), _check(right, 'sight_clear')))
                lr = [indexed[_key(j)] for j in byarm[a][mid]]
                rr = [indexed[_key(j)] for j in byarm[b][mid]]
                if all(_good(r) for r in lr + rr):
                    portfolios.append((any(_check(r, 'sight_clear') for r in lr),
                                       any(_check(r, 'sight_clear') for r in rr)))
            ca, cb = sum(x for x,y in pairs), sum(y for x,y in pairs)
            oa, ob = sum(x and not y for x,y in pairs), sum(y and not x for x,y in pairs)
            lines.append(f'| {_cell_label(cell)} | {a} / {b} | {len(shared)} | {len(pairs)} | '
                         f'{ca} / {cb}' + ('' if pairs else ' (unavailable)') +
                         f' | {oa} / {ob} | {_p(oa,ob) if pairs else "—"} | {len(shared)-len(pairs)} |')
            comparisons.append((cell, a, b, shared, portfolios))
    lines += ['', '### Secondary: any-clear stored-plan portfolio', '',
              'A map clears here if any stored plan clears. Candidate counts are unequal; '
              'this measures the available portfolio and must not be read as per-sample skill. '
              'Only shared maps with all available candidate audits error-free enter. '
              'These are map-level descriptive comparisons; no extra significance test is applied.', '',
              '| Cell | A / B | Shared maps | Fully audited maps | Any-clear A / B | Only A / only B |',
              '|---|---|---:|---:|---:|---:|']
    for cell, a, b, shared, pairs in comparisons:
        ca, cb = sum(x for x,y in pairs), sum(y for x,y in pairs)
        oa, ob = sum(x and not y for x,y in pairs), sum(y and not x for x,y in pairs)
        lines.append(f'| {_cell_label(cell)} | {a} / {b} | {len(shared)} | {len(pairs)} | '
                     f'{ca} / {cb}' + ('' if pairs else ' (unavailable)') + f' | {oa} / {ob} |')
    lines += ['', '### Stored-plan provenance and audit completeness', '',
              '| Arm | Stage | Available plans |', '|---|---|---:|']
    stages = Counter((j['arm'], j['stage']) for j in jobs)
    for (arm, stage), n in sorted(stages.items()):
        lines.append(f'| {arm} | {stage} | {n} |')
    lines += ['', '| Cell | Arm | Audited plans with sequence signature | Distinct chosen move sequences |',
              '|---|---|---:|---:|']
    for cell in [None, *cells]:
        for arm in MEDIUM_ARMS:
            available, observed, good, _, _ = counts(arm, cell)
            signatures = [r.get('signature') or r.get('compactsignature') for r in good]
            signatures = [sig for sig in signatures if sig is not None]
            lines.append(f'| {_cell_label(cell)} | {arm} | {len(signatures)}/{len(available)} | '
                         f'{len(set(signatures)) if signatures else "unavailable"} |')
    lines += ['', 'Repeated decoded plans can share the same chosen move sequence; '
              'distinct-sequence counts use audited compacted-plan signatures only. '
              'They are not counts of independent maps or necessarily distinct final stows. '
              'Unjudged plans have unknown signatures.']
    digest = hashlib.sha256(json.dumps(inv['snapshot_hashes'], sort_keys=True).encode()).hexdigest()
    lines += ['', f'Inventory snapshot: `{inv.get("created_at", "unknown")}`; '
              f'{len(inv["snapshot_hashes"])} source/input digests. '
              f'SHA256 of the sorted JSON digest mapping: `{digest}`. '
              'The complete binding and per-plan source paths are in `skill-check/extensions/medium-inventory.json`.']
    for path, sha in inv['snapshot_hashes'].items():
        if path.endswith('/cohort.json') or path.endswith('/k2/manifest.json'):
            lines.append(f'\n`{path}` SHA256 `{sha}`.')
    if inv.get('warnings'):
        lines += ['', *[f'- {str(w).replace(chr(10), " ")}' for w in inv['warnings']]]
    errors = [r for r in rows if not _good(r)]
    lines += ['', f'Audit rows: {len(rows)}/{len(jobs)}; pending: {len(jobs)-len(rows)}; errors: {len(errors)}.']
    if errors:
        lines += ['', '| Arm | Map | Sample | Error |', '|---|---|---:|---|']
        for r in errors:
            error = str(r['error']).replace('|', '\\|').replace('\n', ' ')
            lines.append(f'| {r["arm"]} | {_identity(r)} | {r.get("sample", "—")} | {error} |')
    return '\n'.join(lines) + '\n'


def render_rule(rows):
    """Rule-masked sample 0, including explicit missing-plan rows in all-map N."""
    zero = [r for r in rows if r.get('arm') in RULE_ARMS and r.get('sample') == 0]
    indexed = _unique(zero, lambda r: (r['arm'], _identity(r)))
    lines = ['## Part 1 extension: rule-masked sample 0', '',
             'These are rule-masked arms on their own 55-map cohort; their results are '
             'reported separately from the main three-arm cohort. 8.2 sample 0 is greedy; '
             'random sample 0 is random. Default tanks and the same deterministic gate '
             'apply. Explicit missing-plan rows count as failures in all-55 denominators; '
             'audit errors and missing audit rows remain pending.', '',
             '| Arm | Audit rows / 55 | Errors | Complete | Sight/lashing clear | Clear / complete | All checks | Status |',
             '|---|---:|---:|---:|---:|---:|---:|---|']
    for arm in RULE_ARMS:
        subset = [r for r in zero if r['arm'] == arm]
        good = [r for r in subset if _good(r)]
        complete = sum(r.get('complete') is True for r in good)
        clear = sum(_check(r, 'sight_clear') for r in good)
        allclear = sum(_check(r, 'all_clear') for r in good)
        final = len(good) == 55
        denominator = 55 if final else len(good)
        lines.append(f'| {arm} | {len(subset)}/55 | {len(subset)-len(good)} | '
                     f'{complete}/{denominator} | {_rate(clear,denominator)} | '
                     f'{_rate(clear,complete)} | {allclear}/{denominator} | '
                     f'{"complete" if final else "pending; observed coverage only"} |')
    a, b = RULE_ARMS
    left = {_identity(r):r for r in zero if r['arm'] == a}
    right = {_identity(r):r for r in zero if r['arm'] == b}
    shared = sorted(set(left) & set(right))
    good = [mid for mid in shared if _good(left[mid]) and _good(right[mid])]
    lines += ['', '| A / B | Field | Paired maps / 55 | Only A / only B | Exact paired p | Status |',
              '|---|---|---:|---:|---:|---|']
    for field in ('complete', 'sight_clear', 'all_clear'):
        get = (lambda r: r.get('complete') is True) if field == 'complete' else (lambda r: _check(r, field))
        oa = sum(get(left[m]) and not get(right[m]) for m in good)
        ob = sum(get(right[m]) and not get(left[m]) for m in good)
        final = len(good) == 55
        lines.append(f'| {a} / {b} | {field} | {len(good)}/55 | {oa} / {ob} | '
                     f'{_p(oa,ob) if final else "pending"} | '
                     f'{"complete" if final else "pending; counts are observed pairs only"} |')
    lines += ['', 'Exact p uses the two-sided binomial sign test on discordant maps. '
              'No sample-level independence assumption is used.']
    return '\n'.join(lines) + '\n'


def render_curves(rows):
    """Per-index descriptive curves for unrestricted and rule-masked 8.2."""
    arms = ('8.2', '8.2 rule')
    subset = [r for r in rows if r.get('arm') in arms and r.get('sample') in range(16)]
    _unique(subset, lambda r: (r['arm'], _identity(r), r['sample']))
    lines = ['## Part 1 extension: 8.2 gate clearance by sample index', '',
             'Sample 0 is greedy; indices 1–15 use T=0.7. Each index expects 55 maps '
             'within its arm/cohort. Incomplete coverage is marked pending and only '
             'observed counts are shown; these are not final all-55 rates. Missing-plan '
             'rows are failures, while audit errors remain unresolved. The stochastic '
             'pool is descriptive because samples repeat the same maps.', '',
             '| Arm | Index / regime | Rows / expected | Errors | Complete | Sight/lashing clear | Clear / complete | All checks | Status |',
             '|---|---|---:|---:|---:|---:|---:|---:|---|']
    for arm in arms:
        groups = [(str(i) + (' greedy' if i == 0 else ' T=0.7'),
                   [r for r in subset if r['arm'] == arm and r['sample'] == i], 55) for i in range(16)]
        groups.append(('T=0.7 pooled (1–15)', [r for r in subset if r['arm'] == arm and r['sample'] > 0], 825))
        for label, observed, expected in groups:
            good = [r for r in observed if _good(r)]
            complete = sum(r.get('complete') is True for r in good)
            clear = sum(_check(r, 'sight_clear') for r in good)
            allclear = sum(_check(r, 'all_clear') for r in good)
            final = len(good) == expected
            denominator = expected if final else len(good)
            lines.append(f'| {arm} | {label} | {len(observed)}/{expected} | {len(observed)-len(good)} | '
                         f'{complete}/{denominator} | {_rate(clear,denominator)} | {_rate(clear,complete)} | '
                         f'{allclear}/{denominator} | {"complete" if final else "pending; observed coverage only"} |')
    return '\n'.join(lines) + '\n'

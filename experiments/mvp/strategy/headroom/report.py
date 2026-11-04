#!/usr/bin/env python3
"""Render HEADROOM.md using full-checker-certified move numbers only."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text())


def checked_moves(row):
    check = (row or {}).get('check') or {}
    value = check.get('moves')
    if check.get('passed') is True and isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return None


def actual_attempts(row):
    """Shared certificates certify another layout without repeating compute."""
    def walk(check):
        if not check:
            return []
        previous = walk(check.get('previous_own_attempt'))
        return previous if check.get('checker_attempted') is False else [*previous, check]
    return walk((row or {}).get('check'))


def sign_test(wins, losses):
    n = wins + losses
    if not n:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(wins, losses) + 1)) / 2 ** n)


def fmt(value):
    if value is None:
        return '—'
    return f'{value:g}'


def status(row):
    if not row:
        return 'pending'
    moves = checked_moves(row)
    if moves is not None:
        return f'PASS {fmt(moves)}'
    check = row.get('check')
    if check is not None:
        return 'FAIL ' + str(check.get('finalize_status', 'checker'))
    if row.get('solver_complete'):
        return 'awaiting check'
    return str(row.get('status', 'pending'))


def quantile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    pos = (len(values) - 1) * fraction
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def fraction(numerator, denominator):
    return f'{numerator}/{denominator} ({100*numerator/denominator:.1f}%)' if denominator else '0/0'


def summarize(manifest, out):
    records = []
    for item in manifest['maps']:
        path = out / 'maps' / item['map_id'] / 'summary.json'
        record = load(path) if path.is_file() else {}
        # Old/misrouted result folders must never silently enter this study.
        if record and record.get('input') != item['input']:
            raise ValueError(f"summary/input mismatch for {item['map_id']}")
        records.append((item, record))
    return records


def render(manifest, out):
    records = summarize(manifest, out)
    rows = [(item, result, [result.get('baseline'), *result.get('layouts', [])]) for item, result in records]
    all_candidates = [row for _, _, candidates in rows for row in candidates if row]
    restricted = [row for row in all_candidates if row.get('kind') != 'default']
    n_complete = sum(bool(result.get('complete')) for _, result in records)
    n_started = sum(bool(result) for _, result in records)
    baseline_passes = sum(checked_moves(result.get('baseline')) is not None for _, result in records)
    gains, ties, losses, deltas = 0, 0, 0, []
    incumbent_passes = 0
    restricted_passes = 0
    table = []
    rescues = []
    for item, result, candidates in rows:
        baseline = result.get('baseline')
        default_moves = checked_moves(baseline)
        passed = [row for row in candidates if row and checked_moves(row) is not None]
        layout_passed = [row for row in result.get('layouts', []) if checked_moves(row) is not None]
        incumbent = min(passed, key=lambda row: (checked_moves(row), row.get('kind') != 'default')) if passed else None
        best = checked_moves(incumbent)
        incumbent_passes += best is not None
        restricted_passes += bool(layout_passed)
        delta = best - default_moves if best is not None and default_moves is not None else None
        if delta is not None and layout_passed:
            deltas.append(delta)
            gains += delta < 0
            ties += delta == 0
            losses += delta > 0
        layouts = result.get('layouts', [])
        random_count = sum(row.get('kind') == 'random' for row in layouts)
        search_count = sum(bool(row.get('solver_complete')) for row in layouts)
        checks = sum(len(actual_attempts(row)) for row in layouts)
        random_checks = sum('check' in row and row.get('kind') == 'random' for row in layouts)
        values = [checked_moves(row) for row in layout_passed]
        spread = '/'.join(fmt(v) for v in (min(values), statistics.median(values), max(values))) if values else '—'
        name = item['map_id'].removeprefix(item['size'] + '-')
        table.append(f"| {name} | {item['size']} | {item['block_count']} | {status(baseline)} | {fmt(best)} | {fmt(delta)} | {incumbent.get('layout_id', '—') if incumbent else '—'} | {random_count}/64 | {search_count}/{len(layouts)} | {len(layout_passed)}/{checks} | {spread} | {'complete' if result.get('complete') else 'partial' if result else 'pending'} |")
        if item['size'] == 'medium' and baseline and default_moves is None and layout_passed:
            rescues.append((item['map_id'], min(checked_moves(row) for row in layout_passed), status(baseline)))
    lines = ['# D175 block-layout headroom study', '',
             f"Generated {datetime.now(timezone.utc).isoformat()} from `{out}`. **{n_complete}/{len(records)} maps complete; {n_started} started.**",
             '',
             '## First read', '',
             f'Default passes: **{baseline_passes}/{len(records)}** ({sum(bool(result.get("baseline")) for _, result in records)} defaults evaluated). Best-found incumbent passes: **{incumbent_passes}/{len(records)}**; a restricted layout passes on **{restricted_passes}** maps. Every move value below comes from a V4-finished full-checker pass and a layout-conformance audit.',
             f'On {len(deltas)} maps with both a checked default and at least one checked restricted layout, best-found versus default: **{gains} fewer, {ties} equal, {losses} more**; exact two-sided sign-test p={sign_test(gains, losses):.6g} (ties excluded). Mean checked change: {fmt(statistics.mean(deltas) if deltas else None)} moves.',
             'Best-found includes the checked default incumbent. This is an optimistic search-headroom measurement, selected on training maps; the sign test is descriptive and does not estimate held-out learned-policy performance.', '']
    if n_complete < len(records):
        lines += ['**Partial study: absence of an improvement or medium rescue is not evidence of little headroom while search/checking remains incomplete.**', '']
    elif deltas and not gains:
        lines += ['**No checked move headroom was found on the mutually checked sample under this layout family and search budget.** Failure/timeout rates below limit the conclusion.', '']
    elif gains:
        lines += [f'Checked move headroom exists on {gains} maps in this sample; its size and reliability must be read with the incomplete/failed evaluations below.', '']
    else:
        lines += ['**No mutually checked comparison is available; headroom is undetermined.**', '']
    lines += ['| Size | Paired checked maps | Default mean moves | Best mean moves | Change | Fewer/equal/more | Sign-test p |',
              '|---|---:|---:|---:|---:|---|---:|']
    for size in sorted(manifest['counts']):
        pairs = []
        for item, result, candidates in rows:
            baseline_moves = checked_moves(result.get('baseline'))
            values = [checked_moves(row) for row in candidates if row and checked_moves(row) is not None]
            restricted_checked = any(checked_moves(row) is not None for row in result.get('layouts', []))
            if item['size'] == size and baseline_moves is not None and values and restricted_checked:
                pairs.append((baseline_moves, min(values)))
        before = statistics.mean(a for a, b in pairs) if pairs else None
        after = statistics.mean(b for a, b in pairs) if pairs else None
        change = f'{100*(after/before-1):.2f}%' if before else '—'
        w = sum(b < a for a, b in pairs)
        t = sum(b == a for a, b in pairs)
        l = sum(b > a for a, b in pairs)
        lines.append(f'| {size} | {len(pairs)} | {fmt(before)} | {fmt(after)} | {change} | {w}/{t}/{l} | {sign_test(w,l):.6g} |')
    lines += ['']
    selection = manifest['selection']
    lines += ['## Scope, layout and actual blocks', '',
              f"The active manifest contains {selection['small_population_manifest_count']:,} current small train maps, whereas the order estimated about 40. This run uses a predetermined ship-balanced sample of {manifest['counts'].get('small', 0)} (14/13/13 for the default 40), selected by {selection['method']}. All {manifest['counts'].get('medium', 0)} available manifest-identified archived train medium maps are included. `train_maps.json` records exact paths, SHA256, train split provenance, hatch/block inventory and loaded POD/declared weight categories. No test, validation or showcase instance bytes are read.",
              'A block is one physical hatch-cover group × hold/deck zone with actual vessel cells. Separate port/starboard covers remain separate groups. A layout permits POD × public catalogue D25 weight class in selected blocks; long-slot footprints must satisfy every touched block. Only new load-list cargo is restricted. Actual equipment, mass, DG, support, strength, stability, terminal and ballast rules remain active in the solver/checker.',
              'Random candidates have static nonoverlap/length/reefer packing witnesses. This necessary screen is not proof of physical or operational feasibility. Local search starts with the default plan’s permissions. All source code remains pinned; restrictions are scoped runtime wrappers outside `src/`.', '',
              'Sampling is deliberately bounded: random compatible cargo-to-slot permutations of a source packing, plus random extra block permissions, produce 64 unique masks. The source is the default departure packing when available, otherwise a constructive/CP static packing. This samples a biased subset of all possible layouts. Local hill climbing tries up to eight single-permission flips per round, for two improving rounds at most. Planner seed is 0; layout seed is 175. Each measured call has 15 s on small or 60 s on medium, equally for default and restrictions, after a discarded warmup. V4 allows up to 600 s for each selected physical plan.', '',
              '| Size | Ship | Maps | Covers per map | Blocks per map | Loads per map |',
              '|---|---|---:|---|---|---|']
    grouping = defaultdict(list)
    for item, _ in records:
        grouping[item['size'], item['ship']].append(item)
    for (size, ship), items in sorted(grouping.items()):
        unique = lambda key: ', '.join(str(v) for v in sorted({item[key] for item in items}))
        lines.append(f"| {size} | {ship} | {len(items)} | {unique('hatch_count')} | {unique('block_count')} | {unique('n_loads')} |")
    lines += ['', 'Archived medium inputs retain the `v1_pending_replacement` epoch and manifest `source_binding_status=drift`; they are not rebased. Full-checker rejection of historical lineage cannot establish that a modern medium call has no headroom.', '',
              '## Checked per-map results', '',
              'Moves are weighted nonproductive moves (the checker objective), not total load lifts. Δ=best−default; lower is better. “Solver complete” is bounded-search success with independent layout audit, not a checker pass. Spread=min/median/max among **checked passing restricted layouts only**; only the best few receive finishing/checking, so this is selection-biased and is not a distribution over all random layouts.', '',
              '| Map | Size | Blocks | Default | Best checked | Δ | Winner | Random evals | Solver complete/layouts | Certified layouts/V4 calls | Checked spread | State |',
              '|---|---|---:|---|---:|---:|---|---:|---:|---:|---|---|', *table, '',
              '## Layout spread and feasibility', '',
              f"Restricted evaluations recorded: **{len(restricted)}**. Random layouts have static packing feasibility by construction (0% geometrically infeasible among admitted random candidates); local permission removals have unknown static feasibility. No physically infeasible layout share can be inferred: planner failures/timeouts and rejected candidate plans do not prove that a layout admits no valid plan. The table gives observed failure/unknown shares instead.", '',
              'Certificates may be reused for identical physical plans after auditing each recipient layout. Certified-layout counts include these recipients; V4 calls and timing samples count actual calls only, including previous failed calls retained before certificate reuse.', '',
              '| Size | Layout evals | Solver-complete | No complete solver plan | Actual V4 calls | Certified layouts | Full-check/finisher failed calls |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for size in sorted(manifest['counts']):
        candidates = [row for item, result in records if item['size'] == size for row in result.get('layouts', [])]
        solver = sum(bool(row.get('solver_complete')) for row in candidates)
        checks = [check for row in candidates for check in actual_attempts(row)]
        passed = sum(checked_moves(row) is not None for row in candidates)
        failed = sum(check.get('passed') is not True for check in checks)
        lines.append(f'| {size} | {len(candidates)} | {fraction(solver, len(candidates))} | {fraction(len(candidates)-solver, len(candidates))} | {len(checks)} | {fraction(passed, len(candidates))} | {fraction(failed, len(checks))} |')
    statuses = Counter(str(row.get('status')) for row in restricted)
    rules = Counter(rule for row in all_candidates for check in actual_attempts(row) for rule in check.get('rules', []))
    lines += ['', 'Restricted solver statuses: ' + (', '.join(f'`{key}`={value}' for key, value in sorted(statuses.items())) or 'none yet') + '.',
              'Full-checker violation counts (candidates, multiple rules possible): ' + (', '.join(f'`{key}`={value}' for key, value in sorted(rules.items())) or 'none recorded') + '.', '',
              '## Where checked gains come from', '',
              'Components below come from full-checker move counts. Future lifts are the move engine’s voyage projection. Entries are default→best; unchanged current-call counts distinguish future port/hatch segregation gains from yard-order gains.', '',
              '| Winning map | Yard relocations | Restow now | Hatch now | Future restow lifts | Future hatch lifts |',
              '|---|---:|---:|---:|---:|---:|']
    for item, result, candidates in rows:
        baseline = result.get('baseline')
        passed = [row for row in candidates if row and checked_moves(row) is not None]
        if checked_moves(baseline) is None or not passed:
            continue
        best = min(passed, key=checked_moves)
        if checked_moves(best) >= checked_moves(baseline):
            continue
        def components(row):
            count = row['check']['count']
            future = count.get('future', [])
            return [count['relocations'], count['restow_lifts'], count['hatch_cover_lifts'],
                    sum(call['restow_lifts'] for call in future),
                    sum(call['hatch_cover_lifts'] for call in future)]
        changes = [f'{a}→{b}' for a, b in zip(components(baseline), components(best))]
        lines.append('| ' + item['map_id'] + ' | ' + ' | '.join(changes) + ' |')
    lines += ['', '## Medium calls', '']
    if rescues:
        lines += [f'- `{name}`: restricted checked pass {fmt(moves)} moves; default {state}.' for name, moves, state in rescues]
    else:
        lines += ['No checked medium rescue is recorded yet. A rescue requires an evaluated default with no checked pass and a restricted full-checker pass.']
    lines += ['', '## Evaluation cost and RL implications', '',
              'Observed wall seconds on the shared, contended CPU host. Layout generation, queue waiting and discarded cold setup/warmup solves are excluded from the two-step rows; two-step includes failed/time-limited measured calls. Finisher/check timings count actual calls only, excluding copied certificate durations. “1,000 evals” is serial two-step cost and excludes selected finishing/checking; a checker-reward RL evaluation would also need those costs.', '',
              '| Size | Two-step evals | Median s | Mean s | p90 s | 1,000 evals hours (mean) | Finisher median/mean s (n) | Checker median/mean s (n) |',
              '|---|---:|---:|---:|---:|---:|---|---|']
    for size in sorted(manifest['counts']):
        candidates = [row for item, result in records if item['size'] == size for row in [result.get('baseline'), *result.get('layouts', [])] if row]
        timings = [row['eval_seconds'] for row in candidates if isinstance(row.get('eval_seconds'), (int, float))]
        attempts = [check for row in candidates for check in actual_attempts(row)]
        finishes = [check['finisher_seconds'] for check in attempts if isinstance(check.get('finisher_seconds'), (int, float))]
        checks = [check['check_seconds'] for check in attempts if isinstance(check.get('check_seconds'), (int, float))]
        pair = lambda values: f'{fmt(statistics.median(values) if values else None)}/{fmt(statistics.mean(values) if values else None)} ({len(values)})'
        mean = statistics.mean(timings) if timings else None
        lines.append(f"| {size} | {len(timings)} | {fmt(statistics.median(timings) if timings else None)} | {fmt(mean)} | {fmt(quantile(timings,.9))} | {fmt(mean*1000/3600 if mean is not None else None)} | {pair(finishes)} | {pair(checks)} |")
    lines += ['', '| Size | Cold setup + discarded warmup median/mean s | Maps | Layout generation median/mean s | Maps |',
              '|---|---:|---:|---:|---:|']
    for size in sorted(manifest['counts']):
        setup = [result['cold_setup_and_warmup_seconds'] for item, result in records if item['size'] == size and isinstance(result.get('cold_setup_and_warmup_seconds'), (int, float))]
        generation = []
        for item, _ in records:
            cache = out / 'maps' / item['map_id'] / 'random_layouts.json'
            if item['size'] == size and cache.is_file():
                generation.append(load(cache)['seconds'])
        pair = lambda values: '/'.join(fmt(v) for v in (statistics.median(values), statistics.mean(values))) if values else '—'
        lines.append(f'| {size} | {pair(setup)} | {len(setup)} | {pair(generation)} | {len(generation)} |')
    errors = [(item['map_id'], result['error']) for item, result in records if result.get('error')]
    lines += ['', '## Completion and reproducibility', '',
              'The runner checkpoints each evaluation/check and can resume the same output directory. Random layout uniqueness and geometry witnesses are persisted; the full run targets ≥64 random feasible layouts per map plus local search, then checks the default and best few distinct plans. A first-read/pilot is explicitly partial. See `LEDGER.md`, `ASSUMPTIONS.md`, `source_snapshot.json`, the allowlist and per-map summaries for run configuration and provenance.',
              f"Recorded map errors: {len(errors)}."]
    lines.extend(f'- `{name}`: {error.replace(chr(10), " ")}' for name, error in errors)
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=HERE / 'train_maps.json')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=HERE / 'HEADROOM.md')
    args = parser.parse_args()
    args.output.write_text(render(load(args.manifest), args.out))
    print(str(args.output))

if __name__ == '__main__':
    main()

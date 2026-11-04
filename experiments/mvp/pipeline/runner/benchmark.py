"""Admission-controlled development budget study. Sealed paths are refused before reading.

180/900 seconds select the shipped live/standard profiles. Other values are
requested targets allocated by budget_profile. Actual wall time and the first
independently checked incumbent time are measured separately.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import time

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from experiments.mvp.pipeline.binding import confirm_brief
from .profiles import budget_profile, engine_kwargs, get_profile
from .runtime import configure_caches

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
DEV = ROOT / 'experiments/mvp/pipeline/maps/dev'
STATE = ROOT / 'experiments/fleet/core-state.json'


def wait_for_capacity(state_path=STATE, *, poll_s=15., sleep=time.sleep, now=time.time):
    """Wait for a fresh fleet reading, eight enabled cores, and load1 <= 45."""
    began = time.monotonic()
    while True:
        try:
            row = json.loads(Path(state_path).read_text())
            mask = int(row['mask'], 0) if isinstance(row['mask'], str) else int(row['mask'])
            load = float(row['load1'])
            age = now() - float(row['t'])
            if mask >= 0 and mask.bit_count() >= 8 and math.isfinite(load) and 0 <= load <= 45 and 0 <= age < 120:
                return dict(mask=mask, enabled_cores=mask.bit_count(), load1=load,
                            reading_age_s=age, waited_s=time.monotonic() - began)
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            pass
        sleep(poll_s)


def dev_paths(list_path, *, dev_root=DEV, root=ROOT):
    """Resolve all paths under the approved development directory before reads."""
    list_path = Path(list_path).resolve()
    if 'sealed' in list_path.parts:
        raise ValueError('Sealed cohorts are not available to the development benchmark')
    paths = []
    for line in list_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        path = Path(line)
        path = (Path(root) / path).resolve() if not path.is_absolute() else path.resolve()
        try:
            path.relative_to(Path(dev_root).resolve())
        except ValueError as error:
            raise ValueError(f'Benchmark input is outside the development cohort: {line}') from error
        if path.suffix != '.json' or 'sealed' in path.parts:
            raise ValueError(f'Benchmark input is not a development JSON map: {line}')
        if path in paths:
            raise ValueError(f'Duplicate development map: {line}')
        paths.append(path)
    if not paths:
        raise ValueError('The development cohort list is empty')
    return paths


def _mean(values):
    return statistics.mean(values) if values else None


def _ladder_summary(rows):
    out = []
    for level in range(1, 6):
        values = [r for r in rows if r.get('status') == 'relaxed_plan' and r.get('level') == level]
        if not values:
            continue
        out.append(dict(level=level, plans=len(values), full_passes=0,
                        completed=sum(r.get('completed') is True for r in values),
                        partial=sum(r.get('completed') is False for r in values),
                        mean_boxes_aboard_percent=_mean([r['boxes_aboard_percent'] for r in values
                            if isinstance(r.get('boxes_aboard_percent'), (int, float))]),
                        mean_lifts_completed=_mean([r['lifts'] for r in values
                            if r.get('completed') is True and isinstance(r.get('lifts'), (int, float))]),
                        mean_lifts_partial=_mean([r['lifts'] for r in values
                            if r.get('completed') is False and isinstance(r.get('lifts'), (int, float))])))
    return out


def summarize(rows):
    """Only checked_plan + passed counts; lift means use passing maps alone."""
    cells = defaultdict(list)
    for row in rows:
        cells[(row['size_class'], row['with_rules'], row['target_budget_s'])].append(row)
    out = []
    for (size, rules, budget), values in sorted(cells.items()):
        passed = [r for r in values if r.get('status') == 'checked_plan' and r.get('passed') is True]
        lifts = [r['lifts'] for r in passed if isinstance(r.get('lifts'), (int, float))]
        out.append(dict(size_class=size, with_rules=rules, target_budget_s=budget,
                        maps=len(values), full_passes=len(passed), pass_rate=len(passed)/len(values),
                        relaxed_plans=sum(r.get('status') == 'relaxed_plan' for r in values),
                        relaxed_by_level=_ladder_summary(values),
                        mean_lifts_passing=_mean(lifts), lift_maps=len(lifts),
                        mean_wall_s=_mean([r['wall_s'] for r in values]),
                        first_legal_maps=sum(r.get('first_legal_s') is not None for r in values),
                        mean_first_legal_s=_mean([r['first_legal_s'] for r in values if r.get('first_legal_s') is not None]),
                        load1_min=min(r['admission']['load1'] for r in values),
                        load1_max=max(r['admission']['load1'] for r in values)))
    comparisons = []
    for size, rules in sorted({(r['size_class'], r['with_rules']) for r in rows}):
        group = [r for r in rows if r['size_class'] == size and r['with_rules'] == rules]
        budgets = sorted({r['target_budget_s'] for r in group})
        longest = {r['map']: r for r in group if r['target_budget_s'] == budgets[-1]}
        for budget in budgets[:-1]:
            short = {r['map']: r for r in group if r['target_budget_s'] == budget}
            both = [(r, longest[name]) for name, r in short.items() if name in longest]
            is_pass = lambda r: r.get('status') == 'checked_plan' and r.get('passed') is True
            paired = [(a, b) for a, b in both if is_pass(a) and is_pass(b)
                      and a.get('lifts') is not None and b.get('lifts') is not None]
            comparisons.append(dict(size_class=size, with_rules=rules, short_budget_s=budget,
                                    reference_budget_s=budgets[-1], paired_maps=len(both),
                                    passes_lost=sum(is_pass(b) and not is_pass(a) for a, b in both),
                                    passes_gained=sum(is_pass(a) and not is_pass(b) for a, b in both),
                                    both_pass_maps=len(paired),
                                    mean_extra_lifts_both_pass=_mean([a['lifts']-b['lifts'] for a, b in paired])))
    return dict(cells=out, relaxed_by_level=_ladder_summary(rows), shorter_budget_cost=comparisons,
                notes='Development measurements only. Full passes require every instruction recheck. '
                      'Relaxed outcomes are split by ladder level and never included in pass rates. '
                      'Lifts include projected later-port lifts (D15). Medium lift means use passing maps only; '
                      'cell means can cover different maps. Paired lift differences use maps passing both budgets.')


def _write(path, value):
    from .evidence import json_evidence
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(json_evidence(value), indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def _profile(target):
    # Validate before numeric coercion so bools cannot become one-second targets.
    profile = budget_profile(target)
    if target == 180.:
        return get_profile('live')
    if target == 900.:
        return get_profile('standard')
    return dict(profile, name=f'target_{target:g}s')


def _validated_plan(instance, raw_plan, readings):
    """Fresh full check and every exact authenticated voyage reading, or None."""
    from experiments.mvp.reader.pipeline import intrinsic_violations
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    from stowage.checker import check
    from stowage.model import MovePlan
    try:
        plan = MovePlan.model_validate(raw_plan)
        report = check(instance, plan)
        if not report.passed or report.count is None:
            return None
        lifts = float(report.count.weighted_nonproductive)
        if not math.isfinite(lifts):
            return None
        context = context_from_instance(instance)
        rules = []
        for reading in readings:
            if reading['status'] != 'accepted':
                return None
            answer = validate_answer(json.dumps(reading['parsed']), context)
            violations = list(intrinsic_violations(instance, answer, plan))
            if violations:
                return None
            rules.append(dict(sentence=reading['sentence'], reading=reading['parsed'],
                              recheck=dict(passed=True, violations=[])))
        return dict(checker=report.model_dump(mode='json'), rules=rules, lifts=lifts)
    except Exception:
        return None


def _validated_relaxed_plan(instance, raw_plan, readings):
    """Independently classify a relaxed plan; T0 and broken instructions refuse."""
    from experiments.mvp.reader.pipeline import intrinsic_violations
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    from stowage.checker import check
    from stowage.model import MovePlan
    from engine.peel import inspect
    try:
        plan = MovePlan.model_validate(raw_plan)
        report = check(instance, plan)
        if report.passed or report.count is None:
            return None
        classification = inspect(instance, plan, report)
        if classification['level'] not in range(1, 6):
            return None
        context, rules = context_from_instance(instance), []
        for reading in readings:
            if reading['status'] != 'accepted':
                return None
            answer = validate_answer(json.dumps(reading['parsed']), context)
            violations = list(intrinsic_violations(instance, answer, plan))
            if violations:
                return None
            rules.append(dict(sentence=reading['sentence'], reading=reading['parsed'],
                              recheck=dict(passed=True, violations=[])))
        lifts = float(report.count.weighted_nonproductive)
        if not math.isfinite(lifts):
            return None
        return dict(checker=report.model_dump(mode='json'), rules=rules, lifts=lifts,
                    level=classification['level'], completed=classification['completed'],
                    boxes_aboard_percent=classification['boxes_aboard_percent'],
                    completion=classification)
    except Exception:
        return None


def benchmark(list_path, *, parser, out_dir, voyage_sentences, budgets=(60., 180., 900.),
              standing='One port per hatch.', executor=None, admit=None):
    """Run each dev map at each budget, with and without the supplied instructions.

    The caller confirms the supplied synthetic development instructions. The
    authenticated reader receipt still mediates every sentence and instruction.
    """
    from .evidence import best_safe_partial, json_evidence
    paths = dev_paths(list_path)
    if (not isinstance(voyage_sentences, (list, tuple)) or not voyage_sentences
            or not all(isinstance(s, str) and s.strip() for s in voyage_sentences)):
        raise ValueError('Supply at least one synthetic development instruction for the with-rules arm')
    profiles = [(float(b), _profile(b)) for b in budgets]
    if len({b for b, _ in profiles}) != len(profiles):
        raise ValueError('Budget values must be distinct')
    out_dir = Path(out_dir).resolve()
    try:
        out_dir.relative_to(HERE)
    except ValueError as error:
        raise ValueError('Benchmark output must stay inside the runner folder') from error
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
        os.environ[name] = '1'
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(out_dir / 'cache')
    configure_caches()
    from . import assemble
    from .envelope import check
    from experiments.mvp.reader.public_brief import VoyageBrief, read_brief
    if executor is None:
        from experiments.mvp.pipeline.binding import execute_brief
        executor = execute_brief
    admit = admit or wait_for_capacity
    rows = []
    for path in paths:
        packet = dict(packet_id=f'dev-budget-{path.stem}', created_utc=datetime.now(timezone.utc).isoformat(),
                      synthetic=True, base_instance=dict(path=str(path), sha256=sha256_file(path)),
                      standing=standing, voyage_sentences=[], profile='live')
        instance = assemble(packet)
        envelope = check(instance)
        if not envelope['in_envelope']:
            raise ValueError(f'Development map is outside the pipeline envelope: {path}: {envelope["reasons"]}')
        for with_rules in (False, True):
            sentences = tuple(voyage_sentences) if with_rules else ()
            for budget, profile in profiles:
                admission = admit()
                started = time.perf_counter()
                first = []
                readings = []

                def incumbent(event):
                    if not isinstance(event, dict) or event.get('plan') is None:
                        return
                    verdict = _validated_plan(instance, event['plan'], readings)
                    if verdict is not None and not first:
                        first.append(time.perf_counter() - started)

                row = dict(map=str(path.relative_to(ROOT)), map_sha256=packet['base_instance']['sha256'],
                           size_class=envelope['size_class'], with_rules=with_rules,
                           sentences=list(sentences), target_budget_s=budget, admission=admission,
                           profile={**profile, 'config': asdict(profile['config'])},
                           measurement='measured', first_legal_s=None)
                try:
                    receipt = read_brief(instance, VoyageBrief(standing=standing, voyage=sentences), parser=parser)
                    reading = receipt.evidence()
                    row['reading_sha256'] = sha256_json(reading)
                    readings = [r for r in reading['sentences'] if r['role'] == 'voyage']
                    if any(r['status'] != 'accepted' for r in readings):
                        row.update(status='needs_confirmation', passed=False, lifts=None,
                                   message='Correct or remove unresolved voyage instructions before planning.')
                    else:
                        receipt = confirm_brief(receipt, 'benchmark_batch_confirm_all')
                        from experiments.mvp.pipeline.binding.confirmation import verify_confirmation
                        confirmation = verify_confirmation(receipt)
                        row.update(confirmation_source=confirmation['source'],
                                   confirmation_sha256=confirmation['confirmation_sha256'])
                        result, evidence = executor(instance, receipt, **engine_kwargs(profile), on_incumbent=incumbent)
                        raw = getattr(result, 'plan', None) if result is not None else None
                        relaxed = evidence.get('status') == 'relaxed_plan'
                        validate = _validated_relaxed_plan if relaxed else _validated_plan
                        verdict = validate(instance, raw, readings) if raw is not None else None
                        passed = (verdict is not None and evidence.get('status') == 'checked_plan'
                                  and evidence.get('passed') is True)
                        status = evidence.get('status', 'error')
                        if (status == 'checked_plan' and not passed) or (relaxed and verdict is None):
                            status = 'no_checked_plan'
                        if passed and not first:
                            first.append(time.perf_counter()-started)
                        evidence = json_evidence(evidence)
                        row.update(status=status, passed=passed, lifts=verdict['lifts'] if verdict else None,
                                   independent_check=verdict, evidence=evidence)
                        if verdict and relaxed:
                            row.update({key: verdict[key] for key in
                                ('level', 'completed', 'boxes_aboard_percent', 'completion')})
                        elif passed:
                            row.update(level=0, completed=True, boxes_aboard_percent=100.)
                        row['best_safe_partial'] = best_safe_partial(evidence)
                except Exception as error:
                    row.update(status='error', passed=False, lifts=None, message=f'{type(error).__name__}: {error}')
                row.update(wall_s=time.perf_counter()-started, first_legal_s=first[0] if first else None)
                row = json_evidence(row)
                rows.append(row)
                _write(out_dir / 'measurements.json', dict(rows=rows, summary=summarize(rows)))
    return dict(rows=rows, summary=summarize(rows))


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cli.add_argument('--dev-list', type=Path, default=ROOT / 'experiments/mvp/pipeline/maps/dev.txt')
    cli.add_argument('--out', type=Path, default=HERE / 'measurements')
    cli.add_argument('--recorded', type=Path)
    cli.add_argument('--recorded-sha256', help='Independently trusted SHA256 of a custom recorded-reader file')
    cli.add_argument('--rules', type=Path, required=True, help='JSON list of synthetic instructions; running confirms them')
    cli.add_argument('--budgets', type=float, nargs='+', default=[60., 180., 900.])
    args = cli.parse_args(argv)
    try:
        from .__main__ import recorded_parser
        result = benchmark(args.dev_list, parser=recorded_parser(args.recorded, expected_sha256=args.recorded_sha256), out_dir=args.out,
                           voyage_sentences=json.loads(args.rules.read_text()), budgets=args.budgets)
        print(json.dumps(result['summary'], indent=2))
        return 0
    except Exception as error:
        print(json.dumps(dict(status='error', message=f'Benchmark could not run: {type(error).__name__}: {error}')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

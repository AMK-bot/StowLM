"""Cold arrival-aware box/slot assignment with fixed prefixes and true-input GM.

Concrete candidate recipes stay in this invocation's context. Every objective
and relaxed completion comes from ordinary replay and the unchanged checker.
"""
from __future__ import annotations

from dataclasses import fields
import importlib.util
import json
from pathlib import Path
from math import isfinite
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
for directory in (HERE/'engine_copy', HERE):
    sys.path.insert(0, str(directory))

from alns import Problem
from stowage.checker import check
from arrival import prepare, restow_options, replay_actions
from arrival_model import Model
from gap import Assignment, Settings
from stow import block_layout, to_assignment, exact_ratio
from targets import derive, derive_from_plan
from loading_seed import loading_seed
from joint_loading import JointLoading,kg_caps_below_probes,backoff_kg_caps
from e9fix import install
from tiers import analyze_report, rank_analysis


def _legacy():
    name = '_c6_tiny_pages'
    if name not in sys.modules:
        path = HERE/'pages_copy/method.py'
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _key(plan):
    return tuple(move.model_dump_json() for move in plan.moves)


def _hard(report):
    return [v for v in report.violations if v.severity == 'hard']


def _quality(report, instance):
    analysis = analyze_report(report, instance=instance)
    return (analysis['level'] if analysis['level'] is not None else 6,
            rank_analysis(analysis))


def _learn(state, report, gm):
    """Only search preferences move; no checker rule or limit changes."""
    state['gm'] = max(.15, float(gm)+.05)
    rules = {v.rule_id for v in _hard(report)}
    if any('securing' in r for r in rules):
        state['limit'] = max(.85, state['limit']-.025)
    if any('visibility' in r for r in rules):
        state['sight_margin'] += .3
    if any('second_check' in r or 'righting' in r for r in rules):
        state['vertical_scale'] *= .98
        state['kg_gm_floor'] = max(state['kg_gm_floor'], float(gm)+.15)+.05
    if any('weather' in r for r in rules):
        state['kg_gm_floor'] = max(state['kg_gm_floor'], float(gm)+.15)
    for violation in _hard(report):
        if 'trim' in violation.rule_id and violation.measured is not None:
            state['shift'] += -.3 if violation.measured < 0. else .3
            break


def propose(instance, *, seed, budget_s, context):
    """Finite outer rounds/branches and per-LP work counts; wall time is telemetry."""
    install()
    meta = context.setdefault('method_metadata', {})
    if len(instance.vessel.cells) <= 12:
        context['_lane'] = 'tiny-pages'
        tiny = dict(context,
            work_config=dict(limits=dict(pricing_calls=8000, patterns=12000,
                lp_calls=200, lp_iterations=200000), cp_deterministic_ticks=.5,
                cp_conflicts=100000, lp_maxiter=10000),
            finishing_config=dict(max_evaluations=28, max_iterations=2,
                max_lp_iterations=2000, trust_radius=.15, loading_seed=True,
                v4_fallback=False))
        context['_tiny_context'] = tiny
        return _legacy().propose(instance, seed=seed, budget_s=budget_s, context=tiny)
    started = monotonic()
    cfg = dict(rounds=6, branches=3, seed_loading=True, lcg_halfwidth_m=.3,
               tcg_halfwidth_m=.008, repair_rounds=4, max_candidates=5,
               joint_loading=True, joint_gm_floor=1.5,
               joint_tank_modes=['pressed', 'half'], joint_gm_min=.7,
               joint_tcg_halfwidth_m=.008,
               tank_mass_reward_per_t=0., tank_vertical_cost_per_tm=0.,
               joint_tail_solver='cp', pairs_per_box=64,
               joint_kg_step_m=0.,
               joint_kg_backoff_ratio=.5,
               branch_schedule=['keep', 'all', 'all', 'all', 'deck', 'all'])
    cfg.update(context.get('method_config', {}))
    kg_step=float(cfg['joint_kg_step_m'])
    if not isfinite(kg_step) or kg_step<0.:raise ValueError('joint_kg_step_m must be finite and nonnegative')
    kg_backoff_ratio=float(cfg['joint_kg_backoff_ratio'])
    if not isfinite(kg_backoff_ratio) or kg_backoff_ratio<0.:raise ValueError('joint_kg_backoff_ratio must be finite and nonnegative')
    original = context.get('problem') or Problem(instance)
    targets = derive(instance)
    state = dict(gm=targets.arrival_gm_m, kg_gm_floor=targets.arrival_gm_m,
                 limit=.95, shift=0., sight_margin=.15,
                 vertical_scale=1.)
    recipe = None
    reference_plan = None
    kg_caps={}
    kg_feedback_pending=False
    joint_gm_floor = float(cfg['joint_gm_floor'])
    options = restow_options(instance)[:int(cfg['branches'])]
    branches = {}
    candidates, seen = [], set()
    context['_recipes'] = {}
    context['_recipe_checks'] = {}
    context.setdefault('observed_plans', [])
    meta.update(method='arrival-fixed box-slot securing LP', cold=True,
                true_arrival=targets.diagnostics, construction_stop='work counts',
                requested_budget_s=budget_s, configuration=cfg, trials=[])
    allowed = {field.name for field in fields(Settings)}
    overrides = {k: v for k, v in context.get('work_config', {}).items() if k in allowed}
    overrides.update({k: v for k, v in cfg.get('assignment', {}).items() if k in allowed})
    for attempt in range(int(cfg['rounds'])):
        requested = cfg['branch_schedule'][attempt % len(cfg['branch_schedule'])]
        label, roots = next((option for option in options if option[0] == requested),
                            options[attempt % len(options)])
        row = dict(attempt=attempt, branch=label, gm_reference=state['gm'],
                   kg_gm_floor=state['kg_gm_floor'], log=[])
        meta['trials'].append(row)
        try:
            if label not in branches:
                branch = prepare(instance, roots, label=label)
                branches[label] = (branch, Problem(branch.working))
            branch, problem = branches[label]
            # Waterplane is input-derived; the outer loop adds a sight margin
            # when actual departure/voyage visibility calls for more room.
            model = Model(problem, branch, sight_draft_m=targets.departure_draught_m,
                          sight_roof_margin_m=state['sight_margin'])
            kwargs = targets.settings(gm=state['kg_gm_floor'], shift_m=state['shift'],
                lcg_halfwidth_m=cfg['lcg_halfwidth_m'], tcg_halfwidth_m=cfg['tcg_halfwidth_m'],
                vertical_scale=state['vertical_scale'])
            kwargs.update(limit=state['limit'], seed=seed+attempt,
                          joint_tail_solver=cfg['joint_tail_solver'],
                          pairs_per_box=int(cfg['pairs_per_box']))
            kwargs.update(overrides)
            joint = None
            if cfg['joint_loading'] and reference_plan is not None:
                modes = cfg['joint_tank_modes']
                tank_mode = modes[max(0,attempt-2) % len(modes)]
                joint = JointLoading(instance, reference_plan, gm_floor=joint_gm_floor,
                                     tank_mode=tank_mode,
                                     equality_grid_reserve=kwargs['joint_tail_solver'] != 'milp',
                                     tcg_halfwidth_m=cfg['joint_tcg_halfwidth_m'],
                                     tank_mass_reward_per_t=cfg['tank_mass_reward_per_t'],
                                     tank_vertical_cost_per_tm=cfg['tank_vertical_cost_per_tm'])
                if kg_feedback_pending:
                    previous_caps=dict(kg_caps)
                    kg_caps=kg_caps_below_probes(joint.probes,kg_step)
                    kg_feedback_pending=False
                    row['kg_cap_feedback']=dict(source='selected same-invocation area failure',
                        native_reference_kg_m={p['phase']:p['kg_m'] for p in joint.probes},
                        step_m=kg_step,caps_m=dict(kg_caps),previous_caps_metadata_only=previous_caps,outward=False)
                if kg_caps:
                    joint.set_kg_caps(kg_caps)
                # Water is now a decision, so fixed-water soft moment targets
                # must not compete with the physical cargo/tank loading rows.
                kwargs.update(joint_loading=joint, lcg=None, tcg=None, vertical_max_tm=None)
            motion_gm = (max(state['gm'], joint_gm_floor,
                max(p['gm_m'] for p in joint.probes)) if joint is not None else state['gm'])
            row['motion_gm'] = motion_gm
            solver = Assignment(model, targets.reference(instance.vessel.hull, gm=motion_gm),
                                Settings(**kwargs), layout=block_layout(model))
            placed = solver.dive(deadline=float('inf'), seed=seed+attempt, log=row['log'])
            row.update(branch.metadata, pairs=solver.n, fixed=len(solver.fixed_pairs))
            if joint is not None and joint.last_spec is not None:
                row['joint_loading'] = joint.last_spec['metadata']
            if placed is None:
                row.update(outcome='no complete assignment', work=dict(solver.work))
                if joint is not None:
                    if kg_caps and kg_step>0. and kg_backoff_ratio>0.:
                        previous_caps=dict(kg_caps)
                        kg_caps=backoff_kg_caps(kg_caps,joint.probes,kg_step*kg_backoff_ratio)
                        row['kg_cap_backoff']=dict(reason='bounded joint construction found no assignment',
                            previous_caps_m=previous_caps,next_caps_m=dict(kg_caps),
                            native_reference_kg_m={p['phase']:p['kg_m'] for p in joint.probes},
                            step_m=kg_step*kg_backoff_ratio,map_infeasibility_proven=False)
                    # Vary an inward search restriction after bounded failure;
                    # this is not evidence that the map cannot be completed.
                    joint_gm_floor = max(float(cfg['joint_gm_min']),joint_gm_floor-.2)
                continue
            ratios = solver.overages(placed)
            row['ratio_dive'] = max(ratios.values(), default=0.)
            if joint is None and row['ratio_dive'] > state['limit']+.03:
                placed, excess = solver.exchange(placed, deadline=float('inf'), log=row['log'])
                if excess > 1e-9 and attempt > 0:
                    placed, excess = solver.repair(placed, deadline=float('inf'),
                        rounds=int(cfg['repair_rounds']), seed=seed+attempt, log=row['log'])
                row['exchange_excess'] = excess
            assignment, illegal = to_assignment(model, placed, solver, preserve_order=joint is not None)
            row.update(illegal_columns=len(illegal), work=dict(solver.work))
            if illegal:
                row['outcome'] = 'concrete column rejected'
                continue
            actions = replay_actions(branch, assignment, problem)
            scored = original.score(actions) if actions is not None else None
            if scored is None:
                row['outcome'] = 'no complete legal replay'
                continue
            identity = tuple(actions)
            if identity in seen and joint is None:
                row['outcome'] = 'repeat'
                continue
            seen.add(identity)
            chosen_recipe = joint.candidate_recipe(solver.last_joint_solution) if joint is not None else recipe
            row['selected_recipe'] = 'joint-'+joint.tank_mode if joint is not None else 'inherited-or-invocation-carry'
            plan = scored.plan.model_copy(update={'ballast': chosen_recipe}) if chosen_recipe is not None else scored.plan
            context['observed_plans'].append(plan)
            report = check(instance, plan)
            row['raw_analysis'] = analyze_report(report, instance=instance)
            # One affine tank LP buys a useful loading seed, not a pass. Its
            # true recipe then feeds the same exact GM and full-check loop.
            if cfg['seed_loading'] and not report.passed:
                seeded, seed_meta = loading_seed(instance, plan,
                    max_lp_iterations=int(cfg.get('loading_lp_iterations', 2000)),
                    search_gm_floor=state['kg_gm_floor'])
                row['loading_seed'] = seed_meta
                if seeded is not None:
                    context['observed_plans'].append(seeded)
                    seeded_report = check(instance, seeded)
                    row['loading_seed_analysis'] = analyze_report(seeded_report, instance=instance)
                    if _quality(seeded_report, instance) < _quality(report, instance):
                        plan, report = seeded, seeded_report
                        row['selected_recipe'] = 'continuous-loading-repair-after-'+row['selected_recipe']
            gm, ratio, over = exact_ratio(instance, plan)
            row.update(outcome='passed' if report.passed else 'full-check failed',
                       gm_actual=gm, exact_ratio=ratio, chains_over=over,
                       lifts=float(report.count.weighted_nonproductive),
                       rules=sorted({v.rule_id for v in _hard(report)}))
            key = _key(scored.plan)
            old_report = context['_recipe_checks'].get(key)
            if old_report is None or _quality(report, instance) < _quality(old_report, instance):
                context['_recipes'][key] = plan
                context['_recipe_checks'][key] = report
            analysis = analyze_report(report, instance=instance)
            row['selected_analysis'] = analysis
            level = analysis['level'] if analysis['level'] is not None else 6
            candidates.append((level, rank_analysis(analysis), scored.objective, scored))
            if report.passed:
                break
            _learn(state, report, gm)
            if kg_step>0. and any('second_check' in v.rule_id for v in _hard(report)):
                kg_feedback_pending=True
                row['kg_cap_feedback_pending']=True
            if joint is not None and any('second_check' in v.rule_id or 'righting' in v.rule_id or 'weather' in v.rule_id
                   for v in _hard(report)):
                joint_gm_floor = max(joint_gm_floor+.1, float(gm)+.1)
            reference_plan = plan
            if plan.ballast is not None:
                # Refresh cargo moment/roof targets and carry the SAME water
                # into subsequent raw checks. This is an incumbent generated
                # inside this invocation, while true input GM remains in meta.
                recipe = plan.ballast
                targets = derive_from_plan(instance, plan)
                row['reference_refreshed'] = targets.diagnostics
        except (ValueError, KeyError, TypeError, RuntimeError, ArithmeticError) as error:
            row.update(outcome='unknown', error=f'{type(error).__name__}: {error}')
        finally:
            row['elapsed_s'] = monotonic()-started
            print(json.dumps(dict(instance_id=instance.id, trial=attempt, branch=label,
                outcome=row.get('outcome'), elapsed_s=row['elapsed_s'],
                joint='joint_loading' in row, gm_actual=row.get('gm_actual'),
                rules=row.get('rules'), work=row.get('work'),
                tail=[{k:v for k,v in entry.items() if k != 'slack_quanta'}
                      for entry in row['log'][-2:]])), flush=True)
    meta.update(propose_s=monotonic()-started, complete_replay_candidates=len(candidates))
    # Every raw/seed recipe was checked and the better report retained before
    # ranking complete candidates. Independent audit need only revisit that
    # frontier, while all trial breaches remain in method metadata.
    context['_observed_dominated_by_candidates'] = bool(candidates)
    candidates.sort(key=lambda item: item[:3])
    if candidates and candidates[0][0] == 0:
        return [candidates[0][3].actions]
    unique, keys = [], set()
    for item in candidates:
        key = _key(item[3].plan)
        if key not in keys:
            unique.append(item[3].actions); keys.add(key)
    return unique[:int(cfg['max_candidates'])]


def finish(instance, plan, *, seed, finalize_s, label, context):
    if context.get('_lane') == 'tiny-pages':
        return _legacy().finish(instance, plan, seed=seed, finalize_s=finalize_s,
                                label=label, context=context['_tiny_context'])
    candidate = context.get('_recipes', {}).get(_key(plan), plan)
    report = context.get('_recipe_checks', {}).get(_key(plan)) or check(instance, candidate)
    meta = dict(finisher='cold assignment + affine tank seed', passed=bool(report.passed))
    if report.passed:
        return candidate, meta
    rules = {v.rule_id for v in _hard(report)}
    cfg = context.get('finishing_config', {})
    if not cfg.get('local_enabled', True):
        return candidate, dict(meta, local_skipped='predeclared disabled')
    area_deficit = max((v.limit-v.measured for v in _hard(report)
        if 'second_check' in v.rule_id and v.unit == 'm*rad'
        and v.limit is not None and v.measured is not None), default=0.)
    if area_deficit > float(cfg.get('max_area_deficit_mrad', float('inf'))):
        return candidate, dict(meta, local_skipped='distant second-check area deficit',
                               max_area_deficit_mrad=area_deficit)
    water_only = all(q.startswith('iscode') or 'securing' in q or 'visibility' in q for q in rules)
    ratios = [v.measured/v.limit for v in _hard(report) if 'securing' in v.rule_id
              and v.measured is not None and v.limit is not None and v.limit > 0.]
    if not water_only or max(ratios, default=0.) > 1.25:
        return candidate, dict(meta, local_skipped='static or distant securing violation')
    from ballast_opt import optimize
    repaired, local = optimize(instance, candidate, out=HERE/'ballast'/f'{instance.id}-{label}',
        budget_s=finalize_s, max_evaluations=int(cfg.get('max_evaluations', 12)),
        max_iterations=int(cfg.get('max_iterations', 2)), parts=('stability', 'lashing', 'visibility'),
        work_bounded=bool(cfg.get('work_bounded',False)),
        max_lp_iterations=int(cfg.get('max_lp_iterations',2000)),
        trust_radius=float(cfg.get('trust_radius',.03)),
        max_trust_radius=float(cfg.get('max_trust_radius',.06)),
        preferred_groups=cfg.get('preferred_groups',['DB-F','DB-M','FWD-DEEP','WING-A','HEEL']))
    context.setdefault('observed_plans', []).append(repaired)
    return repaired, dict(meta, local=local, passed=bool(local['full_checker_passed']))

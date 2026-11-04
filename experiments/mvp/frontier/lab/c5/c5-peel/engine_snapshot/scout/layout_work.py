# Provenance: adapted from lab/c3/c2-layout/method_lp.py and polish.py, lab/c2/fx-scout/method.py, and engine/scout/layout011.py.
"""Cold, count-limited pages; all replay-complete action sequences survive.

Warm improvement uses a newly checked plan from this invocation. Saved
assignments, result files and stored passed flags are never constructor inputs.
"""
from collections import Counter, defaultdict
from copy import copy
from dataclasses import asdict
from itertools import combinations, islice
import random
from time import monotonic

from alns import Sequencer, Scored
from stowage.actions import get_action_space
from stowage.checker import check, departure_condition
from stowage.conventions import cells_of_slot, parse_slot
from stowage.moves import apply_plan
from .advanced_pages import Work, PureModel, MixedModel
from .layout011 import enabled, enumerate_layouts, verdict, balance_with_cuts, warm_columns
from .mixed_oracle import Fast
from .physics import Reference
from .repair import repair_oog

FEATURES = ('mixed_pages', 'sight_roofs', 'oog_reservations', 'unused_covers',
            'fixed_charge_lns', 'capacity_cuts', 'fixed_charge_lp', 'warm_polish',
            'fast_pricing', 'aggregation', 'restricted_master', 'asymmetric_deck',
            'unary_cache', 'oog_repair', 'securing_cuts')


def work_limits(context):
    config = context.get('engine_config')
    return Work(lp_rounds=getattr(config, 'lp_rounds', 40),
                polish_rounds=getattr(config, 'polish_rounds', 8),
                dive_nodes=getattr(config, 'dive_nodes', 80))


def safety_deadline(budget_s, now=None):
    return (monotonic() if now is None else now)+4*max(900., budget_s)


def make_model(problem, context, *, work, deadline, mixed=False, reserve=None, **kwargs):
    reserve = enabled(context, 'oog_reservations') if reserve is None else reserve
    cls = MixedModel if mixed or reserve else PureModel
    options = dict(work=work, safety_deadline=deadline,
        fast_pricing=enabled(context, 'fast_pricing'), aggregation=enabled(context, 'aggregation'),
        restricted_master=enabled(context, 'restricted_master'), unary_cache=enabled(context, 'unary_cache'),
        asymmetric_deck=enabled(context, 'asymmetric_deck'))
    if cls is MixedModel:
        options.update(mixed=mixed and enabled(context, 'mixed_pages'), reserve=reserve)
    return (context.get('page_model_factory') or cls)(problem, **options, **kwargs)


def retain_all(candidates):
    """Preserve alternative load orders and all replay-legal failure families."""
    selected = {}
    for scored in candidates:
        selected.setdefault(tuple(scored.actions), scored)
    return sorted(selected.values(), key=lambda scored: scored.objective)


def score_assignment(problem, model, assignment, *, context, fixed=None):
    if hasattr(model, 'clear_oog'):
        assignment = model.clear_oog(assignment)
    if enabled(context, 'oog_repair') and hasattr(model, 'column_fine'):
        assignment = repair_oog(problem, model, assignment)
    if fixed and any(assignment.get(box) != slot for box, slot in fixed.items()):
        return None
    if set(assignment) != set(problem.instance.load_list) or problem.mixes_hold_stack(assignment):
        return None
    actions = Sequencer(problem).order(assignment)
    scored = problem.score(actions) if actions is not None else None
    if scored is not None and '_warm_ballast' in context:
        plan = scored.plan.model_copy(update={'ballast': context['_warm_ballast']})
        replay = apply_plan(problem.instance, plan)
        if not replay.legal:
            return None
        scored = Scored(tuple(actions), float(replay.count.weighted_nonproductive), plan)
    return scored



def replay_seed_alternative(problem, model, chosen, *, seed, context):
    """Retain a complete seed rejected only by search guidance.

    Balance on a shallow model with private pool containers: newly moved seed
    columns must not alter the subsequent moment-aware LP/dive search path.
    The numerical work caps and the main search RNG remain unchanged.
    """
    validator = getattr(model, '_seed_columns_valid', model._valid_seed)
    if not validator(chosen):
        return None
    probe = copy(model)
    if hasattr(model, 'columns'):
        probe.columns = list(model.columns)
    if hasattr(model, 'pool'):
        probe.pool = dict(model.pool)
    try:
        balanced, _ = probe.balance(list(chosen), random.Random(seed), rounds=4000)
    except Exception:
        balanced = list(chosen)
    return score_assignment(problem, probe, probe.assignment(balanced), context=context)

def construct(problem, model, *, seed, deadline, context):
    started = monotonic()
    row = dict(replay_legal=False, unary_cache_hit=bool(getattr(model, 'unary_cache_hit', False)),
               mixed_pages=sum(page.kind == 'mixed' for page in model.pages),
               oog_reserved_boxes=len(getattr(model, 'reserved', {})))
    if monotonic() >= deadline:
        return [], dict(row, safety_deadline_hit=True)
    root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                          phase=1, deadline=deadline, max_rounds=model.work.lp_rounds)
    row.update(lp_uncovered=root['uncovered'], lp_rounds=root['rounds'],
               columns=len(model.columns), lp_s=monotonic()-started)
    candidates = []
    seed_choice = getattr(model, 'seed_chosen', None)
    rejected_by_guidance = seed_choice is not None and not model._valid_seed(seed_choice)
    early_stop = root['uncovered'] > 3. or monotonic() >= deadline
    if seed_choice is not None and (rejected_by_guidance or early_stop):
        try:
            alternative = replay_seed_alternative(problem, model, seed_choice, seed=seed, context=context)
            if alternative is not None:
                candidates.append(alternative)
                row.update(seed_alternative_replay=True,
                           seed_alternative_lifts=alternative.objective,
                           seed_rejected_by_guidance=rejected_by_guidance)
        except Exception as error:
            row['seed_alternative_error'] = f'{type(error).__name__}: {error}'
    if early_stop:
        row.update(replay_legal=bool(candidates), primary_candidate_index=0,
                   primary_lifts=candidates[0].objective if candidates else None)
        return retain_all(candidates), row
    try:
        if model.seed_chosen is not None and not model._valid_seed(model.seed_chosen):
            model.seed_chosen = None
        if model.seed_chosen is None:
            model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                           phase=3, deadline=deadline, max_rounds=model.work.polish_rounds)
        rng = random.Random(seed)
        chosen, info = model.dive(deadline=deadline, rng=rng, retreats=8)
    except Exception as error:
        row.update(guided_search_error=f'{type(error).__name__}: {error}',
                   replay_legal=bool(candidates), primary_candidate_index=0,
                   primary_lifts=candidates[0].objective if candidates else None)
        return retain_all(candidates), row
    row.update(info)
    if chosen is None:
        row.update(replay_legal=bool(candidates), primary_candidate_index=0,
                   primary_lifts=candidates[0].objective if candidates else None)
        return retain_all(candidates), row
    raw = list(chosen)
    try:
        chosen, heel = model.balance(chosen, rng, rounds=4000)
    except Exception as error:
        chosen, heel = raw, None
        row['balance_error'] = f'{type(error).__name__}: {error}'
    try:
        scored = score_assignment(problem, model, model.assignment(chosen), context=context)
    except Exception as error:
        scored = None
        row['replay_error'] = f'{type(error).__name__}: {error}'
    if scored is not None:
        candidates.append(scored)
    preferred = scored if scored is not None else (candidates[0] if candidates else None)
    row.update(heel_tm=heel, replay_legal=bool(candidates), lifts=scored.objective if scored else None)
    if scored is not None and enabled(context, 'warm_polish') and hasattr(model, 'polish'):
        try:
            ship = apply_plan(problem.instance, scored.plan).final_state.ship
            polished, note = model.polish(chosen, Fast(problem.instance, ship))
            row['securing_polish'] = note
            if note['swaps']:
                again = score_assignment(problem, model, model.assignment(polished), context=context)
                if again is not None:
                    candidates.append(again)
                    preferred = again
        except Exception as error:
            row['securing_polish_error'] = f'{type(error).__name__}: {error}'
    if scored is not None and enabled(context, 'securing_cuts'):
        try:
            from ..benders.cuts import page_cut_callback, page_cut_repair
            cut = page_cut_callback(problem, model, chosen, scored)
            if cut is not None:
                model.page_cuts.append(cut)
                replacement, note = page_cut_repair(model, chosen, model.page_cuts, deadline, work=model.work)
                row['securing_cuts'] = dict(certified_cuts=1, repair=note)
                if replacement is not None:
                    balanced, _, reverted = balance_with_cuts(model, replacement, rng)
                    row['securing_cuts']['balance_reverted_for_cut'] = reverted
                    again = score_assignment(problem, model, model.assignment(balanced), context=context)
                    if again is not None:
                        candidates.append(again)
                        preferred = again
        except Exception as error:
            row['securing_cuts_error'] = f'{type(error).__name__}: {error}'
    row.update(rmp=getattr(model, 'rmp_history', []), seconds=monotonic()-started)
    selected = retain_all(candidates)
    row['primary_candidate_index'] = next((i for i, candidate in enumerate(selected)
        if candidate is preferred), 0)
    row['primary_lifts'] = selected[row['primary_candidate_index']].objective if selected else None
    return selected, row


def layout_for(model, closed=()):
    """Port partition by mandatory hold demand, from C3 method_lp."""
    capacity, need = defaultdict(int), Counter()
    for page in model.pages:
        if page.kind == '20' and not page.deck and page.cover is not None and page.cover not in closed:
            capacity[page.cover] += len(page.slots)
    for box in model.boxes:
        if not model.static[box, model.T.slot_deck].any():
            need[model.pod[box]] += float(model.T.units[box])
    if not need:
        for box in model.boxes:
            need[model.pod[box]] += float(model.T.units[box])
    for port in model.ports:
        need.setdefault(port, 0.)
    layout = {}
    for cover in sorted(capacity, key=lambda c: (-capacity[c], c)):
        port = max(need, key=need.get)
        layout[cover] = port
        need[port] -= capacity[cover]
    return layout


def page_capacity_ok(model):
    """Necessary only for the selected family; never certifies map infeasibility."""
    return sum(float(model.T.units[b]) for b in model.boxes) <= sum(
        len(page.slots) for page in model.pages if page.kind == '20')


def tier_profile(problem, assignment):
    T = problem.engine.T
    bays = sorted({parse_slot(slot).bay for i, slot in enumerate(T.slot_ids)
                   if T.slot_deck[i] and T.slot_n[i] == 1})
    caps = [1, 1, 1]
    for slot in assignment.values():
        si = T.slot_index[slot]
        if T.slot_deck[si]:
            part = min(2, sum(b < parse_slot(slot).bay for b in bays)*3//max(1, len(bays)))
            caps[part] = max(caps[part], int(max(T.slot_k[si]))+1)
    return tuple(caps)


def improve_checked(problem, incumbent, *, context, work, deadline, seed, iterations,
                    evaluate, force_closure):
    """Fixed-charge LNS or unforced polish, seeded only from this run's pass."""
    from . import lns_lp, lns
    engine = lns_lp if enabled(context, 'fixed_charge_lp') else lns
    assignment = {action.box: action.slot for action in incumbent.actions}
    ship = departure_condition(problem.instance, incumbent.plan)
    model = PureModel(problem, work=work, safety_deadline=deadline,
        fast_pricing=enabled(context, 'fast_pricing'), unary_cache=enabled(context, 'unary_cache'),
        asymmetric_deck=enabled(context, 'asymmetric_deck'), aggregation=enabled(context, 'aggregation'),
        restricted_master=enabled(context, 'restricted_master'), oracle=Fast(problem.instance, ship),
        deck_tiers=tier_profile(problem, assignment), lash_limit=1., hold_reward=1.)
    oracle = model.oracle
    try:
        model.oracle = None  # freshly checked incumbent only; new patterns keep gates
        chosen = engine.seed_assignment(model, assignment)
    except ValueError as error:
        return [dict(kind='lns_seed', status=str(error))]
    finally:
        model.oracle = oracle
    covers = sorted({page.cover for page in model.pages if page.cover is not None})
    counts = Counter(model.pages[model.columns[k].page].cover for k in chosen
                     if not model.pages[model.columns[k].page].deck)
    targets = sorted(counts, key=lambda cover: (counts[cover], cover))
    rows = []
    for attempt in range(iterations):
        if monotonic() >= deadline or not covers:
            break
        target = targets[attempt % len(targets)] if force_closure and targets else covers[attempt % len(covers)]
        nearby = sorted(covers, key=lambda cover: (cover != target,
                        abs(covers.index(cover)-covers.index(target)), cover))[:4]
        fixed = {b: slot for b, slot in assignment.items()
                 if all(model.T.stack_cover[int(j)] not in nearby
                        for j in model.T.slot_stacks[model.T.slot_index[slot]] if j >= 0)}
        hold_only = [b for b in model.boxes if model.T.e6_threshold is not None
                     and model.T.mass[b] >= model.T.e6_threshold
                     and not problem.boxes[model.T.box_ids[b]].dg
                     and not problem.boxes[model.T.box_ids[b]].oog
                     and not problem.boxes[model.T.box_ids[b]].special_stowage] if force_closure else []
        nogoods = []
        for repair_attempt in range(3):
            if monotonic() >= deadline:
                break
            out = engine.improve(model, chosen, nearby,
                closed_hold=[target] if force_closure else (), hold_only=hold_only,
                forbidden_pairs=nogoods, seconds=max(0., deadline-monotonic()),
                pricing_s=max(0., deadline-monotonic()) if repair_attempt == 0 else 0.,
                seed=seed+attempt+repair_attempt, max_rounds=work.polish_rounds,
                heel_slack_tm=50, lcg_slack_tm=150, work=work, safety_deadline=deadline)
            row = {key: value for key, value in out.items() if key not in ('chosen', 'assignment')}
            row.update(kind='fixed_charge_lns' if force_closure else 'warm_polish',
                       iteration=attempt, repair_attempt=repair_attempt)
            rows.append(row)
            scored = score_assignment(problem, model, out['assignment'], context=context,
                                      fixed=fixed) if out.get('chosen') is not None else None
            ok, rules = evaluate(scored, row)
            if scored is not None and ok and scored.objective < incumbent.objective:
                incumbent, assignment = scored, {action.box: action.slot for action in scored.actions}
                try:
                    model.oracle = None
                    chosen = engine.seed_assignment(model, assignment)
                finally:
                    model.oracle = oracle
                break
            if scored is None or not rules or not all('e9' in rule for rule in rules):
                break
            checked = check(problem.instance, scored.plan)
            try:
                model.oracle = None
                failed = engine.seed_assignment(model, {a.box: a.slot for a in scored.actions})
                owner = {model.T.box_ids[b]: k for k in failed for b in model.columns[k].boxes}
                for violation in checked.violations:
                    if 'e9' in violation.rule_id:
                        ids = [owner[b] for b in violation.containers if b in owner]
                        if ids:
                            nogoods.append((ids[0], ids[-1]))
            except ValueError:
                break
            finally:
                model.oracle = oracle
    return rows



def reference_lcg(problem, scored):
    mass = sum(problem.boxes[action.box].gross_mass_kg for action in scored.actions)
    return sum(problem.boxes[action.box].gross_mass_kg
               *problem.cells[cells_of_slot(action.slot)[0]].lcg_m
               for action in scored.actions)/mass


def initial_pure_settings(n_boxes, lcg, *, bow_down):
    # Exact 0.11 initialization: its family tier/securing caps already apply;
    # the plain reference additionally supplies its bow-down target.
    return dict(deck_tiers=3 if n_boxes > 120 else 4, lash_limit=.94,
                lcg_target=lcg-1.5 if bow_down else None, hold_reward=3.)


def learn_pure(settings, rules, bow_down, reference_lcg):
    settings = dict(settings)
    if any('visibility' in rule for rule in rules):
        settings['deck_tiers'] = 3
    if bow_down:
        settings['lcg_target'] = (settings['lcg_target'] or reference_lcg)-1.5
    elif any('trim' in rule or 'draft' in rule for rule in rules) and settings['lcg_target'] is not None:
        settings['lcg_target'] += 1.
    if any('securing' in rule for rule in rules):
        settings['lash_limit'] = max(.85, settings['lash_limit']-.03)
    if any('e6' in rule for rule in rules):
        settings['hold_reward'] *= 3.
    return settings


def run_pure_anchor(problem, first_model, layouts, *, settings, lcg, oracle,
                    context, work, deadline, seed, rungs, evaluate, passing):
    """The 0.11 pure ladder, with counts replacing its short wall shares.

    The oracle stays on the original reference, no sight roofs/reservations or
    mixed patterns enter this family, and changed diagnostics retry the same
    cover design before advancing. Extra families cannot consume this work.
    """
    queue = [(layouts[0], True), (layouts[0], 'no_restow')]
    queue += [(layout, 'no_restow') for layout in layouts[1:4]]
    queue += [(layouts[0], False), (None, False)]
    queue += [(layout, 'no_restow') for layout in layouts[4:]]
    rows, seen, previous = [], set(), first_model
    attempt = 0
    while queue and attempt < rungs and monotonic() < deadline:
        layout, mode = queue.pop(0)
        key = (tuple(sorted((layout or {}).items())), str(mode), tuple(sorted(settings.items())))
        if key in seen:
            continue
        seen.add(key)
        attempt += 1
        row = dict(kind='pure_anchor', iteration=attempt, deck_mode=mode, settings=dict(settings),
                   mixed=False, reservations=False, sight_roofs=False)
        rows.append(row)
        try:
            model = PureModel(problem, work=work, safety_deadline=deadline,
                fast_pricing=enabled(context, 'fast_pricing'), unary_cache=enabled(context, 'unary_cache'),
                asymmetric_deck=enabled(context, 'asymmetric_deck'), aggregation=enabled(context, 'aggregation'),
                restricted_master=enabled(context, 'restricted_master'), oracle=oracle,
                layout=layout, layout_deck=mode, **settings)
            row['warm_columns'] = warm_columns(model, previous, deadline=deadline, limit=4000)
            previous = model
            found, construction = construct(problem, model, seed=seed+attempt, deadline=deadline, context=context)
            row.update(construction)
            if not found:
                continue
            answers = []
            for scored in found:
                ok, rules = evaluate(scored, row)
                answers.append((ok, rules, row.get('bow_down', False)))
            primary = min(row.get('primary_candidate_index', 0), len(found)-1)
            scored, (ok, rules, bow_down) = found[primary], answers[primary]
            if ok or (passing and scored.objective >= min(item.objective for item in passing)):
                continue
            changed = learn_pure(settings, rules, bow_down, lcg)
            if changed != settings and attempt < min(9, rungs):
                queue.insert(0, (layout, mode))
                row['retry_same_design'] = True
            settings = changed
        except Exception as error:
            row['error'] = f'{type(error).__name__}: {error}'
    return rows, settings

def propose(instance, *, seed, budget_s, context):
    problem = context['problem']
    meta = context.setdefault('method_metadata', {})
    work = work_limits(context)
    config = context.get('engine_config')
    rungs = getattr(config, 'layout_rungs', 12)
    iterations = getattr(config, 'lns_iterations', 6)
    started = monotonic()
    deadline = safety_deadline(budget_s, started)
    meta.update(method='fixed-work cold page portfolio', trials=[],
                options={name: enabled(context, name) for name in FEATURES},
                work_limits=dict(**asdict(work), layout_rungs=rungs, lns_iterations=iterations),
                safety_allowance_s=deadline-started, gate='real replay then unchanged full checker')
    if instance.onboard.placements:
        meta['skipped'] = 'arrival-prefix route owns nonempty ships'
        return []
    candidates, passing = [], []

    def evaluate(scored, row):
        if scored is None:
            row['replay_legal'] = False
            return False, []
        candidates.append(scored)
        row.update(replay_legal=True, lifts=scored.objective)
        try:
            ok, rules, bow = verdict(instance, scored.plan, context=context)
        except Exception as error:
            row.update(full_check=False, checker_error=f'{type(error).__name__}: {error}')
            return False, []
        row.update(full_check=ok, rules=rules, bow_down=bow)
        row.setdefault('candidate_checks', []).append(dict(lifts=scored.objective,
            full_check=ok, rules=rules, bow_down=bow))
        if ok:
            passing.append(scored)
        return ok, rules

    first = None
    for attempt, reserve in enumerate((False, enabled(context, 'oog_reservations'))):
        if monotonic() >= deadline:
            break
        try:
            model = make_model(problem, context, work=work, deadline=deadline, reserve=reserve)
            found, row = construct(problem, model, seed=seed+attempt, deadline=deadline, context=context)
        except Exception as error:
            meta['trials'].append(dict(kind='reference', error=f'{type(error).__name__}: {error}'))
            continue
        row.update(kind='reference', reservations=reserve)
        meta['trials'].append(row)
        for scored in found:
            evaluate(scored, row)
        if found:
            primary = min(row.get('primary_candidate_index', 0), len(found)-1)
            first = found[primary]
            first_model = model
            checks = row.get('candidate_checks', [])
            first_bow = checks[primary]['bow_down'] if len(checks) > primary else row.get('bow_down', False)
            break
    if first is None:
        meta.update(propose_s=monotonic()-started, complete_replay_legal_stows=0, fully_checked_stows=0)
        return []
    current = first
    lcg = reference_lcg(problem, first)
    settings = initial_pure_settings(len(instance.load_list), lcg, bow_down=first_bow)
    try:
        ship = apply_plan(instance, first.plan).final_state.ship
        anchor_oracle = Fast(instance, ship)
        base = PureModel(problem, work=work, safety_deadline=deadline, oracle=anchor_oracle, **settings)
        layouts = enumerate_layouts(base, limit=max(64, rungs))
        covers = sorted({page.cover for page in base.pages if page.cover is not None})
    except Exception as error:
        meta.update(anchor_setup_error=f'{type(error).__name__}: {error}',
                    complete_replay_legal_stows=len(retain_all(candidates)),
                    fully_checked_stows=len(retain_all(passing)), propose_s=monotonic()-started)
        return [scored.actions for scored in retain_all(candidates)]
    anchor_rows, settings = run_pure_anchor(problem, first_model, layouts,
        settings=settings, lcg=lcg, oracle=anchor_oracle, context=context, work=work,
        deadline=deadline, seed=seed, rungs=rungs, evaluate=evaluate, passing=passing)
    meta['trials'].extend(anchor_rows)
    meta['pure_anchor_trials'] = len(anchor_rows)
    current = min(passing, key=lambda item: item.objective) if passing else first
    # Modern per-stack roofs and reservations are additive to the pure anchor.
    designs = ([(layouts[0], True, (), False), (layouts[0], 'no_restow', (), False)]
               if enabled(context, 'sight_roofs') or enabled(context, 'oog_reservations') else [])
    if enabled(context, 'mixed_pages'):
        designs += [(layouts[0], 'no_restow', (), True), (None, False, (), True)]
    if enabled(context, 'unused_covers'):
        closed_designs = []
        for count in range(len(covers), 0, -1):
            closed_designs += [(None, 'no_restow', closed, False)
                               for closed in islice(combinations(covers, count), 2)]
        designs += closed_designs[:rungs]
    meta['scheduled_designs'] = len(designs)
    sight_margin = 0.
    for attempt, (layout, deck_mode, closed, mixed) in enumerate(designs):
        try:
            if monotonic() >= deadline:
                break
            ship = apply_plan(instance, current.plan).final_state.ship
            reference = Reference(instance, ship, problem.engine.T, margin=sight_margin) if enabled(context, 'sight_roofs') else None
            oracle = reference.oracle if reference is not None else Fast(instance, ship)
            kwargs = dict(settings, oracle=oracle, layout=layout, layout_deck=deck_mode, closed_hold=closed)
            if reference is not None:
                kwargs['roofs'] = reference.roofs
            model = make_model(problem, context, work=work, deadline=deadline, mixed=mixed, **kwargs)
            row = dict(kind='closed_design' if closed else 'layout', iteration=attempt,
                       closed_hold=closed, mixed=mixed, deck_mode=deck_mode, sight_margin_m=sight_margin, settings=dict(settings))
            if enabled(context, 'capacity_cuts') and not page_capacity_ok(model):
                row.update(status='selected page-family cell capacity screen', certified_map_rejection=False)
                meta['trials'].append(row)
                continue
            if closed:
                kwargs['layout'] = layout_for(model, closed)
                model = make_model(problem, context, work=work, deadline=deadline, mixed=mixed, **kwargs)
            found, construction = construct(problem, model, seed=seed+11+attempt, deadline=deadline, context=context)
            row.update(construction)
            meta['trials'].append(row)
            for scored in found:
                ok, rules = evaluate(scored, row)
                current = min(passing, key=lambda item: item.objective) if passing else scored
                if passing and scored.objective >= min(item.objective for item in passing):
                    continue
                if any('visibility' in rule for rule in rules):
                    settings['deck_tiers'] = 3
                    sight_margin = .7
                if row.get('bow_down'):
                    mass = sum(problem.boxes[a.box].gross_mass_kg for a in scored.actions)
                    lcg = sum(problem.boxes[a.box].gross_mass_kg*problem.cells[cells_of_slot(a.slot)[0]].lcg_m
                              for a in scored.actions)/mass
                    settings['lcg_target'] = (settings['lcg_target'] or lcg)-1.5
                elif any('trim' in rule or 'draft' in rule for rule in rules) and settings['lcg_target'] is not None:
                    settings['lcg_target'] += 1.
                if any('securing' in rule for rule in rules):
                    settings['lash_limit'] = max(.85, settings['lash_limit']-.03)
                if any('e6' in rule for rule in rules):
                    settings['hold_reward'] *= 3.
        except Exception as error:
            meta['trials'].append(dict(kind='layout_error', iteration=attempt,
                                       error=f'{type(error).__name__}: {error}'))
    if passing:
        incumbent = min(passing, key=lambda scored: scored.objective)
        for flag, force in (('fixed_charge_lns', True), ('warm_polish', False)):
            if enabled(context, flag) and monotonic() < deadline:
                try:
                    meta['trials'].extend(improve_checked(problem, incumbent, context=context,
                        work=work, deadline=deadline, seed=seed+101, iterations=iterations,
                        evaluate=evaluate, force_closure=force and enabled(context, 'unused_covers')))
                    incumbent = min(passing, key=lambda scored: scored.objective)
                except Exception as error:
                    meta['trials'].append(dict(kind=flag+'_error', error=f'{type(error).__name__}: {error}'))
    selected = retain_all(candidates)
    passed = {tuple(scored.actions) for scored in passing}
    meta.update(propose_s=monotonic()-started, complete_replay_legal_stows=len(selected),
                fully_checked_stows=len(passed), safety_deadline_hit=monotonic() >= deadline,
                retained_guidance_candidates=sum(tuple(scored.actions) not in passed for scored in selected))
    meta['contributions'] = dict(
        replay_candidates=len(selected), checked_passes=len(passed),
        mixed_trials=sum(bool(row.get('mixed')) for row in meta['trials']),
        unused_cover_trials=sum(bool(row.get('closed_hold')) for row in meta['trials']),
        fixed_charge_lns_trials=sum(row.get('kind') == 'fixed_charge_lns' for row in meta['trials']),
        warm_polish_trials=sum(row.get('kind') == 'warm_polish' for row in meta['trials']),
        capacity_screens=sum(row.get('certified_map_rejection') is False for row in meta['trials']),
        best_checked_lifts=min((scored.objective for scored in passing), default=None))
    return [scored.actions for scored in selected]


def polish_finished(instance, plan, *, seed, budget_s, context):
    """Improve an engine-finished pass, retaining its independently checked tanks.

    Returns Scored objects carrying their ballast recipe. Every returned plan,
    including the original incumbent, has passed real replay and the full
    checker during this call. No stored row or external warm result is read.
    """
    problem = context['problem']
    meta = context.setdefault('method_metadata', {}).setdefault('warm_polish_after_finish', {})
    replay = apply_plan(instance, plan)
    report = check(instance, plan)
    if not replay.legal or not report.passed:
        meta['skipped'] = 'requires a replay-legal fully checked engine plan'
        return []
    actions = tuple(get_action_space('boxslot').from_plan(instance, plan))
    incumbent = Scored(actions, float(report.count.weighted_nonproductive), plan)
    passed = [incumbent]
    if instance.onboard.placements or instance.discharge_list:
        meta['skipped'] = 'pure empty-arrival polish; fixed-prefix route retained'
        return passed
    if not enabled(context, 'warm_polish'):
        meta['skipped'] = 'disabled'
        return passed
    local = dict(context, _warm_ballast=plan.ballast)
    work = work_limits(context)
    deadline = safety_deadline(budget_s)
    meta.update(reference='engine-finished plan from this invocation', trials=[], checked_candidates=0)

    def evaluate(scored, row):
        if scored is None:
            row['replay_legal'] = False
            return False, []
        # Deliberately use the complete checker even if the cold constructor
        # has a fail-only diagnostic callback in its context.
        checked = check(instance, scored.plan)
        rules = sorted({v.rule_id for v in checked.violations})
        row.update(replay_legal=True, lifts=scored.objective, full_check=checked.passed, rules=rules)
        meta['checked_candidates'] += 1
        if checked.passed:
            passed.append(scored)
        return checked.passed, rules

    try:
        meta['trials'] = improve_checked(problem, incumbent, context=local, work=work,
            deadline=deadline, seed=seed, iterations=getattr(context.get('engine_config'), 'lns_iterations', 6),
            evaluate=evaluate, force_closure=False)
    except Exception as error:
        meta['error'] = f'{type(error).__name__}: {error}'
    result = retain_all(passed)
    meta.update(passes=len(result), best_lifts=min(scored.objective for scored in result),
                safety_deadline_hit=monotonic() >= deadline)
    return result

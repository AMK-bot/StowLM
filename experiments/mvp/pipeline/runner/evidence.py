"""JSON-safe diagnostic evidence; unknown ranks stay unknown, never finite."""
import math
from copy import deepcopy


LIVE_UNSEARCHED_LADDER_MESSAGE = "L1-L3 weren't searched on the live profile. This is the best candidate already found."


def json_evidence(value):
    nonfinite = []
    def visit(item, path):
        if isinstance(item, float) and not math.isfinite(item):
            kind = 'nan' if math.isnan(item) else 'positive_infinity' if item > 0 else 'negative_infinity'
            nonfinite.append(dict(path=path, kind=kind))
            return None
        if hasattr(item, 'model_dump'):
            item = item.model_dump(mode='json')
        if isinstance(item, dict):
            return {key: visit(child, f'{path}.{key}' if path else str(key)) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [visit(child, f'{path}.{index}' if path else str(index)) for index, child in enumerate(item)]
        return item
    result = visit(value, '')
    if nonfinite and isinstance(result, dict):
        result['nonfinite_values'] = list(result.get('nonfinite_values', [])) + nonfinite
    return result


def best_safe_partial(evidence):
    if not isinstance(evidence, dict):
        return None
    if evidence.get('best_safe_partial') is not None:
        return evidence['best_safe_partial']
    for value in evidence.values():
        if isinstance(value, dict) and (found := best_safe_partial(value)) is not None:
            return found
    return None


def normalize_ladder_work(evidence, *, profile=None):
    """Copy evidence and distinguish actual proposal trials from controller visits.

    Full-checker verdicts, instructions, completion and reported timing remain
    untouched. ``reported_status``/``reported_searched_level`` preserve controller
    wording replaced when an empty proposal loop did no new tier search.
    """
    out = deepcopy(evidence) if isinstance(evidence, dict) else {}
    profile_name = profile.get('name') if isinstance(profile, dict) else profile
    summaries = []

    def proposal_work(search):
        if not isinstance(search, dict):
            return 'unknown', 0
        proposal = search.get('proposal')
        trials = proposal.get('trials') if isinstance(proposal, dict) else None
        if not isinstance(trials, list):
            return 'unknown', 0
        work = 'searched' if trials else 'none'
        search.update(ladder_work=work, ladder_trials=len(trials))
        proposal.update(ladder_work=work, ladder_trials=len(trials))
        return work, len(trials)

    def correct_status(row, message):
        status = row.get('status')
        if isinstance(status, str) and 'within this work' in status:
            row.setdefault('reported_status', status)
            row['status'] = message

    def normalize(seam, *, portfolio=False):
        trials = seam.get('trials')
        if not isinstance(trials, list):
            status = seam.get('status')
            inactive = (seam.get('enabled') is False or status in
                        {'inactive_size_route', 'unsupported_native_domain', 'full_checker_pass'})
            if status == 'existing_witnesses_only' or inactive:
                summary = dict(ladder_work='none', searched_levels=[],
                               unsearched_levels=[] if inactive else list(range(1, 6)),
                               budget_skipped_levels=[], unknown_levels=[], unsearched_partial_levels=[],
                               ladder_trials=0, ladder_not_applicable=inactive)
                if not inactive:
                    summary['ladder_message'] = 'Only existing candidates were classified; no new tier search ran.'
                seam.update(summary)
                return summary
            return None
        observed, searched, skipped, unknown, partial_skipped = set(), set(), set(), set(), set()
        count = 0
        for trial in trials:
            if not isinstance(trial, dict):
                continue
            level = trial.get('level')
            if isinstance(level, bool) or not isinstance(level, int) or level not in range(1, 6):
                continue
            observed.add(level)
            search = trial.get('search')
            work, main_count = proposal_work(search)
            partial_work, partial_count = 'none', 0
            if isinstance(search, dict) and isinstance(search.get('partial_search'), dict):
                partial_work, partial_count = proposal_work(search['partial_search'])
                if partial_work == 'none':
                    partial_skipped.add(level)
                    search['partial_search']['ladder_message'] = (
                        f'L{level} partial search was not run; no assignment proposal trial ran.')
            count += main_count + partial_count
            work = ('searched' if 'searched' in (work, partial_work) else
                    'unknown' if 'unknown' in (work, partial_work) else 'none')
            trial.update(ladder_work=work, ladder_trials=main_count+partial_count)
            if work == 'searched':
                searched.add(level)
            elif work == 'none':
                skipped.add(level)
                message = f'L{level} was not searched; no assignment proposal trial ran.'
                trial['ladder_message'] = message
                correct_status(trial, message)
            else:
                unknown.add(level)
        work = 'searched' if searched else 'unknown' if unknown else 'none'
        summary = dict(ladder_work=work, searched_levels=sorted(searched),
                       unsearched_levels=sorted((set(range(1, 6))-observed) | skipped),
                       budget_skipped_levels=sorted(skipped),
                       unknown_levels=sorted(unknown), unsearched_partial_levels=sorted(partial_skipped),
                       ladder_trials=count, ladder_not_applicable=False)
        if not observed:
            summary['unsearched_levels'] = []
            # An outer L0 row is classification of existing witnesses, not peel work.
            if seam.get('level') == 0 or seam.get('full_checker_passed') is True:
                summary['ladder_not_applicable'] = True
        if profile_name == 'live' and not portfolio and {1, 2, 3} <= skipped:
            summary['ladder_message'] = LIVE_UNSEARCHED_LADDER_MESSAGE
        elif skipped:
            levels = ', '.join(f'L{n}' for n in sorted(skipped))
            scope = 'For this hatch mapping, ' if portfolio else ''
            summary['ladder_message'] = f'{scope}{levels} were not searched; no assignment proposal trial ran.'
        elif partial_skipped:
            summary['ladder_message'] = 'L3 partial search was not run within its proposal allowance.'
        if work == 'none' and observed:
            correct_status(seam, 'No tier search ran; this is the best candidate already found.')
        for row in (seam, seam.get('completed'), seam.get('best_safe_partial')):
            if not isinstance(row, dict):
                continue
            row.update(summary)
            if 'searched_level' in row and not unknown:
                row.setdefault('reported_searched_level', row['searched_level'])
                row['searched_level'] = max(searched) if searched else None
            if work == 'none' and observed:
                correct_status(row, 'No tier search ran; this is the best candidate already found.')
        return summary

    def visit(value, found, *, portfolio=False):
        if isinstance(value, list):
            for child in value:
                visit(child, found, portfolio=portfolio)
            return
        if not isinstance(value, dict):
            return
        seam = value.get('peel_seam')
        if isinstance(seam, dict):
            summary = normalize(seam, portfolio=portfolio)
            if summary is not None:
                found.append(summary)
        for key, child in value.items():
            if key not in {'peel_seam', 'hatch_mapping_trials'} and isinstance(child, (dict, list)):
                visit(child, found, portfolio=portfolio)

    mappings = out.get('hatch_mapping_trials')
    portfolio = isinstance(mappings, list) and bool(mappings)
    visit(out, summaries, portfolio=portfolio)
    if portfolio:
        mapping_work = []
        for position, mapping in enumerate(mappings, 1):
            found = []
            visit(mapping, found, portfolio=True)
            if found:
                summary = found[0]
            else:
                applicable = isinstance(mapping, dict) and mapping.get('status') == 'checked_plan'
                summary = dict(ladder_work='none' if applicable else 'unknown',
                               searched_levels=[], unsearched_levels=[], budget_skipped_levels=[],
                               unknown_levels=[] if applicable else list(range(1, 6)),
                               unsearched_partial_levels=[], ladder_trials=0, ladder_not_applicable=applicable)
            index = mapping.get('index', position) if isinstance(mapping, dict) else position
            if isinstance(mapping, dict):
                mapping.update(summary)
            mapping_work.append(dict(index=index, **summary))
        searched = set().union(*(set(row['searched_levels']) for row in mapping_work))
        unknown = set().union(*(set(row['unknown_levels']) for row in mapping_work))-searched
        def shared(key):
            return set.intersection(*(set(row[key]) for row in mapping_work))
        skipped = shared('budget_skipped_levels')
        summary = dict(ladder_work='searched' if searched else
                       'unknown' if any(row['ladder_work'] == 'unknown' for row in mapping_work) else 'none',
                       searched_levels=sorted(searched), unsearched_levels=sorted(shared('unsearched_levels')-searched-unknown),
                       budget_skipped_levels=sorted(skipped), unknown_levels=sorted(unknown),
                       unsearched_partial_levels=sorted(shared('unsearched_partial_levels')),
                       ladder_trials=sum(row['ladder_trials'] for row in mapping_work),
                       ladder_not_applicable=all(row['ladder_not_applicable'] for row in mapping_work),
                       ladder_mapping_work=mapping_work)
        if profile_name == 'live' and {1, 2, 3} <= skipped:
            summary['ladder_message'] = LIVE_UNSEARCHED_LADDER_MESSAGE
        elif any(row['budget_skipped_levels'] or row['unsearched_partial_levels'] for row in mapping_work):
            notes = []
            for row in mapping_work:
                label = f"Hatch mapping {row['index']}"
                if row['budget_skipped_levels']:
                    levels = ', '.join(f'L{level}' for level in row['budget_skipped_levels'])
                    notes.append(f'{label} skipped {levels}')
                if row['searched_levels']:
                    levels = ', '.join(f'L{level}' for level in row['searched_levels'])
                    notes.append(f'{label} searched {levels}')
                if row['unsearched_partial_levels']:
                    levels = ', '.join(f'L{level}' for level in row['unsearched_partial_levels'])
                    notes.append(f'{label} did not run partial search at {levels}')
            summary['ladder_message'] = '; '.join(notes) + '.'
        out.pop('ladder_message', None)
        out.update(summary)
        return out
    if not summaries and isinstance(out.get('trials'), list):
        summary = normalize(out)
        if summary is not None:
            summaries.append(summary)
    if summaries:
        out.update(summaries[0])
    elif out.get('status') == 'checked_plan' or (out.get('passed') is True and out.get('level') == 0):
        out.update(ladder_work='none', searched_levels=[], unsearched_levels=[],
                   budget_skipped_levels=[], unknown_levels=[], unsearched_partial_levels=[],
                   ladder_trials=0, ladder_not_applicable=True)
    else:
        out.update(ladder_work='unknown', searched_levels=[], unsearched_levels=[],
                   budget_skipped_levels=[], unknown_levels=[], unsearched_partial_levels=[],
                   ladder_trials=0, ladder_not_applicable=False)
    return out

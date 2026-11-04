"""Selected additive settings and separate runner-owned engine wall caps."""
from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path

CALIBRATION = Path(__file__).with_name('fixtures') / 'profile-selection.json'


def measured_profile(name):
    """Read completed calibration or an explicit leader choice from stopped rows."""
    if not CALIBRATION.is_file():
        return None
    data = json.loads(CALIBRATION.read_text())
    leader_selected = (
        data.get('decision_authority') == 'leader'
        and data.get('decision_kind') == 'explicit_partial_study_selection'
        and data.get('user_authorized') is True and data.get('stopped') is True
        and isinstance(data.get('observed_completed_rows'), int)
        and data['observed_completed_rows'] > 0)
    if (data.get('schema_version') != 'stowlm.profile-selection/1' or data.get('measured') is not True
            or not (data.get('completeness', {}).get('complete') or leader_selected)
            or not data.get('data_sha256')):
        raise ValueError('Profile selection is incomplete or unauthenticated by its measurement record')
    from experiments.mvp.pipeline.binding.adapter import verify_manifest, VERSION
    identity = verify_manifest()
    if (not identity['passed'] or data['engine_version'] != VERSION or
            data['engine_manifest_sha256'] != identity['manifest_sha256']):
        raise ValueError('Measured profiles do not match the current pinned engine')
    selected = data['profiles'][name]
    from engine.config import Config
    kwargs = dict(selected['engine_kwargs'])
    kwargs['config'] = Config(**kwargs['config']) if kwargs['config'] is not None else None
    timeout = float(selected['engine_timeout_s'])
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Measured engine deadline must be positive and finite')
    profile = dict(kwargs, name=name, engine_timeout_s=timeout, pending_measurement=False,
                target_wall_s=180. if name == 'live' else 900.,
                calibration_source=str(CALIBRATION), data_sha256=data['data_sha256'],
                measured_budget_label=selected['budget_label'],
                measurement=selected, reasoning=data['reasoning'],
                notes='Measured development profile. Engine deadline uses observed engine times plus 20% '
                      'rounded up to 30 seconds; final checks and packaging follow the engine deadline. '
                      'Shared-machine timings may improve on a quiet machine.')
    if leader_selected:
        profile.pop('pending_measurement')
        profile.update(selection_kind=data['decision_kind'],
            measurement_status='leader_selected_from_partial_study',
            target_wall_s=timeout, completeness=data['completeness'],
            notes=f"Leader-selected settings from {data['observed_completed_rows']} completed rows of the stopped partial development study. "
                  'The runner enforces the chosen engine wall cap independently of native work budgets. '
                  'Admission precedes the cap; final checking and packaging follow it. '
                  'A timeout returns the best checked incumbent after fresh rechecks, if one exists. '
                  'GPU work and other jobs loaded the machine; recorded timings are treated as '
                  'upper bounds for quieter-machine planning, not guaranteed quiet-run limits. '
                  'Measured engine settings are preserved exactly, including the ladder allowances.')
    return profile

def budget_profile(budget_s: float) -> dict:
    """Allocate additive stage budgets, without claiming a total wall-time cap."""
    if isinstance(budget_s, bool) or not math.isfinite(budget_s) or budget_s <= 0:
        raise ValueError('The engine budget must be a positive finite number of seconds')
    from engine.config import Config
    scale = budget_s / 180.
    config = Config(assignment_budget_s=60. * scale, assignment_finalize_s=20. * scale,
                    assignment_tries=1, lift_polish_s=5. * scale,
                    peel_proposal_s=20. * scale, peel_finalize_s=10. * scale,
                    peel_candidates=1, proof_outer_s=10. * scale,
                    post_pass_exchange_s=5. * scale, area_securing_s=10. * scale)
    return dict(budget_s=30. * scale, finalize_s=10. * scale, seed=0, config=config,
                pending_measurement=True, target_wall_s=float(budget_s),
                notes='Provisional additive settings. Engine floors and atomic checker calls can overrun the target.')


def _live_ladder_allowance(profile):
    from engine.config import Config
    current = profile['config'] if profile['config'] is not None else Config()
    if current.peel_proposal_s >= 70.:
        return profile
    revised = dict(profile, config=replace(current, peel_proposal_s=70.), pending_measurement=True)
    revised['peel_budget_choice'] = dict(
        measurement='projection', previous_proposal_s=current.peel_proposal_s, proposal_s=70.,
        source='engine/peel_ladder/assignment_proposal.py and assignment_method.py',
        reason='The assignment loop needs at least 20 s remaining. L3 first uses 70% of '
               'the proposal allowance; 70 s leaves a nominal 21 s for partial search before '
               'setup overhead. This enables an attempt but does not guarantee one. '
               'This provisional change is not measured profile calibration.')
    if profile.get('pending_measurement') is False:
        revised['measured_base_profile'] = True
        revised['notes'] += (' Other settings and the deadline retain their measured selection. '
                             'The runtime peel allowance is overridden to 70 s; this ladder adjustment '
                             'is unmeasured and the resulting complete profile awaits calibration.')
    return revised


def get_profile(name: str) -> dict:
    if name not in {'live', 'standard'}:
        raise ValueError(f'Unknown pipeline profile: {name}')
    measured = measured_profile(name)
    if measured is not None:
        if measured.get('selection_kind') == 'explicit_partial_study_selection':
            return measured
        return _live_ladder_allowance(measured) if name == 'live' else measured
    profile = budget_profile(180. if name == 'live' else 900.)
    if name == 'standard':
        profile['config'] = replace(profile['config'], assignment_tries=2, peel_candidates=2)
    profile['engine_timeout_s'] = 720. if name == 'live' else 900.
    if name == 'live':
        profile = _live_ladder_allowance(profile)
        profile['measured_demo'] = dict(
            measurement='measured', source='fixtures/cli-measurement.json',
            whole_run_s=623.4100180829992, engine_wall_s=547.5519343339838,
            first_incumbent_s=536.1251680409769, admission_load1=40.52,
            enabled_cores=12,
            limitation='Shared-machine development run; not profile calibration.')
        profile['notes'] += (
            ' The 720 s engine timeout is a configured allowance above the recorded '
            '623.4 s whole demo run and 536.1 s first checked incumbent; '
            'the 180 s target was not met. Admission waits happen before the cap starts. '
            'Live peel proposal allowance is provisionally 70 s: the earlier 20 s '
            'allowance can enter the frozen assignment loop zero times. The existing '
            'demo timing predates this change; it is not a measurement of the revised ladder.')
    return dict(profile, name=name)


def engine_kwargs(profile: dict) -> dict:
    return {key: profile[key] for key in ('budget_s', 'finalize_s', 'seed', 'config')}

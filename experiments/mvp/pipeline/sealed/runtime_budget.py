"""D276 conditional timing receipt; never a cap-calibration or reader receipt."""
from __future__ import annotations

import argparse
import copy
import math
from pathlib import Path

from .common import HERE, HarnessError, atomic_json, object_hash, read_json, sha256
from . import projection_31

OUTPUT = HERE/'measurements/runtime-budget-D276.json'
AUTHORIZATION = 'd276_44_row_projection_authorized'
DEADLINES = dict(pipeline=1200,instruction=1200,twostep=180,twostep_300=420)


def stable_projection(value):
    value = copy.deepcopy(value)
    value.pop('computed_utc',None)
    return value


def _profile_identity():
    from dataclasses import asdict, is_dataclass
    from experiments.mvp.pipeline.runner.profiles import get_profile
    profile = {k:asdict(v) if is_dataclass(v) else v for k,v in get_profile('standard').items()}
    kwargs = {k:profile.get(k) for k in ('budget_s','finalize_s','seed','config')}
    if (profile.get('name') != 'standard' or profile.get('budget_s') != 300
            or profile.get('finalize_s') != 100 or profile.get('seed') != 0
            or profile.get('engine_timeout_s') != 1800):
        raise HarnessError('D276 timing requires registered D265 standard300/100/1800 profile')
    return object_hash(profile),object_hash(kwargs)


def _validate_projection(value, engine_kwargs_sha256):
    projection_31.validate_sample(value.get('rows',[]))
    cells = value.get('per_cohort_arm',[])
    if (len(cells) != 8 or {(r.get('cohort'),r.get('arm')) for r in cells} != set(projection_31.SAMPLE)
            or any(r.get('sample_rows') != projection_31.SAMPLE.get((r.get('cohort'),r.get('arm')))
                   or r.get('usable_timing_rows') != r.get('sample_rows') for r in cells)
            or value.get('status') != 'conditional_projection' or value.get('workers') != 4
            or value.get('registered_trials') != 186 or value.get('confidence_interval') is not False
            or value.get('overall_projection_status') != 'conditional_projection_including_measured_guard_model'):
        raise HarnessError('D276 timing requires all44 measured rows across all eight cells')
    explicit = [r for r in value['rows'] if r.get('recorded_profile_sha256')]
    # Historical report profiles omit the top-level Config. Their actual recipe
    # survives inside measurement.engine_kwargs; never turn that omission into
    # an invented config=None or silently waive the recipe check.
    raw_rows = read_json(projection_31.STUDY/'run/rows.json')
    projection_31.validate_sample(raw_rows)
    profiles = {(r['map_id'],r['arm']):r['profile'] for r in raw_rows if r.get('profile')}
    if len(explicit) != 17 or len(profiles) != 17 or any(
            object_hash(profiles.get((r['map_id'],r['arm']),{}).get('measurement',{}).get('engine_kwargs')) != engine_kwargs_sha256
            or r.get('recorded_profile',{}).get('name') != 'standard'
            or r['recorded_profile'].get('engine_timeout_s') != 1800 for r in explicit):
        raise HarnessError('D276 recorded standard profile differs; absent timeout profiles cannot be invented')
    missing = [r for r in value['rows'] if r['arm'] in {'pipeline','instruction'}
               and not r.get('recorded_profile_sha256')]
    if len(missing) != 5 or any(r.get('child_deadline_censored') is not True for r in missing):
        raise HarnessError('D276 timeout profile accounting differs')
    hours = value.get('projection_hours_range')
    if (not isinstance(hours,list) or len(hours) != 2
            or any(type(v) not in (int,float) or not math.isfinite(v) or v <= 0 for v in hours)
            or hours[0] > hours[1] or not value.get('guard_model')
            or value.get('assumptions',{}).get('serial_reader_reserve_s') != 1800):
        raise HarnessError('D276 needs finite conditional range, measured guard model and reader reserve')
    study = value.get('recorded_study_configuration',{})
    if (study.get('workers') != 4 or study.get('profile') != 'standard'
            or study.get('arm_deadlines_s') != DEADLINES or study.get('audit_timeout_s') != 300
            or study.get('prepass_timeout_s') != 1800):
        raise HarnessError('D276 recorded study deadlines differ')


def build_receipt(*, projection=None, projection_path=projection_31.OUTPUT):
    value = projection_31.build_projection() if projection is None else projection
    profile_hash,kwargs_hash = _profile_identity()
    _validate_projection(value,kwargs_hash)
    persisted = read_json(projection_path)
    if object_hash(stable_projection(persisted)) != object_hash(stable_projection(value)):
        raise HarnessError('D276 projection artifact differs from recomputed public inputs')
    return dict(schema_version='sealed-runtime-budget/3',decision='D276',split='development',
        label='Conditional projection from loaded-machine44-row timing study; not a confidence interval',
        fixture_surrogate=False,planners_stubbed=False,study_complete=False,
        timing_sample_complete=True,sample_rows=44,supplemental_rows_used=0,
        confidence_interval=False,cap_calibration_waived=False,reader_inference_measured=False,
        workers=4,cpu_cores=18,profile='standard',profile_sha256=profile_hash,
        engine_kwargs_sha256=kwargs_hash,binding_sha256=sha256(HERE.parent/'binding/adapter.py'),
        engine_wall_s=1800,arm_deadlines_s=DEADLINES,audit_timeout_s=300,prepass_timeout_s=1800,
        twostep_s=15,twostep_finalize_s=15,serial_reader_reserve_s=1800,
        final_idle_condition=dict(enabled_cores=18,max_load1=6,other_fleet_work=[]),
        estimated_hours_range=value['projection_hours_range'],
        cache_uncertainty_sensitivity_hours=value['cache_uncertainty_sensitivity_hours'],
        admission_wait='Additional and unbounded; excluded from range.',
        projection_path=str(Path(projection_path).resolve()),projection_sha256=sha256(projection_path),
        projection_stable_sha256=object_hash(stable_projection(value)),
        all_input_hashes=value['source']['all_input_hashes'],
        code_sha256={**value['source']['code_sha256'],str(Path(__file__).resolve()):sha256(__file__),
                     str(HERE/'budget.py'):sha256(HERE/'budget.py')},
        per_cohort_arm=value['per_cohort_arm'],per_arm_projection=value['per_arm_projection'])


def validate_receipt(receipt,config):
    prereg = read_json(config.get('preregistration',HERE/'preregistration.json'))
    if prereg.get('execution',{}).get('runtime_budget',{}).get('status') != AUTHORIZATION:
        raise HarnessError('D27644-row runtime projection needs pre-open registration authorization')
    if (config.get('workers') != 4 or config.get('profile') != 'standard'
            or config.get('arm_deadlines_s') != DEADLINES or config.get('audit_timeout_s') != 300
            or config.get('prepass_timeout_s') != 1800
            or config.get('twostep_s') != 15 or config.get('twostep_finalize_s') != 15):
        raise HarnessError('D276 runtime config differs from registered profile/workers/deadlines')
    expected = build_receipt()
    if object_hash(receipt) != object_hash(expected):
        raise HarnessError('D276 runtime receipt drift: regenerate from unchanged public timing sources')
    return dict(workers=4,estimated_hours=expected['estimated_hours_range'][1],
        estimated_hours_range=expected['estimated_hours_range'],label=expected['label'],receipt=receipt)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=OUTPUT)
    args=parser.parse_args()
    if not args.out.resolve().is_relative_to(HERE):
        raise HarnessError('Runtime receipt output must stay inside sealed/')
    receipt=build_receipt()
    atomic_json(args.out,receipt)
    print(f"{args.out}: {receipt['estimated_hours_range']} hours; conditional, admission additional")


if __name__ == '__main__':
    main()

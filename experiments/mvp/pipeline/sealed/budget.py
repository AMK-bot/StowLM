"""Accept a measured development batch budget; never invent a machine hour."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import math

from .common import HERE, HarnessError, object_hash, read_json, sha256

DEFAULT_WORKERS = 2
MAX_WORKERS = 16  # Enabled allocation minus two is also enforced at admission.
POPULATIONS = {'tiny15': 15, 'small30': 30, 'envelope24': 24}


def standard_profile_hash():
    from experiments.mvp.pipeline.runner.profiles import get_profile
    profile = {k: asdict(v) if is_dataclass(v) else v for k,v in get_profile('standard').items()}
    return object_hash(profile)


def measured_budget(config):
    from .driver import public_path
    if not config.get('runtime_measurement'):
        raise HarnessError('Runtime budget awaits binding development measurements')
    receipt = read_json(public_path(config['runtime_measurement']))
    if receipt.get('schema_version') == 'sealed-runtime-budget/3':
        from .runtime_budget import validate_receipt
        return validate_receipt(receipt,config)
    if receipt.get('schema_version') == 'sealed-runtime-budget/2':
        return _measured_native(receipt,config)
    if (receipt.get('schema_version') != 'sealed-runtime-budget/1'
            or receipt.get('split') != 'development' or receipt.get('fixture_surrogate') is not False
            or receipt.get('workers') != config['workers']
            or receipt.get('admission_load_limit') != config['workers'] + 2
            or receipt.get('no_other_fleet_work') is not True
            or receipt.get('includes_serial_readings') is not True
            or receipt.get('sensitivity_finalize_s') != 300
            or receipt.get('includes_admission_and_audits') is not True
            or type(receipt.get('gpu_running')) is not bool):
        raise HarnessError('Runtime receipt must describe real admitted development batches and audits')
    cores = 12 if receipt['gpu_running'] else 18
    if receipt.get('cpu_cores') != cores or not 1 <= config['workers'] <= min(cores - 2, MAX_WORKERS):
        raise HarnessError('Runtime receipt worker/core allocation differs')
    if (receipt.get('profile_sha256') != standard_profile_hash()
            or receipt.get('binding_sha256') != sha256(HERE.parent/'binding/adapter.py')
            or receipt.get('twostep_s') != config['twostep_s']
            or receipt.get('twostep_finalize_s') != config['twostep_finalize_s']):
        raise HarnessError('Runtime measurement recipe differs from the frozen recipe')
    cohorts = receipt.get('cohorts', {})
    if set(cohorts) != set(POPULATIONS):
        raise HarnessError('Measure all development cohorts, including the instruction block')
    seconds = 0.
    for name, count in POPULATIONS.items():
        row = cohorts[name]
        n, elapsed = row.get('maps'), row.get('elapsed_s')
        if (type(n) is not int or n < 1 or type(elapsed) not in (int, float)
                or not math.isfinite(elapsed) or elapsed <= 0
                or row.get('all_registered_arms') is not True):
            raise HarnessError('Each cohort needs measured end-to-end batches of all its arms')
        # elapsed is the whole batch at the stated workers, not summed process
        # time. Admission and audits are included; never divide by workers again.
        seconds += count * elapsed / n
    return dict(workers=config['workers'], estimated_hours=seconds/3600,
                label='projection from measured development batches', receipt=receipt)


def _measured_native(receipt,config):
    from .real_dry_run import DEADLINES, WORKERS
    if (receipt.get('split') != 'development' or receipt.get('fixture_surrogate') is not False
            or receipt.get('study_complete') is not True or receipt.get('harness_errors') != 0
            or receipt.get('planners_stubbed') is not False or receipt.get('workers') != WORKERS
            or config['workers'] != WORKERS or receipt.get('profile_sha256') != standard_profile_hash()
            or receipt.get('binding_sha256') != sha256(HERE.parent/'binding/adapter.py')
            or receipt.get('twostep_s') != config['twostep_s']
            or receipt.get('twostep_finalize_s') != config['twostep_finalize_s']
            or receipt.get('arm_deadlines_s') != config.get('arm_deadlines_s')
            or receipt.get('arm_deadlines_s') != DEADLINES
            or receipt.get('serial_reader_reserve_s') != 1800
            or receipt.get('reader_inference_measured') is not False
            or receipt.get('final_idle_condition') != dict(enabled_cores=18,max_load1=6,other_fleet_work=[])):
        raise HarnessError('Native runtime study differs from the registered recipe or idle-release condition')
    rows = receipt.get('per_map',[])
    work = 0.
    longest = 0.
    expected = {(c,a) for c in POPULATIONS for a in
        (('pipeline','twostep','instruction','twostep_300') if c=='envelope24' else ('pipeline','twostep'))}
    if {(r.get('cohort'),r.get('arm')) for r in rows} != expected or len(rows) != len(expected):
        raise HarnessError('Native timing study must measure every registered cohort/arm')
    for row in rows:
        if (type(row.get('n')) is not int or row['n'] < (8 if row['cohort']=='envelope24' else 3)
                or any(type(row.get(k)) not in (int,float) or not math.isfinite(row[k]) or row[k]<=0
                       for k in ('mean_s','max_s')) or row['max_s'] < row['mean_s']):
            raise HarnessError('Native timing cells need measured samples and finite wall times')
        work += POPULATIONS[row['cohort']]*row['mean_s']
        longest = max(longest,row['max_s'])
    return dict(workers=WORKERS,estimated_hours=(work/WORKERS+longest+1800)/3600,
        label='projection from measured four-worker slot times;1800s reader reserve and scheduling tail; admission additional',
        receipt=receipt)

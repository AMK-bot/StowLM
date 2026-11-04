"""Read-only artifact audit of all 18 completed D217 scale rows."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ARM = HERE.parent
ROOT = ARM.parents[3]
sys.path.insert(0, str(ROOT / 'src'))
sys.dont_write_bytecode = True
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS'):
    os.environ[name] = '1'
WEIGHTS = 'da15edecf1061c2e2014317ccaf53190f6c2997b5f225f8ca39bd5174a6559d6'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def main():
    from stowage.model import MovePlan
    from stowage.versions import component_versions
    spec = importlib.util.spec_from_file_location('d217_scale_admission_audit', ARM.parent / 'k1b/skill-check/admission.py')
    admission = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(admission)
    manifest = read(HERE / 'manifest.json')
    assert manifest['frozen_weights_sha256'] == WEIGHTS == sha(ARM / 'frozen-source/fitted.json')
    jobs = manifest['inputs']
    assert len(jobs) == 18 and len({j['map_id'] for j in jobs}) == 18
    assert read(HERE / 'DONE.json')['maps'] == 18
    expected = {j['map_id'] for j in jobs}
    assert {p.parent.name for p in (HERE / 'maps').glob('*/row.json')} == expected
    checks, artifacts, epochs, sources = [], 0, set(), set()
    rows = []
    for job in jobs:
        folder = HERE / 'maps' / job['map_id']
        row = read(folder / 'row.json')
        decoded = read(folder / 'decode.json')
        assert row['evaluated'] is True and row['map_id'] == job['map_id']
        assert row['input'] == job['input'] and '/maps/train/medium/' in row['input'] and 'test' not in row['input']
        assert sha(ROOT / job['input']) == job['input_sha256'] == row['binding']['input_sha256']
        assert row['binding']['weights_sha256'] == WEIGHTS and decoded['binding'] == row['binding']
        for name, digest in row['binding']['code_sha256'].items():
            assert sha(ARM / name) == digest, f'changed experiment source {name}'
        assert row['decode'] == decoded['metadata']
        assert row['complete'] == (decoded['plan'] is not None) == (row['decode']['status'] == 'complete')
        for name in ('decode_seconds', 'evaluation_seconds', 'plan_seconds'):
            assert math.isfinite(row[name]) and row[name] >= 0
        assert abs(row['plan_seconds'] - row['decode_seconds'] - row['evaluation_seconds']) < .000001
        historic = read(ROOT / row['comparators']['model82']['source'])
        assert historic['binding']['input_sha256'] == job['input_sha256']
        assert sha(ROOT / row['comparators']['model82']['source']) == row['comparators']['model82']['source_sha256']
        if row['complete']:
            evaluation = read(folder / 'evaluation/result.json')
            assert row['evaluation'] == evaluation
            assert evaluation['binding']['input_sha256'] == job['input_sha256']
            assert evaluation['binding']['weights_sha256'] == WEIGHTS and evaluation['binding']['finisher_s'] == 600
            model = MovePlan.model_validate(decoded['plan'])
            assert model.model_dump(mode='json') == decoded['plan'] == read(folder / 'evaluation/raw-plan.json')
            canonical_raw_sha = hashlib.sha256(json.dumps(decoded['plan'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            # Sorted artifact JSON loses dict insertion order. Rebuild exactly
            # the engine's original action and provenance-version key order;
            # this changes no value and reproduces its noncanonical byte hash.
            assert all(set(action) == {'op', 'box', 'slot'} for action in model.actions)
            model.actions = [{key: action[key] for key in ('op', 'box', 'slot')} for action in model.actions]
            versions = model.provenance.versions
            version_order = list(component_versions()) + ['feasibility-config']
            assert set(versions) == set(version_order)
            model.provenance.versions = {key: versions[key] for key in version_order}
            assert model.model_dump(mode='json') == decoded['plan']
            original_raw_sha = hashlib.sha256(model.model_dump_json().encode()).hexdigest()
            assert original_raw_sha == evaluation['binding']['plan_sha256']
            assert row['passed'] == evaluation['passed'] and row['gate_clear'] == evaluation['gate_clear']
            assert row['moves'] == evaluation.get('moves')
            epochs.add(evaluation['binding']['outcome_epoch'])
            sources.add(json.dumps(evaluation['binding']['code'], sort_keys=True))
            evidence = evaluation['admission']
            checked = admission.validate_snapshot(evidence, now_s=evidence['read_at'])
            assert checked['admitted'] is True
            assert Path(evidence['source']).resolve() == (ROOT / 'experiments/fleet/core-state.json').resolve()
            assert evidence['core_count'] == int(evidence['source_payload']['mask']).bit_count()
            assert 0 <= evidence['age_s'] < 120 and evidence['core_count'] >= 8 and evidence['load1'] <= 45
            for name, digest in evaluation['artifacts'].items():
                assert sha(folder / 'evaluation' / name) == digest
                artifacts += 1
            if row['passed']:
                assert evaluation['mask_replay'] is True
                report = read(folder / 'evaluation/report.json')
                assert report['passed'] is True
                assert not [v for v in report['violations'] if v.get('severity') == 'hard']
                assert not [k for k, value in report['parts'].items() if value.get('error')]
                assert row['moves'] == report['count']['weighted_nonproductive']
                checks.append(dict(map_id=row['map_id'], canonical_raw_plan_sha256=canonical_raw_sha, original_plan_binding_sha256=original_raw_sha, parts=len(report['parts']),
                                   parts_ran=sum(value.get('ran') is True for value in report['parts'].values()),
                                   mask_replay=True, admission=evidence))
        else:
            assert row['passed'] is False and row['gate_clear'] is False and row['moves'] is None
            assert row['evaluation_seconds'] == 0
            assert not (folder / 'evaluation/result.json').exists()
        rows.append(row)
    summary = read(HERE / 'summary.json')
    assert summary['status'] == 'FINAL' and summary['evaluated'] == 18
    for key in ('complete', 'gate_clear', 'passed'):
        assert summary[key] == sum(row[key] is True for row in rows)
    assert len(epochs) == 1 and len(sources) == 1
    result = dict(verified=True, verified_utc=datetime.now(timezone.utc).isoformat(), maps=18,
                  complete=summary['complete'], gate_clear=summary['gate_clear'], passed=summary['passed'],
                  weights_sha256=WEIGHTS, input_hashes_verified=18, decode_and_row_bindings_verified=18,
                  finished_artifact_hashes_verified=artifacts, checker_epochs=sorted(epochs),
                  checker_code_bindings=len(sources), full_checker_and_mask_passes=checks,
                  total_decode_seconds=sum(row['decode_seconds'] for row in rows),
                  total_evaluation_compute_seconds=sum(row['evaluation_seconds'] for row in rows),
                  rule_complete_model_incomplete=[row['map_id'] for row in rows if row['complete'] and not row['comparators']['model82']['complete']],
                  rule_pass_model_fail=[row['map_id'] for row in rows if row['passed'] and not row['comparators']['model82']['passed']])
    (HERE / 'verification.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({key: result[key] for key in ('verified', 'maps', 'complete', 'gate_clear', 'passed', 'finished_artifact_hashes_verified', 'checker_epochs')}))


if __name__ == '__main__':
    main()

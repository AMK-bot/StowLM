"""Real pipeline-test failures remain diagnostics, never placeholder plans."""
import json
from pathlib import Path
import shutil

import pytest

from app.capture import checked_replay
from app.flow import DemoService
from app.inputs import load_inputs

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'experiments/sft-pilot/pipeline-test/c1-loop'
ENTRY = 'instances/v3.random_yard.tiny.110280000/result.json'


def test_real_failed_model_run_imports_without_inventing_plan():
    loaded = load_inputs(run_folder=RUN, entry=ENTRY)
    assert loaded.plan is None
    assert loaded.metadata['label'] == 'StowLM (diagnostic adapter, not evidence)'
    assert loaded.metadata['planner_result']['status'] == 'error'
    payload = checked_replay(loaded)
    assert payload['plan'] is None and payload['frames'] == []
    assert payload['planner_result']['status'] == 'error'
    assert payload['metadata']['shippable'] is False
    assert payload['metadata']['departure_approved'] is False
    assert payload['checker']['bound'] is False
    assert payload['metadata']['model_run']['instance_bound'] is True
    assert payload['metadata']['model_run']['trace']


def test_failed_model_run_service_retains_diagnostic_and_rejects_comparison():
    loaded = load_inputs(run_folder=RUN, entry=ENTRY)
    service = DemoService(loaded.instance, loaded.plan, loaded.metadata, discover=False)
    assert service.replay(service.active_plan_id)['plan'] is None
    assert service.catalog()['plans'][0]['label'] == loaded.metadata['label']
    with pytest.raises(ValueError, match='(?i)no plan'):
        service.comparison(service.active_plan_id, service.active_plan_id)


def test_failed_model_run_config_content_mismatch_rejected(tmp_path):
    copied = tmp_path/'run'
    shutil.copytree(RUN, copied)
    config_path = copied/'config.json'
    config = json.loads(config_path.read_text())
    config['inputs'][0]['sha256'] = '0'*64
    config_path.write_text(json.dumps(config))
    original = ROOT/json.loads((RUN/'config.json').read_text())['inputs'][0]['path']
    with pytest.raises(ValueError, match='(?i)(checksum|sha256|content)'):
        load_inputs(run_folder=copied, entry=ENTRY, instance_path=original)


def test_nonmodel_failed_result_is_still_rejected(tmp_path):
    path = tmp_path/'result.json'
    path.write_text(json.dumps({'status': 'error', 'plan': None, 'planner': 'solver.scalable',
                               'planner_version': 'test'}))
    with pytest.raises(ValueError, match='no plan'):
        load_inputs(plan_path=path)


def test_failed_model_run_unwitnessed_result_input_checksum_rejected(tmp_path, replay_case):
    from app.tests.test_model_runs import run_folder
    root, path, record = run_folder(tmp_path, replay_case)
    record['result'].update(status='error', plan=None)
    record['input_sha256'] = '0'*64
    record.pop('report')
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='(?i)(checksum|witness|sha256)'):
        load_inputs(run_folder=root, selected_instance=replay_case[0])


def test_failed_model_run_requires_genuine_model_manifest(tmp_path, replay_case):
    root = tmp_path/'ordinary-run'
    root.mkdir()
    (root/'manifest.json').write_text(json.dumps({'experiment': 'solver'}))
    path = root/'result.json'
    path.write_text(json.dumps({'status': 'error', 'plan': None, 'planner': 'solver.scalable',
                               'planner_version': 'test'}))
    with pytest.raises(ValueError, match='(?i)(model|manifest)'):
        load_inputs(run_folder=root, selected_instance=replay_case[0])

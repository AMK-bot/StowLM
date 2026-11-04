"""Historical base-mask corrections must not become current-rules evidence."""
import importlib.util
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'experiments/base-mask/runs/official-base-bo32-small-c2'


def test_saved_historical_summary_counts_every_sample_and_disclaims_current_rules():
    summary = json.loads((RUN / 'summary.json').read_text())
    for population in (summary, summary['by_rung']['small']):
        assert population['mask_dead_end_count'] == 168
        assert population['mask_sample_count'] == 192
        assert population['mask_dead_end_sample_rate'] == .875
        assert population['mask_dead_end_instance_rate'] == 1.0
    assert summary['official'] is False
    assert summary['historically_official'] is True
    assert summary['current_rules_qualified'] is False
    assert 'HISTORICAL' in (RUN / 'ROW.md').read_text()
    audit = json.loads((RUN / 'provenance-audit.json').read_text())
    assert audit['n_inputs_matching_config_bytes'] == 6
    assert audit['n_inputs_with_voyage'] == 0
    assert audit['n_current_v2_members'] == 0
    assert audit['n_reports_with_current_binding'] == 0
    assert audit['full_checker_rerun'] is False


def load_auditor():
    spec = importlib.util.spec_from_file_location('basemask_historical_audit',
        ROOT / 'experiments/base-mask/audit_historical.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_historical_audit_is_repeatable_and_preserves_original_evidence(tmp_path):
    module = load_auditor()
    target = tmp_path / 'run'
    shutil.copytree(RUN, target)
    evidence = [target / 'config.json', *sorted((target / 'instances').glob('*.json'))]
    before = {p: p.read_bytes() for p in evidence}
    first = module.correct_run(target)
    assert first['n_inputs_matching_config_bytes'] == 6
    written = {p.name: p.read_bytes() for p in target.iterdir() if p.is_file()}
    assert module.correct_run(target) == first
    assert {p.name: p.read_bytes() for p in target.iterdir() if p.is_file()} == written
    assert all(p.read_bytes() == value for p, value in before.items())


def test_historical_audit_rejects_changed_input_bytes(tmp_path):
    module = load_auditor()
    audit = module.audit_run(RUN)
    source_root = tmp_path / 'inputs'
    shutil.copytree(ROOT / 'experiments/base-mask/instances-v3-val-c2-small', source_root)
    changed = source_root / audit['inputs'][0]['source_relative_path']
    changed.write_bytes(changed.read_bytes() + b'\n')
    result = module.audit_run(RUN, inputs_dir=source_root)
    assert result['n_inputs_matching_config_bytes'] == 5
    assert result['current_rules_qualified'] is False
    assert any(row['input_bytes_match_config'] is False for row in result['inputs'])

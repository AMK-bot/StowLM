import json

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file
from experiments.mvp.pipeline.copilot.hero_postfix import load_postfix_scenarios


def test_postfix_loader_reads_new_frozen_inputs_and_rejects_drift(tmp_path):
    path, marker = tmp_path/'scenarios.json', tmp_path/'freeze.json'
    path.write_text(json.dumps({'scenarios':[{'id':'new-input'}]}))
    marker.write_text(json.dumps({'scenarios_sha256':sha256_file(path)}))
    assert load_postfix_scenarios(path, marker) == [{'id':'new-input'}]
    path.write_text(json.dumps({'scenarios':[{'id':'changed'}]}))
    with pytest.raises(ValueError, match='definitions changed'):
        load_postfix_scenarios(path, marker)


def test_postfix_inputs_are_disjoint_and_keep_original_goals():
    from pathlib import Path
    root = Path(__file__).parent
    original = json.loads((root/'fixtures/hero-scenarios.json').read_text())['scenarios']
    new = load_postfix_scenarios()
    assert len(new) == len(original) == 8
    assert not {row['id'] for row in original} & {row['id'] for row in new}
    assert sorted(row['goal'] for row in new) == sorted(row['goal'] for row in original)

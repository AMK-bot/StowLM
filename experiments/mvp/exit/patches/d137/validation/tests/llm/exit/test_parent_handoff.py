import json
from pathlib import Path

import pytest

from llm.exit import resources


def policy(tmp_path,monkeypatch):
    monkeypatch.setattr(resources,'ROOT',tmp_path)
    target=tmp_path/'experiments/mvp/exit/deployment/parent-policy.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(dict(retired_parents=['pilot2/adapter'],
        completion_marker='sft81/DONE.json')))
    return target


def test_retired_pilot_watcher_cannot_enter_gpu_path(tmp_path,monkeypatch):
    policy(tmp_path,monkeypatch)
    with pytest.raises(RuntimeError,match='parent retired'):
        resources.require_current_parent(tmp_path/'pilot2/adapter')
    resources.require_current_parent(tmp_path/'sft81/adapter')


def test_pilot_marker_does_not_satisfy_81_gpu_admission(tmp_path,monkeypatch):
    policy(tmp_path,monkeypatch)
    marker=tmp_path/'experiments/mvp/runs/sft-pilot2-small/polish_summary.json'
    marker.parent.mkdir(parents=True);marker.write_text('{}')
    ledger=tmp_path/'experiments/fleet/ledger.md'
    ledger.parent.mkdir(parents=True);ledger.write_text('')
    monkeypatch.setattr(resources,'probe',lambda:dict(load_1m=0,memory_free_percent=99))
    out=tmp_path/'job';out.mkdir()
    with pytest.raises(RuntimeError,match='required parent'):
        with resources.reservation(out,cores=1,ram_gb=1,gpu=True,estimate_s=1):
            pytest.fail('8.1 completion absent')
    assert not json.loads((out/'admission.json').read_text())['admitted']

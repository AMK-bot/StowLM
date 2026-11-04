"""Calibration release gate uses only synthetic local evidence."""
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.sealed import budget, calibration, instructions, worker
from experiments.mvp.pipeline.sealed.common import HarnessError, atomic_json, object_hash, sha256
from experiments.mvp.pipeline.sealed.instructions import CAP_REDUCTION


def evidence(tmp_path, monkeypatch, broken):
    here=tmp_path/'sealed'
    here.mkdir()
    (here/'instructions.py').write_text('fixed instructions')
    binding=tmp_path/'binding'
    binding.mkdir()
    (binding/'adapter.py').write_text('fixed binding')
    maps=tmp_path/'maps'
    maps.mkdir()
    monkeypatch.setattr(calibration,'HERE',here)
    monkeypatch.setattr(budget,'standard_profile_hash',lambda:'profile')
    rule=dict(schema_version='stowlm.reader/2',constraint=dict(type='max_tiers',bay=1,zone='deck',max_tiers=2))
    monkeypatch.setattr(worker,'_load',lambda job:SimpleNamespace(id=job['path']))
    monkeypatch.setattr(instructions,'public_facts',lambda instance:{'id':instance.id})
    monkeypatch.setattr(instructions,'select_rule',lambda instance,rank:rule)
    items=[]
    for index in range(32):
        path=maps/f'dev-{index:02}.json'
        path.write_text('{}')
        audit=here/f'audit-{index:02}.json'
        atomic_json(audit,dict(map_sha256=sha256(path),arm='pipeline',audit_verified=True,
            audit_fresh_process=True,audit_pid=2,planning_pid=1,passed=True,
            full_checker_pass=True,level=0,counterfactual_rule=rule,
            counterfactual_rule_sha256=object_hash(rule),
            counterfactual_instruction_recheck=index>=broken))
        items.append(dict(map_path=str(path),map_sha256=sha256(path),audit_row=str(audit),
                          audit_sha256=sha256(audit),rule_sha256=object_hash(rule),cap_reduction=CAP_REDUCTION))
    (maps/'dev.txt').write_text('\n'.join(r['map_path'] for r in items)+'\n')
    path=here/'calibration.json'
    atomic_json(path,dict(schema_version='sealed-tier-calibration/1',split='development',
        fixture_surrogate=False,cap_reduction=CAP_REDUCTION,profile_sha256='profile',
        instructions_sha256=sha256(here/'instructions.py'),binding_sha256=sha256(binding/'adapter.py'),
        development_list_sha256=sha256(maps/'dev.txt'),evidence=items))
    return path


@pytest.mark.parametrize('broken',[0,16,17,32])
def test_amend_cap_requires_strict_majority_of_entire_development_cohort(tmp_path,monkeypatch,broken):
    path=evidence(tmp_path,monkeypatch,broken)
    if broken<=16:
        with pytest.raises(HarnessError,match='most development'):
            calibration.verify_calibration(path)
    else:
        result=calibration.verify_calibration(path)
        assert result['binding']==broken and result['maps']==32
        assert len(result['files'])==65


def test_amend_calibration_cannot_select_successful_development_maps(tmp_path,monkeypatch):
    from experiments.mvp.pipeline.sealed.common import read_json
    path=evidence(tmp_path,monkeypatch,32)
    receipt=read_json(path)
    receipt['evidence'].pop()
    atomic_json(path,receipt)
    with pytest.raises(HarnessError,match='every development'):
        calibration.verify_calibration(path)


def test_amend_calibration_recomputes_rule_and_pins_audit_bytes(tmp_path,monkeypatch):
    from experiments.mvp.pipeline.sealed.common import read_json
    path=evidence(tmp_path,monkeypatch,32)
    receipt=read_json(path)
    audit=receipt['evidence'][0]['audit_row']
    atomic_json(audit,{'changed':'after calibration'})
    with pytest.raises(HarnessError,match='hash differs'):
        calibration.verify_calibration(path)
    path2=tmp_path/'other'
    path2.mkdir()
    path=evidence(path2,monkeypatch,32)
    monkeypatch.setattr(instructions,'select_rule',lambda instance,rank:{'wrong':'rule'})
    with pytest.raises(HarnessError,match='most development'):
        calibration.verify_calibration(path)

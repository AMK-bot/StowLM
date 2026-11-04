"""D49: outer screens need independently measured opposite residuals."""
from copy import deepcopy
import hashlib

import numpy as np
import pytest

from stowage.linear import margin_policy as policy
from stowage.linear.qualification import qualify_holdout
from tests.linear.test_margin_policy import context, bank_for


def necessary_bank(context, tmp_path):
    bank = bank_for(context, tmp_path)
    _, model, diagnostics = context
    for side in policy._SIDES:
        opposite = side.replace("unsafe", "conservatism")
        diagnostics["signed_errors"][opposite] = deepcopy(diagnostics["signed_errors"][side])
        diagnostics["signed_errors"][opposite]["gm_m"] = 2. if side == "lower_unsafe" else .3
    measured = policy._zeros(2)
    for side in policy._SIDES:
        measured[side] = deepcopy(diagnostics["signed_errors"][side.replace("unsafe", "conservatism")])
    transfer = policy._zeros(2)
    qualification = qualify_holdout(measured, transfer, deepcopy(measured),
        station_x_m=model.station_x_m, calibration_seed=1, holdout_seed=2,
        calibration_samples=128, holdout_samples=64)
    qualification["direction"] = "necessary"
    edge=deepcopy(qualification)
    edge.update(kind='necessary-domain-edges-v1',holdout_seed=32)
    bank["entries"]["response"]["necessary"] = dict(format=1,
        direction="necessary", physical_optimistic=measured, reference_transfer=transfer,
        holdout_qualification=qualification,edge_holdout_qualification=edge)
    return bank


def test_necessary_reserves_use_opposite_gm_residual_not_unsafe(context, tmp_path, monkeypatch):
    family, model, diagnostics = context
    bank = necessary_bank(context, tmp_path)
    monkeypatch.setattr(policy, "_boundary_list", lambda *args, **kwargs:
        {"lower_unsafe": 3., "upper_unsafe": 4.})
    bank["entries"]["response"]["necessary"]["boundary_list"] = {"direction": "necessary"}
    diagnostics["margin_policy"] = policy.qualify_policy(family, None, diagnostics, bank=bank)
    conservative = policy.default_reserves(model, direction="conservative")
    necessary = policy.default_reserves(model, direction="necessary")
    assert conservative["lower_unsafe"]["gm_m"] == pytest.approx(.011022)
    assert necessary["lower_unsafe"]["gm_m"] == pytest.approx(2.2)
    assert necessary["list_moment_kgm"]["lower_unsafe"] == 3.


def test_missing_necessary_qualification_never_reuses_conservative_policy(context, tmp_path):
    family, model, diagnostics = context
    diagnostics["margin_policy"] = policy.qualify_policy(family, None, diagnostics,
        bank=bank_for(context, tmp_path))
    with pytest.raises(ValueError, match="necessary.*qualification"):
        policy.default_reserves(model, direction="necessary")


def test_necessary_fails_closed_without_boundary_evidence(context, tmp_path):
    family, _, diagnostics = context
    with pytest.raises(ValueError, match="necessary.*boundary"):
        policy.qualify_policy(family, None, diagnostics, bank=necessary_bank(context, tmp_path))


def test_unknown_margin_direction_is_rejected(context):
    _, model, _ = context
    with pytest.raises(ValueError, match="direction"):
        policy.default_reserves(model, direction="optimistic")


def test_necessary_signed_mapping_cannot_infer_missing_opposite_gm():
    with pytest.raises(ValueError, match="optimistic"):
        policy.optimistic_envelope(policy._zeros(1), 1)


def test_necessary_requires_independent_edge_holdout_certificate(context,tmp_path,monkeypatch):
    family,model,diagnostics=context
    bank=necessary_bank(context,tmp_path)
    monkeypatch.setattr(policy,'_boundary_list',lambda *a,**kw:
        {'lower_unsafe':0.,'upper_unsafe':0.})
    record=bank['entries']['response']['necessary']
    record['boundary_list']={'direction':'necessary'}
    record.pop('edge_holdout_qualification',None)
    diagnostics['margin_policy']=policy.qualify_policy(family,None,diagnostics,bank=bank)
    assert 'necessary' not in diagnostics['margin_policy']
    assert 'N1' in diagnostics['margin_policy']['necessary_unavailable']
    assert policy.default_reserves(model,direction='conservative') is not None
    with pytest.raises(ValueError,match='necessary.*qualification'):
        policy.default_reserves(model,direction='necessary')


def test_malformed_present_edge_certificate_still_rejects(context,tmp_path,monkeypatch):
    family,_,diagnostics=context
    bank=necessary_bank(context,tmp_path)
    monkeypatch.setattr(policy,'_boundary_list',lambda *a,**kw:
        {'lower_unsafe':0.,'upper_unsafe':0.})
    record=bank['entries']['response']['necessary']
    record['boundary_list']={'direction':'necessary'}
    record['edge_holdout_qualification']=None
    with pytest.raises(ValueError,match='necessary.*edge'):
        policy.qualify_policy(family,None,diagnostics,bank=bank)


def test_necessary_runtime_recomputes_edge_holdout_breach(context,tmp_path,monkeypatch):
    family,_,diagnostics=context
    bank=necessary_bank(context,tmp_path)
    monkeypatch.setattr(policy,'_boundary_list',lambda *a,**kw:
        {'lower_unsafe':0.,'upper_unsafe':0.})
    record=bank['entries']['response']['necessary']
    record['boundary_list']={'direction':'necessary'}
    record['edge_holdout_qualification']['holdout_unsafe']['lower_unsafe']['gm_m']=99.
    with pytest.raises(ValueError,match='holdout failed.*gm_m'):
        policy.qualify_policy(family,None,diagnostics,bank=bank)


@pytest.mark.parametrize('shared_stale',[False,True])
def test_legacy_outer_builder_change_preserves_only_conservative_evidence(
        context,tmp_path,shared_stale):
    family,model,diagnostics=context
    bank=necessary_bank(context,tmp_path)
    entry=bank['entries']['response']
    entry['necessary'].pop('edge_holdout_qualification')
    entry['evidence'].append(dict(
        path='experiments/linear-model/epoch_requalification/necessary.py',sha256='0'*64))
    if shared_stale:
        entry['evidence'][0]['sha256']='0'*64
        with pytest.raises(ValueError,match='checksum'):
            policy.qualify_policy(family,None,diagnostics,bank=bank)
    else:
        diagnostics['margin_policy']=policy.qualify_policy(family,None,diagnostics,bank=bank)
        assert len(diagnostics['margin_policy']['evidence']) == 1
        assert policy.default_reserves(model,direction='conservative') is not None
        with pytest.raises(ValueError,match='necessary.*qualification'):
            policy.default_reserves(model,direction='necessary')


def test_partitioned_outer_source_change_does_not_poison_conservative(
        context,tmp_path,monkeypatch):
    family,model,diagnostics=context
    bank=necessary_bank(context,tmp_path)
    entry=bank['entries']['response']
    entry['conservative_evidence']=deepcopy(entry['evidence'])
    entry['necessary_evidence']=[dict(
        path='experiments/linear-model/epoch_requalification/necessary.py',sha256='0'*64)]
    entry['necessary']['boundary_list']={'direction':'necessary'}
    monkeypatch.setattr(policy,'_boundary_list',lambda *a,**kw:
        {'lower_unsafe':0.,'upper_unsafe':0.})
    diagnostics['margin_policy']=policy.qualify_policy(family,None,diagnostics,bank=bank)
    assert policy.default_reserves(model,direction='conservative') is not None
    assert 'necessary' not in diagnostics['margin_policy']
    assert 'checksum' in diagnostics['margin_policy']['necessary_unavailable']
    with pytest.raises(ValueError,match='necessary.*qualification'):
        policy.default_reserves(model,direction='necessary')


def test_necessary_evidence_is_read_once_at_qualification_not_each_row(context,tmp_path,monkeypatch):
    family,model,diagnostics=context
    bank=necessary_bank(context,tmp_path)
    entry=bank['entries']['response']
    path=tmp_path/'large-outer-observations.json';path.write_text('immutable outer observations')
    outer=[dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())]
    entry['necessary_evidence']=outer
    entry['necessary']['boundary_list']={'direction':'necessary'}
    monkeypatch.setattr(policy,'_boundary_list',lambda *a,**kw:
        {'lower_unsafe':0.,'upper_unsafe':0.})
    calls=[]
    original=policy._evidence
    def evidence(records):
        if records==outer:calls.append('outer')
        return original(records)
    monkeypatch.setattr(policy,'_evidence',evidence)
    diagnostics['margin_policy']=policy.qualify_policy(family,None,diagnostics,bank=bank)
    assert calls==['outer']
    assert policy.default_reserves(model,direction='necessary') is not None
    assert policy.default_reserves(model,direction='necessary') is not None
    assert calls==['outer']
    # Like existing conservative evidence, a source-file change is detected
    # when rebuilding qualification; row construction uses the saved digest.
    path.write_text('changed outer observations')
    assert policy.default_reserves(model,direction='necessary') is not None
    diagnostics['margin_policy']=policy.qualify_policy(family,None,diagnostics,bank=bank)
    assert 'necessary' not in diagnostics['margin_policy']
    assert policy.default_reserves(model,direction='conservative') is not None


@pytest.mark.parametrize('invalid',[None,[],[{}],[{'path':'x','sha256':None}]])
def test_malformed_outer_evidence_metadata_remains_a_hard_failure(context,tmp_path,monkeypatch,invalid):
    family,_,diagnostics=context
    bank=necessary_bank(context,tmp_path)
    entry=bank['entries']['response']
    entry['necessary_evidence']=invalid
    entry['necessary']['boundary_list']={'direction':'necessary'}
    monkeypatch.setattr(policy,'_boundary_list',lambda *a,**kw:
        {'lower_unsafe':0.,'upper_unsafe':0.})
    with pytest.raises(ValueError,match='evidence'):
        policy.qualify_policy(family,None,diagnostics,bank=bank)

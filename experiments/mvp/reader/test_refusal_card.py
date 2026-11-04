import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from llm.instructions.schema import CloseBay
from experiments.mvp.reader import refusal_card as card
from experiments.mvp.reader.schema import answer_envelope
from experiments.mvp.reader.refusal_explainer import proof_sha256


def fixture(tmp_path,monkeypatch):
    proof=dict(schema_version='stowlm.refusal-evidence/1',status='static_impossible',
               parsed=answer_envelope(CloseBay(type='close_bay',bay=2)),
               facts=[{'id':'f','text':'Needs 23 cells; 0 remain.'}],required_fact_ids=['f'],
               alternatives=[],nearest_alternative_id=None,clarifications=[])
    selection=dict(schema_version='stowlm.refusal-explanation/1',status='static_impossible',
                   statements=[{'fact_id':'f','text':'Needs 23 cells; 0 remain.'}],alternative_id=None,clarification_id=None)
    row=dict(case_id='case',passed=True,proof=proof,explanation='Needs 23 cells; 0 remain.',
             parsed={'parsed':proof['parsed']},explainer={'validated':True,'proof_sha256':proof_sha256(proof),'generated_selection':selection})
    (tmp_path/'examples').mkdir()
    path=tmp_path/'examples/case.json';path.write_text(json.dumps(row))
    monkeypatch.setattr(card,'RUN',tmp_path)
    monkeypatch.setattr(card,'verify',lambda:{'cases':[{'case_id':'case','input':'fake','sentence':'Bay2closed'}]})
    monkeypatch.setattr(card,'context_from_instance',lambda i:{'bays':[1,2]})
    import stowage.io
    monkeypatch.setattr(stowage.io,'load_instance',lambda p:NS())
    return path,row


def test_live_wrong_reading_never_releases_cached_explanation(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch)
    parser=NS(parse=Mock(return_value=(CloseBay(type='close_bay',bay=1),{'parser':'GlimmerParser'})))
    row=card.read_recorded_card('case',parser=parser)
    assert row['status']=='reading_mismatch' and not row['cached_explanation_released']
    assert 'explanation' not in row


def test_matching_live_reader_releases_only_bound_recorded_card(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch)
    parser=NS(parse=Mock(return_value=(CloseBay(type='close_bay',bay=2),{'parser':'GlimmerParser','adapter_sha256':'a'*64})))
    row=card.read_recorded_card('case',parser=parser)
    assert row['cached_explanation_released'] and row['no_planner_called']
    assert row['parser']['adapter_sha256']=='a'*64 and not row['alternative_applied']


def test_changed_cached_fact_is_rejected(tmp_path,monkeypatch):
    path,row=fixture(tmp_path,monkeypatch)
    row['proof']['facts'][0]['text']='A checker proved it.'
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError,match='proof does not match'):
        card.read_recorded_card('case',parser=NS())

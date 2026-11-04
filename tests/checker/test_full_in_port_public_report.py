"""Public and SFT report parsers retain the named D74 programme field."""
import json

import pytest

from stowage.checker.in_port_report import InPortCheckReport, InPortReport, attach_programme
from stowage.model import CheckReport, PartReport


def programme():
    return dict(changeover_k=3,lifts_after_k=7,pumped_mass_t=20.,abs_change_t=40.,
        pump_time_required_h=4.,pump_time_available_h=1.5,ballast_delay_h=2.5,
        pump_window_lifts=3,ballast_pump_m3_h=500.,t_lift_s=120.,cranes=2,
        gross_moves_per_hour=60.,heel_step_limit_t=10.,heel_max_step_t=4.,
        heel_max_ideal_step_t=15.,heel_path_feasible=True)


@pytest.mark.parametrize('fields',[None,programme()])
def test_public_check_report_restores_activated_programme(fields):
    part=(InPortReport(name='in_port') if fields is None
          else attach_programme(PartReport(name='in_port'),fields))
    active=InPortCheckReport(passed=True,parts={'in_port':part})
    wire=active.model_dump_json()
    restored=CheckReport.model_validate_json(wire)
    assert restored.model_dump_json()==wire
    restored_part=restored.parts['in_port']
    assert 'ballast_programme' in restored_part.model_dump()
    if fields is None:
        assert restored_part.ballast_programme is None
    else:
        assert restored_part.ballast_programme.ballast_delay_h==2.5
        assert restored_part.ballast_programme.changeover_k==3
        assert restored_part.ballast_programme.rate_check_rule=='port.ballast.transfer_rate'


def test_off_report_bytes_have_no_new_default_field():
    part=PartReport(name='strength',metrics={'bm_ratio':.5})
    expected=b'{"name":"strength","violations":[],"metrics":{"bm_ratio":0.5},"margins":{},"ran":true,"error":null}'
    assert part.model_dump_json().encode()==expected
    report=CheckReport(passed=True,parts={'strength':part})
    assert b'ballast_programme' not in report.model_dump_json().encode()
    assert CheckReport.model_validate_json(report.model_dump_json()).model_dump_json()==report.model_dump_json()


def test_public_parser_preserves_explicit_null_without_adding_it_to_other_parts():
    payload=dict(passed=True,parts={'in_port':dict(name='in_port',ballast_programme=None),
                                  'strength':dict(name='strength')})
    restored=CheckReport.model_validate(payload)
    encoded=json.loads(restored.model_dump_json())
    assert encoded['parts']['in_port']['ballast_programme'] is None
    assert 'ballast_programme' not in encoded['parts']['strength']


@pytest.mark.parametrize('fields',[None,programme()])
def test_real_sft_admission_public_parser_restores_programme(monkeypatch,fields):
    # Synthetic publication linkage exercises build_gold_example's real public
    # CheckReport.model_validate call, then stops before token/physics work.
    from llm.stowlm import sft_data
    from tests.llm.test_sft_data_admission import synthetic_publication
    instance,plan,record,publish=synthetic_publication()
    record['check_report']['parts']['in_port']=dict(name='in_port',ballast_programme=fields)
    monkeypatch.setattr(sft_data,'current_gold_source_sha256',lambda:'a'*64)
    original=sft_data.require_clean
    class Parsed(Exception): pass
    def observe(*objects,**kwargs):
        if kwargs.get('what')=='SFT gold checker evidence':
            report=objects[0]
            assert type(report) is CheckReport
            block=report.parts['in_port'].ballast_programme
            assert block is None if fields is None else block.ballast_delay_h==2.5
            raise Parsed
        return original(*objects,**kwargs)
    monkeypatch.setattr(sft_data,'require_clean',observe)
    with pytest.raises(Parsed):
        sft_data.build_gold_example(instance,plan,split='train',gold_record=record,
                                    publication=publish())


def test_public_parser_rejects_malformed_programme_instead_of_dropping_it():
    from pydantic import ValidationError
    fields=programme()
    fields['ballast_delay_h']='invalid'
    with pytest.raises(ValidationError,match='ballast_delay_h'):
        CheckReport.model_validate(dict(passed=True,parts={
            'in_port':dict(name='in_port',ballast_programme=fields)}))

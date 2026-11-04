import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.explain.summary import rederive, slots_for, validate_summary, write_summary

FACTS = [dict(id='mass', text='mass 12.5 kg; limit 20 kg.', th='มวล 12.5 kg; ขีดจำกัด 20 kg.', evidence=[]),
         dict(id='rank', text='weight rank 2 of 6 in BAY01.', th='อันดับน้ำหนัก 2 จาก 6 ใน BAY01.', evidence=[])]
SPEC = {'sentences': [dict(before='This relevant distinction is', slot_id='rank', after='for this placement'),
                      dict(before='The stated loading comparison is', slot_id='mass', after='in this plan')]}


class Client:
    def complete(self, prompt, **kwargs):
        return SimpleNamespace(text=json.dumps(SPEC), cost_usd=Decimal('.001'), request_id='model-request')


def test_spark_really_writes_framing_but_code_owns_values_and_labels():
    result, audit = write_summary(FACTS, client=Client())
    assert audit['accepted'] and 'This relevant distinction is' in result['text']
    assert 'This relevant distinction is: weight rank' in result['text']
    assert 'mass 12.5 kg; limit 20 kg' in result['text']
    assert rederive(result, FACTS, 'en')


@pytest.mark.parametrize('bad', ['mass 20 kg; limit 12.5 kg', 'weight rank 1', 'because it is safe',
                                'the only reason is', 'at SGSIN', 'this is two times better',
                                'The count is twelve', 'ค่าที่นับคือศูนย์', 'The share is ½',
                                'The rank is twelfth', 'The scale is one million',
                                'ค่าที่นับคือสิบเอ็ด', 'The count is Ⅻ'])
def test_summary_rejects_unbound_numeric_roles_ids_or_causal_claims(bad):
    spec = json.loads(json.dumps(SPEC))
    spec['sentences'][0]['before'] = bad
    with pytest.raises(ValueError):
        validate_summary(spec, slots_for(FACTS), 'en')


def test_rederive_rejects_source_measurement_change():
    summary = validate_summary(SPEC, slots_for(FACTS), 'en')
    other = [dict(s) for s in FACTS]
    other[0]['text'] = 'mass 15 kg; limit 20 kg.'
    other[0]['th'] = 'มวล 15 kg; ขีดจำกัด 20 kg.'
    with pytest.raises(ValueError, match='rederive'):
        rederive(summary, other, 'en')


def test_thai_cannot_exchange_the_paired_fact_roles():
    spec = {'sentences': [dict(before='ข้อมูลที่แสดงคือ', slot_id='rank', after='สำหรับตำแหน่งนี้'),
                          dict(before='ข้อมูลเปรียบเทียบคือ', slot_id='mass', after='ตามแผนนี้')]}
    summary = validate_summary(spec, slots_for(FACTS), 'th', paired_ids=['rank', 'mass'])
    assert 'มวล 12.5 kg; ขีดจำกัด 20 kg' in summary['text']
    with pytest.raises(ValueError, match='same English'):
        validate_summary(spec, slots_for(FACTS), 'th', paired_ids=['mass', 'rank'])
    spec['sentences'][0]['before'] = 'This ข้อมูลที่แสดงคือ'
    with pytest.raises(ValueError, match='English lead-ins'):
        validate_summary(spec, slots_for(FACTS), 'th', paired_ids=['rank', 'mass'])


def test_template_does_not_claim_model_authorship():
    result, audit = write_summary(FACTS)
    assert result is None and audit['source'] == 'template' and not audit['accepted']


def test_thai_writer_receives_the_actual_admitted_english_framing():
    english = validate_summary(SPEC, slots_for(FACTS), 'en')
    class ThaiClient:
        def complete(self, prompt, **kwargs):
            assert 'ENGLISH FRAMING: ' + json.dumps(english['spec'], ensure_ascii=False) in prompt
            assert 'Translate each before/after faithfully' in prompt
            spec = {'sentences': [dict(before='ข้อมูลเปรียบเทียบที่แสดงคือ', slot_id='rank', after='สำหรับตำแหน่งนี้'),
                                  dict(before='ข้อมูลการบรรทุกที่ระบุคือ', slot_id='mass', after='ตามแผนนี้')]}
            return SimpleNamespace(text=json.dumps(spec), cost_usd=Decimal('.001'), request_id='thai-request')
    result, audit = write_summary(FACTS, 'th', client=ThaiClient(), english=english)
    assert audit['accepted'] and result['slot_ids'] == english['slot_ids']

"""Regression coverage for the four rev-v5 public orchestration probes."""
from dataclasses import replace
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import pytest

from experiments.mvp.reader import public_brief as api
from llm.instructions.schema import CloseBay, Refusal

CONTEXT = {'bays':[1,3], 'hatch_covers':['A']}
PROVENANCE = {'parser':'fixture', 'adapter_sha256':'a'*64}


@pytest.fixture
def seam():
    instance = {'id':'same-id', 'cargo':['A']}
    compiled = NS(forbids=lambda *a: None, violations=lambda p: [])
    result = NS(plan='checked')
    with patch.object(api, 'context_from_instance', return_value=CONTEXT) as context, \
         patch.object(api, 'compile_constraint', return_value=(compiled, {})) as compile_, \
         patch('experiments.mvp.reader.select_binding.per_box_slot_screen', return_value=[]), \
         patch.object(api, 'plan_and_check', return_value=(result, {})) as plan:
        yield instance, context, compile_, plan, result


def parser_for(*voyage):
    empty = Refusal(code='not_an_instruction', reason='Empty standing brief')
    return NS(parse=Mock(side_effect=[(a, dict(PROVENANCE)) if not isinstance(a, Exception) else a for a in (empty, *voyage)]))


def read(instance, *answers):
    return api.read_brief(instance, api.VoyageBrief('', tuple(f'Sentence {i}' for i in range(len(answers)))), parser=parser_for(*answers))


def test_plain_string_cannot_demote_a_hard_restriction(seam):
    instance, _, compile_, plan, _ = seam
    parser = parser_for(CloseBay(type='close_bay', bay=1))
    with pytest.raises(TypeError, match='VoyageBrief'):
        api.plan_brief(instance, 'Do not load any containers in bay 01.', parser=parser)
    parser.parse.assert_not_called()
    compile_.assert_not_called()
    plan.assert_not_called()


@pytest.mark.parametrize('rejected', [api.ParserOutputError({'raw':'bad JSON'}), *[Refusal(code=code,reason='fixture') for code in ('ambiguous','unsupported','loosening','not_an_instruction')]])
def test_unresolved_voyage_blocks_even_with_other_accepted_sentences(seam, rejected):
    instance, _, compile_, plan, _ = seam
    receipt = read(instance, CloseBay(type='close_bay', bay=1), rejected)
    result, evidence = api.execute_brief(instance, receipt)
    assert result is None and evidence['passed'] is False and evidence['plan_released'] is False
    assert evidence['status'] == 'needs_confirmation'
    assert evidence['unresolved_sentence_indices'] == [2]
    assert evidence['sentences'][1]['binds'] is True
    assert evidence['sentences'][2]['binds'] is False
    assert evidence['existing_plan_satisfies_request'] is False
    compile_.assert_not_called()
    plan.assert_not_called()


def test_explicit_removal_requires_new_reader_receipt(seam):
    instance, _, _, plan, result = seam
    old = read(instance, Refusal(code='ambiguous', reason='Which bay?'))
    assert api.execute_brief(instance, old)[0] is None
    new = api.read_brief(instance, api.VoyageBrief(''), parser=parser_for())
    assert api.execute_brief(instance, new)[0] is result
    assert plan.call_args.args[1] is None
    assert api.execute_brief(instance, old)[0] is None  # acknowledgement never mutates old evidence


def test_constraints_come_from_receipt_and_cannot_be_omitted(seam):
    instance, _, compile_, plan, result = seam
    receipt = read(instance, CloseBay(type='close_bay', bay=1))
    with pytest.raises(TypeError):
        api.execute_brief(instance, receipt, [])
    got, evidence = api.execute_brief(instance, receipt)
    assert got is result
    assert compile_.call_args.args[1] == CloseBay(type='close_bay', bay=1)
    assert len(plan.call_args.args[1].parts) == 1
    assert evidence['compilation'][0]['parser'] == PROVENANCE
    assert evidence['compilation'][0]['binding'] == evidence['binding']
    # Inspection returns a copy; removing a constraint from it cannot change execution.
    copy = receipt.evidence()
    copy['sentences'].clear()
    assert len(receipt.evidence()['sentences']) == 2


@pytest.mark.parametrize('fake', [{}, {'reader_required':True,'sentences':[{'role':'voyage','binds':True}]}])
def test_minimal_fabricated_evidence_is_rejected(seam, fake):
    instance, _, _, plan, _ = seam
    with pytest.raises(ValueError, match='reader-produced'):
        api.execute_brief(instance, fake)
    plan.assert_not_called()


@pytest.mark.parametrize('field', ['sentence','parsed','parser','role','binds','status'])
def test_edits_to_reading_or_provenance_invalidate_receipt(seam, field):
    instance, _, _, plan, _ = seam
    receipt = read(instance, CloseBay(type='close_bay', bay=1))
    payload = json.loads(receipt.payload)
    payload['sentences'][1][field] = 'tampered'
    forged = replace(receipt, payload=json.dumps(payload))
    with pytest.raises(ValueError, match='reader-produced'):
        api.execute_brief(instance, forged)
    plan.assert_not_called()


def test_input_digest_includes_cargo_not_just_id_or_reader_context(seam):
    instance, _, _, plan, _ = seam
    receipt = read(instance, CloseBay(type='close_bay', bay=1))
    changed = dict(instance, cargo=['DIFFERENT'])
    with pytest.raises(ValueError, match='input/context'):
        api.execute_brief(changed, receipt)
    plan.assert_not_called()


def test_context_change_invalidates_receipt(seam):
    instance, context, _, plan, _ = seam
    receipt = read(instance, CloseBay(type='close_bay', bay=1))
    context.return_value = dict(CONTEXT, bays=[1])
    with pytest.raises(ValueError, match='input/context'):
        api.execute_brief(instance, receipt)
    plan.assert_not_called()


def test_authenticated_checkpoint_replays_across_process_keys(seam):
    instance, _, _, _, result = seam
    receipt = read(instance, CloseBay(type='close_bay', bay=1))
    key = b'fixture replay signing key ONLY!!'
    checkpoint = api.export_checkpoint(receipt, key=key)
    with patch.object(api, '_PROCESS_KEY', b'another process private key'):
        with pytest.raises(ValueError, match='reader-produced'):
            api.execute_brief(instance, receipt)
        reloaded = api.load_checkpoint(instance, checkpoint, key=key)
        assert api.execute_brief(instance, reloaded)[0] is result
        assert reloaded.payload == receipt.payload


def test_checkpoint_cannot_be_forged_tampered_or_moved_to_another_input(seam):
    instance, _, _, plan, _ = seam
    receipt = read(instance, CloseBay(type='close_bay', bay=1))
    key = b'fixture replay signing key ONLY!!'
    checkpoint = api.export_checkpoint(receipt, key=key)
    with pytest.raises(ValueError, match='authentication'):
        api.load_checkpoint(instance, dict(checkpoint, payload=checkpoint['payload']+' '), key=key)
    with pytest.raises(ValueError, match='authentication'):
        api.load_checkpoint(instance, checkpoint, key=b'wrong signing key with 32 bytes!!')
    with pytest.raises(ValueError, match='input/context'):
        api.load_checkpoint(dict(instance,cargo=[]), checkpoint, key=key)
    with pytest.raises(ValueError, match='dedicated'):
        api.export_checkpoint(receipt, key=b'short')
    plan.assert_not_called()


def test_fabricated_typed_receipt_cannot_claim_a_reader_ran(seam):
    instance, _, _, plan, _ = seam
    with pytest.raises(ValueError, match='reader-produced'):
        api.execute_brief(instance, api.ReadBrief('{}', '0'*64))
    plan.assert_not_called()


@pytest.mark.parametrize('provider', [{'parser':'fixture'}, {'parser':'fixture','adapter_sha256':'bad'}, {'adapter_sha256':None}])
def test_missing_or_invalid_parser_provenance_blocks_hard_request(seam, provider):
    instance, _, compile_, plan, _ = seam
    parser = parser_for(CloseBay(type='close_bay', bay=1))
    parser.parse.side_effect = [(Refusal(code='not_an_instruction',reason='Empty'), PROVENANCE),
                                (CloseBay(type='close_bay',bay=1), provider)]
    result, evidence = api.plan_brief(instance, api.VoyageBrief('', ('Close bay 1',)), parser=parser)
    assert result is None and evidence['status'] == 'needs_confirmation'
    compile_.assert_not_called()
    plan.assert_not_called()


def test_unadapted_parser_has_explicit_null_adapter_provenance(seam):
    instance, _, _, _, result = seam
    parser = NS(parse=Mock(return_value=(CloseBay(type='close_bay',bay=1), {'parser':'fixture','adapter_sha256':None})))
    got, evidence = api.plan_brief(instance, api.VoyageBrief('', ('Close bay 1',)), parser=parser)
    assert got is result
    assert evidence['compilation'][0]['parser']['adapter_sha256'] is None


def test_third_party_out_of_context_answer_is_rejected_without_guess(seam):
    instance, _, compile_, plan, _ = seam
    receipt = read(instance, CloseBay(type='close_bay',bay=99))
    result, evidence = api.execute_brief(instance, receipt)
    assert result is None and evidence['status'] == 'needs_confirmation'
    assert evidence['sentences'][1]['status'] == 'malformed'
    assert evidence['sentences'][1]['parsed'] is None
    compile_.assert_not_called()
    plan.assert_not_called()


def test_real_instance_receipt_is_bound_to_full_model_serialization():
    from pathlib import Path
    from stowage.io import load_instance
    instance = load_instance(Path('experiments/mvp/corpus/val/small/feeder_500/simulated_yard/seed-040100002.json'))
    context = api.context_from_instance(instance)
    parser = parser_for(CloseBay(type='close_bay',bay=context['bays'][0]))
    receipt = api.read_brief(instance, api.VoyageBrief('', ('Close the first bay',)), parser=parser)
    # No search: prove that the ordinary Instance model supports issuance and verification.
    evidence = api._verify(instance, receipt)
    assert len(evidence['binding']['input_sha256']) == 64
    assert evidence['binding']['input_sha256'] != evidence['binding']['context_sha256']


@pytest.mark.parametrize('kind', ['spend_cap','spend_ledger','unavailable','provider_value_error','network'])
def test_provider_failures_propagate_even_for_empty_standing_only(seam, kind):
    from llm.spark.spend import SpendCapExceeded, SpendError
    from experiments.mvp.reader.pipeline import ParserUnavailableError
    failure = {'spend_cap':SpendCapExceeded('Fixture cap'), 'spend_ledger':SpendError('Fixture ledger'),
               'unavailable':ParserUnavailableError('Fixture adapter'), 'provider_value_error':ValueError('Fixture provider failure'),
               'network':OSError('Fixture network')}[kind]
    instance, _, compile_, plan, _ = seam
    parser = NS(parse=Mock(side_effect=failure))
    with pytest.raises(type(failure)):
        api.plan_brief(instance, api.VoyageBrief(''), parser=parser)
    compile_.assert_not_called()
    plan.assert_not_called()

from experiments.mvp.pipeline.contract.schema import IntakeResult
from .private_intake import PrivateIntake
from . import private_intake
from .schema import compact_json
from .test_decoder import ROW

BASE = {'rotation': {'calls': [{'locode': 'THLCH'}, {'locode': 'SGSIN'}]}, 'call_index': 0,
        'vessel': {'allowed_iso_types': ['22G1'], 'allowed_imdg_classes': ['3']},
        'containers': [{'id': ROW['id'], 'gross_mass_kg': 999}]}


def test_mock_local_model_and_shared_validator():
    calls = []
    def model(text, *, context, messages, schema):
        assert context['pods'] == ['SGSIN']
        assert text == messages[-1]['content']
        assert schema['anyOf']
        return compact_json(ROW)
    def validator(raw, documents, base):
        calls.append(raw)
        assert base['containers'] == BASE['containers']
        assert base['load_list'] == []
        assert 'load_list' not in BASE
        assert documents[0]['text'] == 'explicit synthetic fragment'
        assert next(o['value'] for o in raw['observations'] if o['field'] == 'gross_mass_kg') == ROW['gross_mass_kg']
        return {'rows': [ROW], 'questions': [], 'field_sources': []}
    result = PrivateIntake(model, validator=validator).read_text('explicit synthetic fragment', base_instance=BASE)
    assert isinstance(result, IntakeResult)
    assert result.status == 'complete'
    assert result.rows == [ROW]
    assert len(calls) == 1
    assert result.records[0].stage == 'intake_private'
    assert result.records[0].cost_usd == 0
    assert result.model_dump(mode='json')['records'][0]['mode'] == 'live'


def test_model_refusal_is_not_filled_from_base():
    def model(*args, **kwargs):
        return {'refusal': {'code': 'MISSING_FIELD'}}
    def validator(*args):
        raise AssertionError('no validator for model refusal')
    result = PrivateIntake(model, validator=validator).read_text('unknown mass', base_instance=BASE)
    assert result.rows == []
    assert result.questions[0].reason == 'MISSING_FIELD'


def test_missing_shared_validator_fails_closed(monkeypatch):
    def unavailable():
        raise ImportError('task 1.3 pending')
    monkeypatch.setattr(private_intake, '_validator', unavailable)
    result = PrivateIntake(lambda *a, **kw: ROW).read_text('synthetic', base_instance=BASE)
    assert result.rows == []
    assert result.questions[0].reason == 'VALIDATOR_UNAVAILABLE'


def test_validator_exception_and_invalid_model_fail_closed():
    def broken(*args):
        raise ValueError('invalid DG mapping')
    result = PrivateIntake(lambda *a, **kw: ROW, validator=broken).read_text('synthetic', base_instance=BASE)
    assert result.rows == []
    assert result.questions[0].reason == 'VALIDATOR_REJECTED'
    result = PrivateIntake(lambda *a, **kw: '{}').read_text('synthetic', base_instance=BASE)
    assert result.questions[0].reason == 'INVALID_OUTPUT'


def test_no_loaded_model_returns_question():
    result = PrivateIntake().read_text('synthetic', base_instance=BASE)
    assert result.status == 'needs_answers'
    assert result.questions[0].reason == 'MODEL_UNAVAILABLE'


def real_case():
    from pathlib import Path
    from stowage.model.instance import Instance
    from .schema import ROW_KEYS
    path = Path('experiments/mvp/frontier/lab/sets/quick_small.txt').read_text().splitlines()[0]
    base = Instance.model_validate_json(Path(path).read_text())
    box = next(box for box in base.containers if box.id in base.load_list and box.dg is None and not box.reefer)
    full = box.model_dump(mode='json')
    proposal = {key: full[key] for key in ROW_KEYS}
    proposal['gross_mass_kg'] = round(proposal['gross_mass_kg'], 3)
    return base, box, proposal


def test_real_task13_validator_completes_one_row_from_explicit_base_facts():
    base, box, proposal = real_case()
    proposal['gross_mass_kg'] -= 1
    text = compact_json(proposal)
    result = PrivateIntake(lambda *a, **kw: proposal).read_text(text, base_instance=base)
    assert result.status == 'complete', result.questions
    assert len(result.rows) == 1
    actual = result.rows[0]
    assert actual['id'] == box.id
    assert actual['tare_kg'] == box.tare_kg
    assert actual['gross_mass_kg'] == proposal['gross_mass_kg']
    assert next(s for s in result.field_sources if s['field'] == 'tare_kg')['document'] == 'base-container'
    assert next(s for s in result.field_sources if s['field'] == 'gross_mass_kg')['document'] == 'private-text'
    assert next(b for b in base.containers if b.id == box.id).gross_mass_kg != proposal['gross_mass_kg']


def test_unknown_plate_rating_names_the_missing_field():
    base, box, proposal = real_case()
    data = base.model_dump(mode='json')
    next(b for b in data['containers'] if b['id'] == box.id)['max_gross_mass_kg'] = None
    result = PrivateIntake(lambda *a, **kw: proposal).read_text(compact_json(proposal), base_instance=data)
    assert result.status == 'needs_answers'
    assert any(q.field == 'max_gross_mass_kg' and q.reason == 'missing_csc_rating' for q in result.questions)


def test_incomplete_base_names_the_unavailable_required_field():
    base, box, proposal = real_case()
    data = base.model_dump(mode='json')
    del next(b for b in data['containers'] if b['id'] == box.id)['tare_kg']
    result = PrivateIntake(lambda *a, **kw: proposal).read_text(compact_json(proposal), base_instance=data)
    assert result.status == 'needs_answers'
    assert any(q.field == 'tare_kg' and q.reason == 'BASE_FIELD_UNAVAILABLE' for q in result.questions)


def test_new_dg_declaration_does_not_inherit_old_cargo_metadata():
    base, box, proposal = real_case()
    proposal['dg'] = {'un_number': '1263', 'imdg_class': '3'}
    result = PrivateIntake(lambda *a, **kw: proposal).read_text(compact_json(proposal), base_instance=base)
    assert result.status == 'needs_answers'
    assert any(q.field == 'dg.stowage_category' for q in result.questions)
    assert 'stowage_category' not in result.rows[0]['dg']


def test_matching_dg_uses_only_base_metadata_not_declared_un_or_class():
    from .private_intake import validation_input
    row = dict(ROW, dg={'un_number': '1263', 'imdg_class': '3'})
    base = {**BASE, 'containers': [{**row, 'tare_kg': 2200,
            'dg': {**row['dg'], 'stowage_category': 'A', 'packing_group': 'II'}}]}
    raw, documents, _ = validation_input(row, compact_json(row), base)
    inherited = [o for o in raw['observations'] if o['document'] == 'base-container']
    assert {o['field'] for o in inherited} == {'tare_kg', 'dg.stowage_category', 'dg.packing_group'}
    row['dg'] = {'un_number': '1789', 'imdg_class': '8'}
    raw, _, _ = validation_input(row, compact_json(row), base)
    assert not any(o['field'].startswith('dg.') and o['document'] == 'base-container' for o in raw['observations'])


def test_local_glimmer_hook_rejects_failed_gate_without_loading_model(tmp_path):
    import json
    import pytest
    from .private_intake import LocalGlimmerModel
    (tmp_path / 'gates.json').write_text(json.dumps({'all_pass': False, 'decision': 'cut'}))
    (tmp_path / 'tuned.json').write_text('{}')
    with pytest.raises((ValueError, FileNotFoundError)):
        LocalGlimmerModel(tmp_path, results=tmp_path)('synthetic', context={}, messages=[], schema={})


def test_local_model_checks_adoption_before_loading(monkeypatch, tmp_path):
    import pytest
    from . import artifacts, evaluate
    def reject(*args, **kwargs):
        raise ValueError('frozen adapter configuration changed')
    monkeypatch.setattr(artifacts, 'verify_adoption', reject)
    monkeypatch.setattr(evaluate, 'run_glimmer', lambda *a, **kw: pytest.fail('model loaded before verification'))
    with pytest.raises(ValueError, match='configuration'):
        private_intake.LocalGlimmerModel(tmp_path)('synthetic', context={}, messages=[], schema={})

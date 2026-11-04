import json

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file
from experiments.mvp.pipeline.runner.assembly import AssemblyError, assemble, assembly_metadata
from stowage.conventions import make_container_id
from stowage.model.fixtures import build_instance


@pytest.fixture
def packet(tmp_path):
    instance = build_instance()
    path = tmp_path / 'base.json'
    path.write_text(instance.model_dump_json(indent=2))
    return dict(packet_id='synthetic.assembly', created_utc='2026-11-01T00:00:00Z',
                base_instance=dict(path=str(path), sha256=sha256_file(path)))


def rows(packet):
    instance = assemble(packet)
    return [box.model_dump(mode='json') for box in instance.containers if box.id in instance.load_list]


def test_no_replacements_and_exact_intake_hash_equal(packet):
    base = assemble(packet)
    assert assembly_metadata(packet)['hash_equal']
    packet['load_list'] = dict(origin='intake', rows=rows(packet))
    packet['yard_state'] = dict(origin='vision', state=base.yard.model_dump(mode='json'))
    result = assemble(packet)
    assert result == base
    metadata = assembly_metadata(packet, result)
    assert metadata['hash_equal']
    assert metadata['base_instance_sha256'] == metadata['assembled_instance_sha256']
    assert metadata['replacements'] == dict(load_list=True, yard_state=True)


def test_replacements_keep_nonload_yard_attributes(packet):
    original = rows(packet)
    replacement = dict(original[0], gross_mass_kg=original[0]['gross_mass_kg'] + 100)
    packet['load_list'] = dict(origin='intake', rows=[replacement])
    result = assemble(packet)
    assert result.load_list == [replacement['id']]
    assert len(result.containers) == 4
    assert next(box for box in result.containers if box.id == replacement['id']).gross_mass_kg == replacement['gross_mass_kg']
    assert not assembly_metadata(packet, result)['hash_equal']


def test_wrong_base_hash_rejected_before_load(packet):
    packet['base_instance']['sha256'] = '0' * 64
    with pytest.raises(AssemblyError, match='hash mismatch') as caught:
        assemble(packet)
    assert caught.value.status == 'error'


def test_empty_replacement_load_rows_are_a_question(packet):
    packet['load_list'] = dict(origin='intake', rows=[])
    with pytest.raises(AssemblyError, match='load list is empty') as caught:
        assemble(packet)
    assert caught.value.status == 'needs_answers'
    assert caught.value.questions[0].field == 'load_list.rows'
    assert 'at least one' in caught.value.questions[0].prompt


def test_omitted_load_list_preserves_base_cargo(packet):
    assert assemble(packet).load_list == build_instance().load_list


def test_sealed_path_and_symlink_never_read(packet, tmp_path):
    sealed = tmp_path / 'sealed'
    sealed.mkdir()
    path = sealed / 'map.json'
    path.write_text('secret not valid JSON')
    link = tmp_path / 'disguised.json'
    link.symlink_to(path)
    packet['base_instance']['path'] = str(link)
    with pytest.raises(AssemblyError, match='Sealed maps'):
        assemble(packet)


def test_unknown_intake_identity_is_a_question(packet):
    row = dict(rows(packet)[0], id=make_container_id('SYNU', 100))
    packet['load_list'] = dict(origin='intake', rows=[row])
    with pytest.raises(AssemblyError) as caught:
        assemble(packet)
    assert caught.value.status == 'needs_answers'
    assert caught.value.questions[0].row == 0
    assert 'no matching yard identity' in caught.value.questions[0].reason


def test_new_identity_requires_matching_yard(packet):
    base = assemble(packet)
    row = dict(rows(packet)[0], id=make_container_id('SYNU', 100))
    yard = base.yard.model_dump(mode='json')
    yard['stacks'][0]['boxes'] = [row['id']]
    packet['yard_state'] = dict(origin='vision', state=yard)
    packet['load_list'] = dict(origin='intake', rows=[row])
    assert assemble(packet).load_list == [row['id']]


def test_unknown_yard_identity_needs_attributes(packet):
    base = assemble(packet)
    yard = base.yard.model_dump(mode='json')
    yard['stacks'][0]['boxes'] = [make_container_id('SYNU', 100)]
    packet['yard_state'] = dict(origin='vision', state=yard)
    with pytest.raises(AssemblyError) as caught:
        assemble(packet)
    assert caught.value.status == 'needs_answers'
    assert 'no container attributes' in caught.value.questions[0].reason


@pytest.mark.parametrize('change', ['duplicate', 'bad_mass', 'bad_yard', 'wrong_length'])
def test_ambiguous_or_invalid_inputs_become_questions(packet, change):
    replacement = rows(packet)
    if change == 'duplicate':
        replacement.append(replacement[0])
    elif change == 'bad_mass':
        replacement[0]['gross_mass_kg'] = -1
    elif change == 'bad_yard':
        packet['yard_state'] = dict(origin='vision', state={'yard_id': 'x', 'stacks': [dict(key='bad')]})
    else:
        replacement[0].update(length_ft=20, iso_type='22G1', height_m=2.591)
        replacement[0]['reefer_setpoint_c'] = None
    packet['load_list'] = dict(origin='intake', rows=replacement)
    with pytest.raises(AssemblyError) as caught:
        assemble(packet)
    assert caught.value.status == 'needs_answers'
    assert caught.value.questions
    json.dumps([row.model_dump(mode='json') for row in caught.value.questions])

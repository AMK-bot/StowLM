import pytest

from .sequence_provenance import frame_provenance


@pytest.mark.parametrize('producer', ['lattice.sequence', 'lattice.sequence_blocks', 'lattice.recover_candidate'])
def test_direct_repair_is_not_native_engine_output(producer):
    provenance = frame_provenance(dict(plan_producer=producer, gate={'gauges': {'n_frames': 144, 'passed': True, 'all_frames': True}}))
    assert provenance['label'] == 'crane sequence repaired'
    assert provenance['native_engine_output'] is False
    assert provenance['frames_checked'] == 144 and provenance['all_frames_passed']
    assert provenance['frame_claim_scope'] == 'crane frames walked on this plan'


def test_unmodified_native_plan_is_labelled_separately_from_repaired_base():
    provenance = frame_provenance(dict(plan={'id': 'engine-plan'}, base_plan={'id': 'base.lattice-sequence'}))
    assert provenance['label'] == 'crane sequence not repaired'
    assert provenance['native_engine_output'] is True
    assert provenance['base_sequence']['label'] == 'crane sequence repaired'


def test_explicit_repair_metadata_and_embedded_gate_metadata_count():
    assert frame_provenance({'sequence_repair': {'original_plan_sha256': 'original'}})['repaired']
    assert frame_provenance({'gate': {'sequence_repair': {'producer': 'lattice.sequence'}}})['repaired']


def test_pool_inherits_only_selected_donor_sequence_not_failed_requested_sequence():
    row = dict(plan_producer='lattice.standard_plan_pool', sequence_repair=False,
               standard_plan_pool=dict(requested={'record': {'plan_producer': 'lattice.sequence'}},
                                       donor={'record': {'plan': {'id': 'native'}}}))
    provenance = frame_provenance(row)
    assert provenance['label'] == 'crane sequence not repaired'
    assert provenance['native_engine_output'] is False  # portfolio selection is separately produced
    row['standard_plan_pool']['donor']['record']['plan_producer'] = 'lattice.sequence_blocks'
    assert frame_provenance(row)['label'] == 'crane sequence repaired'
    assert frame_provenance(row)['source'] == 'standard_plan_pool.donor.record'


def test_base_record_provenance_is_separate_and_input_not_mutated():
    row = {'plan': {'id': 'native'}}
    base = {'plan_producer': 'lattice.recover_candidate'}
    provenance = frame_provenance(row, base_record=base)
    assert provenance['base_sequence']['repaired'] and not provenance['repaired']
    assert row == {'plan': {'id': 'native'}} and base == {'plan_producer': 'lattice.recover_candidate'}


def test_pending_request_does_not_claim_native_output_or_a_frame_verdict():
    provenance = frame_provenance({'status': 'pending'})
    assert provenance['native_engine_output'] is None
    assert provenance['frames_checked'] is None and provenance['all_frames_passed'] is None

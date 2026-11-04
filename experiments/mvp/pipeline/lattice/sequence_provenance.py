"""Presentation-only crane provenance; this never grants a gate acceptance."""
from __future__ import annotations


REPAIR_PRODUCERS = frozenset(('lattice.sequence', 'lattice.sequence_blocks', 'lattice.recover_candidate'))


def _repair(record):
    producer = record.get('plan_producer') or record.get('producer')
    repair = record.get('sequence_repair') or (record.get('gate') or {}).get('sequence_repair')
    plan_id = (record.get('plan') or {}).get('id', '')
    named = isinstance(plan_id, str) and any(marker in plan_id for marker in ('.lattice-sequence', '.lattice-block-sequence'))
    repaired = producer in REPAIR_PRODUCERS or bool(repair) or named
    return dict(repaired=repaired, label='crane sequence repaired' if repaired else 'crane sequence not repaired',
                producer=producer, evidence='producer' if producer in REPAIR_PRODUCERS else
                'sequence_repair' if repair else 'recorded_plan_id' if named else 'no recorded sequence repair')


def frame_provenance(row, *, base_record=None):
    """Describe selected-plan frames separately from its comparison baseline.

    Portfolio selection inherits the donor's crane sequence, not the rejected
    requested search's sequence. The function reads JSON only, never source
    paths or mutable files. Recorded plan-id suffixes retain baseline lineage
    when a row embeds only the baseline plan rather than its full source row.
    """
    pool = row.get('standard_plan_pool') or {}
    is_pool = row.get('plan_producer') == 'lattice.standard_plan_pool'
    selected = (pool.get('donor') or {}).get('record') if is_pool else row
    selected = selected if isinstance(selected, dict) else {}
    result = _repair(selected)
    has_plan_evidence = bool(selected.get('plan') or selected.get('plan_producer') or result['repaired'])
    result.update(schema_version='stowlm.crane-sequence-provenance/1',
                  native_engine_output=(not result['repaired'] and not is_pool) if has_plan_evidence else None,
                  source='standard_plan_pool.donor.record' if is_pool else 'selected_plan',
                  selection_producer=row.get('plan_producer'),
                  frame_claim_scope='crane frames walked on this plan')
    gauges = (row.get('gate') or {}).get('gauges') or {}
    result['frames_checked'] = gauges.get('n_frames', row.get('frames_checked'))
    result['all_frames_passed'] = (gauges.get('passed') is True and gauges.get('all_frames') is True) if gauges else None
    base = base_record
    if base is None:
        embedded = row.get('base_plan')
        base = dict(plan=embedded) if isinstance(embedded, dict) else {}
    result['base_sequence'] = _repair(base)
    result['base_sequence']['scope'] = 'comparison baseline only; does not classify the selected plan'
    return result

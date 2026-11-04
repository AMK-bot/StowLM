"""Exact recorded facts for the frozen hero's bay-09 closure; no native work."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_json
from .acceptance import recorded_gate_pass
from .film_analysis import authenticated_snapshot, displacement, final_positions
from .sequence_provenance import frame_provenance
from .store import canonical_rule_set, entry_key, _atomic

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
HERO_SHA256 = 'cabff3d68d56e64ba9956761bcd0ca2abfa5077d41296db35b630efc11b04678'
RULES = [dict(schema_version='stowlm.reader/2', constraint=dict(type='close_bay', bay=9))]
ATTRIBUTION = ('Pointwise forced means the old slot is forbidden by the instruction. '
               'Other changed slots may reflect search or coupled constraints; they are not '
               'certified avoidable or individually caused by this rule.')


def _paths(path):
    absolute = Path(path).resolve()
    try:
        relative = str(absolute.relative_to(ROOT))
    except ValueError:
        relative = None
    return dict(relative_path=relative, absolute_path=str(absolute))


def _source(snapshot, key):
    matches = [item for item in snapshot['rows'] if item['key'] == key]
    if len(matches) != 1:
        raise ValueError('exact canonical entry is missing or duplicated in the authenticated snapshot')
    source = matches[0]
    path = Path(source['path']).resolve()
    if not path.is_relative_to(HERE.resolve()):
        raise ValueError('film fact sources must remain inside lattice')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source['raw_sha256'] or json.loads(raw) != source['record']:
        raise ValueError('entry bytes changed or snapshot record differs from its authenticated source')
    row = source['record']
    if row.get('key', key) != key or entry_key(row['instance_sha256'], row['canonical_rules']) != key:
        raise ValueError('stored row key differs from its exact instance/rule identity')
    if row.get('status') != 'checked_plan' or row.get('instance_sha256') != sha256_json(row.get('instance')) \
            or not recorded_gate_pass(row['instance'], row.get('plan'), row['canonical_rules'], row.get('gate')):
        raise ValueError('stored entry has an unbound or incomplete recorded gate')
    return row, dict(key=key, **_paths(path), raw_sha256=source['raw_sha256'],
        catalogue_sha256=snapshot['catalogue_sha256'], plan_sha256=sha256_json(row['plan']),
        instance_sha256=row['instance_sha256'])


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name+' must be a finite recorded number')
    return value


def _lifts(row):
    gate = row['gate']
    value = _number(gate.get('lifts'), 'gate.lifts')
    counted = _number(gate['checker'].get('count', {}).get('weighted_nonproductive'), 'checker.count.weighted_nonproductive')
    if counted != value or row.get('lifts', value) != value:
        raise ValueError('stored lift count differs from its checker count')
    return value


def _frames(row):
    gauges = row['gate']['gauges']
    physics = gauges.get('physics') or {}
    frames = physics.get('frames')
    expected = len(row['plan']['moves'])+1
    if not isinstance(frames, list) or len(frames) != expected or physics.get('n_lifts') != expected-1:
        raise ValueError('recorded native timeline is absent or incomplete')
    for step, frame in enumerate(frames):
        if not isinstance(frame, dict) or type(frame.get('step')) is not int or frame['step'] != step \
                or frame.get('error') is not None or frame.get('stability_passed') is not True \
                or frame.get('strength_passed') is not True:
            raise ValueError('recorded native frames are malformed, unordered or not all passed')
        _number(frame.get('heel_deg'), 'frame.heel_deg')
    peak = max(frames, key=lambda frame: abs(frame['heel_deg']))
    return dict(n_frames=len(frames), expected_frames=expected, all_frames_passed=True,
        physics_epoch=physics.get('physics_epoch'), native_frame_source='gate.gauges.physics.frames',
        peak_abs_heel_deg=abs(peak['heel_deg']), peak_signed_heel_deg=peak['heel_deg'],
        peak_frame=peak['step'], peak_criteria=peak.get('criteria'), peak_covers_off=peak.get('covers_off'),
        peak_source_json_pointer=f"/gate/gauges/physics/frames/{peak['step']}/heel_deg",
        criteria_note=physics.get('note'), source_scope='Recorded native crane-frame walk on this exact plan; no fresh native verification.')


def build_close_bay_facts(snapshot, hero_sha=HERO_SHA256, *, frozen_base_record=None,
                          frozen_base_source=None):
    """Consume authenticated_snapshot's rows and extract only replay/count facts."""
    from stowage.model import Instance
    if not snapshot.get('catalogue_sha256'):
        raise ValueError('facts require an authenticated catalogue snapshot')
    selected, selected_source = _source(snapshot, entry_key(hero_sha, RULES))
    base, base_source = _source(snapshot, entry_key(hero_sha, []))
    if selected['canonical_rules'] != canonical_rule_set(RULES) or base['canonical_rules'] != [] \
            or selected['instance'] != base['instance']:
        raise ValueError('selected and baseline bodies/rules differ from the frozen hero identity')
    if frozen_base_record is None:
        path = HERE/'fixtures/hero-base.json'
        raw = path.read_bytes()
        frozen_base_record = json.loads(raw)
        frozen_base_source = dict(**_paths(path), raw_sha256=hashlib.sha256(raw).hexdigest())
    if frozen_base_record.get('instance_sha256') != hero_sha or \
            sha256_json(frozen_base_record.get('plan')) != base_source['plan_sha256']:
        raise ValueError('authenticated base is not the frozen baseline plan')
    instance = Instance.model_validate(selected['instance'])
    before, after = final_positions(instance, base['plan']), final_positions(instance, selected['plan'])
    movement = displacement(instance, before, after, RULES)
    forbidden = movement['rules'][0]['forbidden_baseline_ids']
    forced = movement['forced_ids']
    if set(forbidden) != set(forced) or not set(forced).issubset(set(instance.load_list)):
        raise ValueError('close-bay causal attribution cannot certify every forbidden old slot as a required load displacement')
    changed = [dict(container_id=bid, baseline_slot=before.get(bid), selected_slot=after.get(bid),
        pointwise_forced=bid in set(forced)) for bid in movement['moved_ids']]
    forced_slots = [dict(container_id=bid, baseline_slot=before[bid], selected_slot=after.get(bid),
        reason='The confirmed close-bay rule forbids loading into this baseline footprint.') for bid in forced]
    base_lifts, selected_lifts = _lifts(base), _lifts(selected)
    delta = selected_lifts-base_lifts
    if selected.get('base_lifts', base_lifts) != base_lifts or selected.get('lift_delta', delta) != delta:
        raise ValueError('stored lift delta is inconsistent with the frozen baseline')
    sequence = frame_provenance(selected, base_record=base)
    raw_producer, raw_label = selected.get('plan_producer'), selected.get('producer_label')
    original = selected.get('original_binding_plan')
    original_matches = sha256_json(original) == selected_source['plan_sha256'] if original is not None else None
    if sequence.get('native_engine_output') is True and original_matches is False:
        raise ValueError('selected plan differs from its recorded native original binding plan')
    derived_label = raw_label or (f"Frontier Engine {selected['engine_version']}; {sequence['label']}"
                                   if sequence.get('native_engine_output') is True else
                                   f"{raw_producer or 'Recorded producer unspecified'}; {sequence['label']}")
    return dict(schema_version='stowlm.film-close-bay-facts/1',
        extracted_utc=datetime.now(timezone.utc).isoformat(), instance_sha256=hero_sha,
        canonical_key=selected_source['key'], canonical_rules=deepcopy(RULES),
        computed_utc=selected['computed_utc'], instruction_id=selected['id'],
        boxes=dict(count=len(instance.load_list), source='instance.load_list',
            base_final_positions=len(before), selected_final_positions=len(after)),
        pointwise_forced=dict(count=len(forced), container_ids=forced, forbidden_old_slots=forced_slots,
            source='Actual reader footprint predicate on frozen baseline final slots; load IDs deduplicated.'),
        changed_final_positions=dict(count=len(changed), container_ids=movement['moved_ids'], positions=changed,
            additional_count=movement['additional_moved_count'], additional_ids=movement['additional_moved_ids'],
            source='Public move replay of the authenticated frozen baseline and selected plan.'),
        extra_lifts=dict(baseline=base_lifts, selected=selected_lifts, delta=delta,
            unit='weighted_nonproductive_lifts', measurement='recorded full-checker counts',
            stored_measurement=selected.get('measurement'), includes_projected_later_port_lifts=True,
            baseline_source='baseline.gate.checker.count.weighted_nonproductive',
            selected_source='selected.gate.checker.count.weighted_nonproductive',
            delta_source='selected minus baseline; checked against stored lift_delta'),
        native_frames=_frames(selected), sequence_provenance=sequence,
        producer=dict(recorded_plan_producer=raw_producer, recorded_producer_label=raw_label,
            derived_display_label=derived_label,
            label_source='Stored producer label when present; otherwise recorded engine_version and sequence_provenance.',
            original_binding_plan_sha256=sha256_json(original) if original is not None else None,
            original_binding_plan_matches_selected=original_matches,
            engine_version=selected.get('engine_version'), engine_manifest_sha256=selected.get('engine_manifest_sha256'),
            binding_evidence_status=(selected.get('binding_evidence') or {}).get('status'),
            recovered_incumbent=(selected.get('binding_evidence') or {}).get('recovered_incumbent')),
        sources=dict(selected=selected_source, baseline=base_source,
            frozen_baseline_reference=frozen_base_source or dict(kind='in_memory_reference', plan_sha256=base_source['plan_sha256']),
            extractor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),
        attribution=ATTRIBUTION,
        limitations=["It replans from scratch; keeping changes small isn't built yet.",
            'Other changes are not certified avoidable or wholly caused by the close-bay instruction.',
            'Authenticated recorded facts only; no planner, checker or native crane-frame calculation ran during extraction.'],
        native_compute_performed=False, native_gates_repeated=False)


def write_close_bay_facts(destination=HERE/'hero-facts-close-bay-9.json', *, store_dir=HERE/'store',
                           hero_sha=HERO_SHA256, expected_catalogue_sha256=None):
    destination = Path(destination).resolve()
    if not destination.is_relative_to(HERE.resolve()):
        raise ValueError('film fact output must remain inside lattice')
    snapshot = authenticated_snapshot(store_dir, expected_sha256=expected_catalogue_sha256)
    result = build_close_bay_facts(snapshot, hero_sha)
    _atomic(destination, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store-dir', type=Path, default=HERE/'store')
    parser.add_argument('--output', type=Path, default=HERE/'hero-facts-close-bay-9.json')
    parser.add_argument('--hero-sha256', default=HERO_SHA256)
    parser.add_argument('--expected-catalogue-sha256')
    args = parser.parse_args()
    facts = write_close_bay_facts(args.output, store_dir=args.store_dir, hero_sha=args.hero_sha256,
                                expected_catalogue_sha256=args.expected_catalogue_sha256)
    print(canonical_json(dict(path=str(args.output), boxes=facts['boxes']['count'],
        forced=facts['pointwise_forced']['count'], moved=facts['changed_final_positions']['count'],
        extra_lifts=facts['extra_lifts'], native_frames=facts['native_frames'])))


if __name__ == '__main__':
    main()

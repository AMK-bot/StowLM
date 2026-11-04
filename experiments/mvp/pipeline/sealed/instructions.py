"""Pre-registered instruction draw. No plan, checker or result is consulted."""
from __future__ import annotations

RULE_TYPES = ('close_bay', 'max_tiers', 'dg_keep_away', 'one_port_per_hatch')
CAP_REDUCTION = 3  # 2026-11-02 development-only revision; AMENDMENT.md.


def public_facts(instance) -> dict:
    """Only ship geometry and cargo attributes; never yard state or any result."""
    import json
    raw = instance if isinstance(instance, dict) else instance.model_dump(mode='json')
    vessel = raw['vessel']
    geometry = {key: vessel.get(key, []) for key in
                ('id', 'cells', 'stacks', 'bay_pairs', 'hatch_covers', 'exclusion_regions')}
    cargo = [{key: box.get(key) for key in ('length_ft', 'gross_mass_kg', 'pod', 'dg')}
             for box in raw['containers']]
    cargo.sort(key=lambda row: json.dumps(row, sort_keys=True, separators=(',', ':')))
    return dict(ship=geometry, cargo=cargo)


def select_rule(instance, cohort_index: int) -> dict:
    """Six assignments/type in a 24-map cohort; fields use public map facts.

    The index is rank by canonical public_facts, with map_id only breaking ties;
    it is never execution, feasibility or success order.
    This may draw an impossible or vacuous rule: neither permits a redraw.
    """
    if type(cohort_index) is not int or cohort_index < 0:
        raise ValueError('cohort_index must be a nonnegative manifest ordinal')
    from experiments.mvp.reader.schema import context_from_instance
    context = context_from_instance(instance)
    raw = instance if isinstance(instance, dict) else instance.model_dump(mode='json')
    bays = sorted(bay for bay in context['bays'] if bay % 2)
    if not bays:
        raise ValueError('instruction draw needs a public physical bay')
    bay = bays[(cohort_index // 4) % len(bays)]
    kind = RULE_TYPES[cohort_index % 4]
    if kind == 'close_bay':
        rule = dict(type=kind, bay=bay)
    elif kind == 'max_tiers':
        caps = context['max_tiers_by_bay'][str(bay)]
        zone = 'deck' if caps['deck'] else 'hold'
        rule = dict(type=kind, bay=bay, zone=zone, max_tiers=max(0, caps[zone]-CAP_REDUCTION))
    elif kind == 'dg_keep_away':
        from experiments.mvp.reader.schema import IMDG_CLASSES
        from llm.instructions.apply import _class_matches
        # One vote per load-list box/class, including nested and subsidiary
        # risks. Background cargo never chooses the instruction's target.
        load_ids = set(raw.get('load_list', []))
        counts = {candidate: sum(_class_matches(candidate, _dg_classes(box))
                                for box in raw['containers'] if box['id'] in load_ids)
                  for candidate in IMDG_CLASSES}
        classes = sorted((candidate for candidate, count in counts.items() if count),
                         key=lambda candidate: (-counts[candidate], candidate))
        rule = dict(type=kind, imdg_class=classes[0] if classes else '3',
                    min_bays=1, away_from=dict(kind='bay', bay=bay))
    else:
        rule = dict(type=kind)
    return dict(schema_version='stowlm.reader/2', constraint=rule)


def sentence_for(rule: dict) -> str:
    rule = rule['constraint']
    kind = rule['type']
    if kind == 'close_bay':
        return f"Do not load containers in bay {rule['bay']}."
    if kind == 'max_tiers':
        return f"In bay {rule['bay']}, use at most {rule['max_tiers']} tiers in the {rule['zone']}."
    if kind == 'dg_keep_away':
        return (f"Keep IMDG class {rule['imdg_class']} at least {rule['min_bays']} "
                f"20-foot bay away from bay {rule['away_from']['bay']}.")
    if kind == 'one_port_per_hatch':
        return 'Each physical hatch cover must serve at most one discharge port across hold and deck.'
    raise ValueError('unknown preregistered rule type')


def _dg_classes(box):
    classes = set()
    pending = [box['dg']] if box.get('dg') else []
    while pending:
        entry = pending.pop()
        classes.add(entry['imdg_class'])
        classes.update(entry.get('subsidiary_risks', []))
        pending.extend(entry.get('additional', []))
    return classes


def instruction_targets(instance, envelope: dict) -> dict:
    """Count load-list boxes addressed by a DG rule, including mixed-load risks."""
    rule = envelope['constraint']
    if rule['type'] != 'dg_keep_away':
        return dict(instruction_target_count=None, no_matching_cargo=None)
    from llm.instructions.apply import _class_matches
    raw = instance if isinstance(instance, dict) else instance.model_dump(mode='json')
    load_ids = set(raw['load_list'])
    count = 0
    for box in raw['containers']:
        if box['id'] not in load_ids:
            continue
        count += bool(_class_matches(rule['imdg_class'], _dg_classes(box)))
    return dict(instruction_target_count=count, no_matching_cargo=count == 0)


class FixtureParser:
    """Explicit offline surrogate; never a reader-accuracy measurement."""
    fixture_surrogate = True

    def __init__(self, expected_rule=None):
        self.expected_rule = expected_rule

    def parse(self, sentence, context):
        from experiments.mvp.reader.schema import validate_answer
        import json
        if sentence == 'One port per hatch.':
            envelope = dict(schema_version='stowlm.reader/2', constraint=dict(type='one_port_per_hatch'))
        elif self.expected_rule is not None and sentence == sentence_for(self.expected_rule):
            envelope = self.expected_rule
        else:
            raise ValueError('fixture parser accepts only the pre-registered fixture sentences')
        return validate_answer(json.dumps(envelope), context), dict(
            parser='SealedFixtureSurrogate', adapter_sha256=None, fixture_surrogate=True)

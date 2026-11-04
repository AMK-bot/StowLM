"""Development-only rule selection. Sentences are captions, never lookup keys."""
from __future__ import annotations

import json

from experiments.mvp.pipeline.contract.schema import canonical_json
from experiments.mvp.reader.schema import context_from_instance, validate_answer


def sentence(body):
    kind = body['type']
    if kind == 'close_bay':
        return f"Do not load any containers in bay {body['bay']:02d}."
    if kind == 'max_tiers':
        scope = 'every bay' if body['bay'] is None else f"bay {body['bay']:02d}"
        return f"In {scope}, use at most {body['max_tiers']} occupied tiers per {body['zone']} stack."
    if kind == 'one_port_per_hatch':
        return 'Keep one discharge port per physical hatch cover, across hold and deck.'
    anchor = 'the accommodation' if body['away_from']['kind'] == 'accommodation' else f"bay {body['away_from']['bay']:02d}"
    return f"Keep all IMDG class {body['imdg_class']} containers at least {body['min_bays']} 20-foot bays away from {anchor}."


def make_spec(identifier, bodies, *, purpose='lattice', base_violations=None):
    return dict(id=identifier, purpose=purpose,
                sentences=[sentence(b) for b in bodies],
                rules=[dict(schema_version='stowlm.reader/2', constraint=b) for b in bodies],
                base_already_obeys=None if base_violations is None else not bool(base_violations),
                base_violations=base_violations,
                confirmation_source='batch',
                selection='synthetic development demonstration, not a held-out benchmark')


def select_instructions(instance, base_plan):
    """Select 25 instructions, preferring a small actual base-plan violation.

    Every body goes through the real strict reader schema. This is explicit
    batch confirmation of structured development requests, not a word parser.
    The three reserved requests are disjoint canonical keys for live filming.
    """
    from experiments.mvp.reader.pipeline import intrinsic_violations
    from llm.instructions.apply import _dg_classes
    context = context_from_instance(instance)
    bays = [b for b in context['bays'] if b % 2]
    active = set(instance.load_list)
    classes = sorted({c for b in instance.containers if b.id in active for c in _dg_classes(b)})
    pools = {'close_bay': [dict(type='close_bay', bay=b) for b in bays],
             'max_tiers': [], 'dg_keep_away': [],
             'one_port_per_hatch': [dict(type='one_port_per_hatch')]}
    for zone in ('hold', 'deck'):
        for bay in [None] + bays:
            maximum = context['max_tiers'][zone] if bay is None else context['max_tiers_by_bay'][str(bay)][zone]
            for cap in sorted({max(0, maximum - 1), max(0, maximum - 2), 0}, reverse=True):
                pools['max_tiers'].append(dict(type='max_tiers', bay=bay, zone=zone, max_tiers=cap))
    for cls in classes:
        for bay in bays:
            for radius in (1, 2):
                pools['dg_keep_away'].append(dict(type='dg_keep_away', imdg_class=cls,
                    min_bays=radius, away_from=dict(kind='bay', bay=bay)))
        if context['has_accommodation']:
            for radius in (1, 2, 3):
                pools['dg_keep_away'].append(dict(type='dg_keep_away', imdg_class=cls,
                    min_bays=radius, away_from=dict(kind='accommodation')))
    ranked = {}
    for kind, bodies in pools.items():
        rows = []
        for body in bodies:
            answer = validate_answer(json.dumps(dict(schema_version='stowlm.reader/2', constraint=body)), context)
            violations = list(intrinsic_violations(instance, answer, base_plan))
            # Tiny restrictions first; base-obeyed rows remain clearly marked.
            zero_cap = kind == 'max_tiers' and body['max_tiers'] == 0
            bay_anchor = kind == 'dg_keep_away' and body['away_from']['kind'] != 'accommodation'
            rows.append(((zero_cap, not bool(violations), len(violations), bay_anchor, canonical_json(body)), body, violations))
        ranked[kind] = sorted(rows)
    selected = {}
    for kind, count in [('max_tiers', 10), ('close_bay', 6), ('dg_keep_away', 5), ('one_port_per_hatch', 1)]:
        selected[kind] = ranked[kind][:count]
    chosen = []
    while any(selected.values()):
        for kind in ('max_tiers', 'dg_keep_away', 'close_bay', 'one_port_per_hatch'):
            if selected[kind]:
                _, body, errors = selected[kind].pop(0)
                chosen.append(make_spec(f'{kind}-{len(chosen)+1:02d}', [body], base_violations=errors))
    # A useful independently shipped shot-6 fallback gets first priority.
    if chosen:
        chosen[0]['backup_shot_6'] = True
    for left_kind, right_kind in [('max_tiers', 'close_bay'), ('max_tiers', 'dg_keep_away')]:
        if ranked[left_kind] and ranked[right_kind]:
            left, right = ranked[left_kind][0], ranked[right_kind][0]
            chosen.append(make_spec(f'pair-{len(chosen)+1:02d}', [left[1], right[1]],
                                    base_violations=left[2]+right[2]))
    impossible = [dict(type='max_tiers', bay=None, zone=zone, max_tiers=0) for zone in ('hold', 'deck')]
    errors = [error for body in impossible for error in intrinsic_violations(instance,
              validate_answer(json.dumps(dict(schema_version='stowlm.reader/2', constraint=body)), context), base_plan)]
    chosen.append(make_spec('pair-zero-capacity', impossible, base_violations=errors))
    keys = {canonical_json(sorted(r['rules'], key=canonical_json)) for r in chosen}
    off_lattice = []
    for _, body, errors in ranked['max_tiers'][10:] + ranked['dg_keep_away'][5:] + ranked['close_bay'][6:]:
        if body['type'] == 'max_tiers' and body['max_tiers'] == 0:
            continue
        row = make_spec(f'live-{len(off_lattice)+1}', [body], purpose='off_lattice', base_violations=errors)
        key = canonical_json(sorted(row['rules'], key=canonical_json))
        if key not in keys:
            keys.add(key)
            off_lattice.append(row)
        if len(off_lattice) == 3:
            break
    return dict(instructions=chosen, off_lattice=off_lattice,
                baseline=make_spec('base', [], base_violations=[]))

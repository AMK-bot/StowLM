"""D70 coverage counts measured admitted instances, never requested DG cohorts.

Binding measurements must accompany the reference solver's best passing plan
and use IMDG 7.2.4's applicable minimum, including strengthening provisions.
This reporting layer does not substitute a distance heuristic for checker
geometry. Missing measurements stay unknown, even when classes co-occur.
"""
from collections.abc import Mapping
import json


CLAIM_LABEL = "DG safety under stress, zero-shot transfer"
BINDING_DEFINITION = (
    "D70: in the reference solver's best passing plan, at least one pair of boxes "
    "of the two classes sits at exactly the applicable IMDG 7.2.4 minimum "
    "separation, including strengthening codes, with no slack"
)
CLASSES = ('2.1', '2.2', '2.3', '3', '4.1', '4.3', '6.1', '8', '9', '1.4S')
CLASS_MIN_INSTANCES = 10
PAIR_MIN_INSTANCES = 5


def stress_release_policy(version, entries_sha256=None):
    """Use the inventory owner's trusted policy and supersession gate for reports."""
    from eval.splits_inventory import RELEASE_PINS, _reject_superseded

    _reject_superseded(version)
    pin = RELEASE_PINS.get(version)
    if pin is None or not version.startswith('dg-stress'):
        raise ValueError('unsupported DG-stress split release')
    if entries_sha256 is not None and entries_sha256 != pin.entries_sha256:
        raise ValueError('DG-stress split inventory digest differs from the trusted release pin')
    return json.loads(pin.policy_json)


def pair_cohort(row, policy):
    """Read the allocated cohort, never infer it from admitted or requested classes."""
    spec = row.get('spec') or {}
    family = (row.get('instance') or {}).get('family') or {}
    if 'dg_stress_pair' in row:
        value = row['dg_stress_pair']
    elif 'dg_stress_pair' in spec:
        value = spec['dg_stress_pair']
    elif 'dg_stress_pair' in family:
        value = family['dg_stress_pair']
    else:
        return 'unknown'
    if value is None or value == 'mixed':
        return 'mixed'
    if value not in policy.get('small_pair_cohorts', {}):
        raise ValueError('DG-stress cohort is not allocated by the selected release policy')
    return value


def admitted_class_uns(instance):
    """Measure accepted export and onboard DG; exclude unrelated yard blockers."""
    admitted_ids = set(instance.load_list) | {p.container_id for p in instance.onboard.placements}
    measured = {}
    def entries(dg):
        yield dg
        for child in dg.additional:
            yield from entries(child)
    for box in instance.containers:
        if box.id in admitted_ids and box.dg is not None:
            for dg in entries(box.dg):
                measured.setdefault(dg.imdg_class, set()).add(dg.un_number)
    return {label: sorted(uns) for label, uns in sorted(measured.items())}


def _pair_key(pair):
    if (not isinstance(pair, (list, tuple)) or len(pair) != 2
            or any(not isinstance(label, str) or not label for label in pair)
            or pair[0] == pair[1]):
        raise ValueError('D70 binding pairs must name two distinct classes')
    return '/'.join(sorted(pair))


def coverage_summary(rows):
    """Apply D70's >=10 admitted-instance and >=5 binding-instance claim gates.

    ``dg_coverage.admitted_class_uns`` is measured from accepted cargo. A
    reference record may supply ``dg_binding`` with this module's exact
    ``definition``, ``plan_selection='best_passing'`` and measured class
    ``pairs``. Only a passing, input-bound reference can supply binding evidence;
    ordinary method plans, cohort tags and co-presence never supply it.
    """
    from eval.harness_metrics import _checked_passed

    classes = {label: {'instances': set(), 'uns': set()} for label in CLASSES}
    present, bindings, admitted_known, binding_known = {}, {}, set(), set()
    versions = {row['split_version'] for row in rows if row.get('split_version')}
    if len(versions) > 1:
        raise ValueError('DG coverage cannot pool split releases')
    version = next(iter(versions), '')
    policy = stress_release_policy(version) if version.startswith('dg-stress') else {}
    pair_keys = {_pair_key(definition['classes'])
                 for definition in policy.get('pair_definitions', {}).values()}
    for index, row in enumerate(rows):
        key = row.get('spec_key') or row.get('instance_id') or str(index)
        evidence = row.get('dg_coverage') or {}
        admitted = evidence.get('admitted_class_uns')
        if row.get('allocation_only') or not isinstance(admitted, Mapping):
            continue
        admitted_known.add(key)
        present[key] = set()
        for label, uns in admitted.items():
            if (not isinstance(label, str) or not isinstance(uns, list) or not uns
                    or any(not isinstance(un, str) or len(un) != 4 or not un.isdigit() for un in uns)):
                raise ValueError('D70 admitted class evidence requires class labels and four-digit UNs')
            values = classes.setdefault(label, {'instances': set(), 'uns': set()})
            values['instances'].add(key)
            values['uns'].update(uns)
            present[key].add(label)
        reference = row.get('reference_solver') or {}
        if not isinstance(reference, Mapping):
            continue
        reference = reference.get('witness', reference)
        measurement = reference.get('dg_binding') or {}
        if (not _checked_passed(reference) or reference.get('report_binding_valid') is not True
                or (reference.get('result') or {}).get('planner') not in ('solver.exact', 'solver.scalable')
                or measurement.get('definition') != BINDING_DEFINITION
                or measurement.get('plan_selection') != 'best_passing'
                or not isinstance(measurement.get('pairs'), list)):
            continue
        measured_pairs = {_pair_key(pair) for pair in measurement['pairs']}
        if any(not set(pair.split('/')) <= present[key] for pair in measured_pairs):
            raise ValueError('D70 binding witness names classes absent from admitted cargo')
        binding_known.add(key)
        for pair in measured_pairs:
            pair_keys.add(pair)
            bindings.setdefault(pair, set()).add(key)
    return {
        'rule': 'D70', 'class_min_instances': CLASS_MIN_INSTANCES, 'pair_min_instances': PAIR_MIN_INSTANCES,
        'binding_definition': BINDING_DEFINITION,
        'n_admitted_unmeasured': len(rows) - len(admitted_known),
        'n_binding_unmeasured': len(rows) - len(binding_known),
        'classes': {label: {'n_instances': len(values['instances']), 'uns': sorted(values['uns']),
                            'tested': len(values['instances']) >= CLASS_MIN_INSTANCES}
                    for label, values in sorted(classes.items())},
        'pairs': {pair: {'n_copresent': sum(set(pair.split('/')) <= labels for labels in present.values()),
                         'n_binding': len(bindings.get(pair, set())),
                         'tested': len(bindings.get(pair, set())) >= PAIR_MIN_INSTANCES}
                  for pair in sorted(pair_keys)},
    }


def render_coverage(coverage):
    """Print counts and UNs beside an explicitly qualified D70 coverage claim."""
    classes = '; '.join(f"{label}={values['n_instances']} (UNs {','.join(values['uns']) or 'none'}; "
                        f"{'tested' if values['tested'] else 'not tested'})"
                        for label, values in coverage['classes'].items())
    pairs = '; '.join(f"{pair}={values['n_binding']} binding/{values['n_copresent']} co-present "
                      f"({'tested' if values['tested'] else 'not tested'})"
                      for pair, values in coverage['pairs'].items())
    return (f"D70 (class >=10 instances; pair >=5 binding instances): {classes}; {pairs}; "
            f"unmeasured admitted/binding {coverage['n_admitted_unmeasured']}/{coverage['n_binding_unmeasured']}")

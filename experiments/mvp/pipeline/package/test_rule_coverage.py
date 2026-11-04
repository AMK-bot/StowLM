"""Enabled checker coverage is sourced, deterministic and fails closed on drift."""
from hashlib import sha256
from pathlib import Path

import pytest

from stowage.rules.catalogue import DEFAULT_CATALOGUE_PATH, catalogue_snapshot
from experiments.mvp.pipeline.package.rule_coverage import _audit_gates, rule_coverage


GATED = {
    'port.dg.refused_class', 'port.dg.direct_delivery',
    'port.e3.quay_crossing', 'port.e3.yard_crossing',
    'port.stability.cargo_operations', 'port.ballast.transfer_rate',
}
SOFT = {'iscode.stability.margin', 'iacs.strength.margin'}


def test_actual_checker_coverage_and_catalogue_hash():
    snapshot = catalogue_snapshot()
    assert snapshot.catalogue.activation.rule_freeze_v1 is False
    result = rule_coverage()
    assert result['active_rule_count'] == 54
    assert result['inactive_rule_count'] == 8
    assert set(result['inactive_rules']) == GATED | SOFT
    assert set(result['active_rules']) | set(result['inactive_rules']) == set(snapshot.rules)
    assert not set(result['active_rules']) & set(result['inactive_rules'])
    assert result['catalogue_sha256'] == sha256(Path(DEFAULT_CATALOGUE_PATH).read_bytes()).hexdigest()
    assert result['rule_freeze_v1'] is False
    assert set(result['rule_gate_sources']) == GATED
    assert all('rule_freeze_v1 is false' in result['inactive_rule_reasons'][rule] for rule in GATED)
    assert all('Not checker-enforced' in result['inactive_rule_reasons'][rule] for rule in SOFT)


def test_legacy_companions_stay_active_and_packet_applicability_is_not_claimed():
    result = rule_coverage()
    assert {'port.e3.quay_height', 'port.e3.yard_height', 'port.call.draft', 'port.call.length',
            'stability.open_hatch_immersion', 'expert.e6.heavy_under_deck',
            'expert.e1.heavy_bottom', 'expert.e2.weight_order', 'expert.e9.over_height',
            'expert.e9.over_width_length'} <= set(result['active_rules'])
    assert 'not rules exercised by this packet' in result['rule_coverage_scope']
    assert any('legacy guard' in note for note in result['rule_coverage_notes'])


def test_source_identities_cover_actual_checker_and_move_code():
    result = rule_coverage()
    root = Path(DEFAULT_CATALOGUE_PATH).parent.parent
    expected = {'rules/catalogue.py'} | {str(path.relative_to(root))
        for folder in ('checker', 'moves') for path in (root / folder).rglob('*.py')}
    assert set(result['rule_source_sha256']) == expected
    assert all(digest == sha256((root / name).read_bytes()).hexdigest()
               for name, digest in result['rule_source_sha256'].items())
    assert result == rule_coverage()


@pytest.mark.parametrize(('source', 'before', 'after'), [
    ('checker/full.py', 'if rule_freeze_v1_enabled():', 'if True:'),
    ('moves/_port_rules.py', 'if not rule_freeze_v1_enabled():', 'if False:'),
    ('moves/_physical.py', 'if not self.crossing_enabled:', 'if False:'),
    ('checker/in_port.py', 'if d74:', 'if True:'),
])
def test_missing_or_bypassed_code_gate_fails_closed(source, before, after):
    root = Path(DEFAULT_CATALOGUE_PATH).parent.parent
    names = ('checker/full.py', 'moves/_port_rules.py', 'moves/_physical.py',
             'checker/in_port.py', 'checker/in_port_programme.py')
    sources = {name: (root / name).read_text() for name in names}
    assert before in sources[source]
    sources[source] = sources[source].replace(before, after)
    with pytest.raises(ValueError, match='Rule coverage audit:'):
        _audit_gates(sources, set(catalogue_snapshot().rules))


def test_unknown_catalogue_gate_cannot_be_counted_active(monkeypatch):
    from types import SimpleNamespace
    from experiments.mvp.pipeline.package import rule_coverage as module

    snapshot = catalogue_snapshot()
    rule = snapshot.catalogue['expert.physical.slot_occupancy']
    reference = snapshot.catalogue['port.dg.refused_class'].parameters['activation_path']
    altered = rule.model_copy(update={'parameters': {**rule.parameters, 'activation_path': reference}})
    catalogue = snapshot.catalogue.model_copy(update={'rules': [
        altered if row.id == rule.id else row for row in snapshot.catalogue]})
    monkeypatch.setattr(module, 'catalogue_snapshot', lambda: SimpleNamespace(
        catalogue=catalogue, rules={row.id: row for row in catalogue}, content=snapshot.content))
    with pytest.raises(ValueError, match='catalogue/runtime activation gates differ'):
        module.rule_coverage()

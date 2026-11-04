"""Read the fixed tier policy and classify an unchanged full-checker report.

Only hard violations determine the ladder: the ordinary checker can pass with
soft/tolerated diagnostics, and that existing L0 meaning is retained. Breaches
are absolute measured/limit gaps, summed over hard occurrences in their original
units. This is deliberately an unnormalised mixed-unit sum: the policy supplies
no exchange rates between metres, radians, kilograms and securing ratios.
A missing numeric pair remains unknown, except all_loaded whose box-count
breach is derived exactly from the checker's shut-out/container IDs. Cargo
retention (count, TEU, mass) precedes rule count, breach and lifts (D242).
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import math
from pathlib import Path
import tomllib

HERE = Path(__file__).resolve().parent
from frozen_env import ROOT
TABLE_PATH = HERE / 'tiers.toml'
TIERS = tuple(f'T{i}' for i in range(6))


class TierTable:
    def __init__(self, data):
        self.data = data
        self.by_rule = {}
        for tier in TIERS:
            for rule_id in data['tiers'][tier]['rules']:
                if rule_id in self.by_rule:
                    raise ValueError(f'Rule mapped more than once: {rule_id}')
                self.by_rule[rule_id] = tier
        self.levels = {int(k[1:]): tuple(v) for k, v in data['levels'].items()}
        expected = {k: tuple(f'T{i}' for i in range(5, 5-k, -1)) for k in range(6)}
        if self.levels != expected:
            raise ValueError('Tier table does not implement the fixed L0..L5 ladder')

    def tier_for(self, rule_id):
        return self.by_rule.get(rule_id, 'T0')

    def peeled(self, level):
        return self.levels[level]


def load_table(path=None):
    with Path(path or TABLE_PATH).open('rb') as stream:
        return TierTable(tomllib.load(stream))


def tier_for(rule_id, table=None):
    return (table or load_table()).tier_for(rule_id)


def _dict(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else deepcopy(value)


def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def violation_breach(violation, *, shut_out=None):
    """Magnitude outside a violated upper or lower bound, or None if unknown."""
    measured, limit = violation.get('measured'), violation.get('limit')
    if _numeric(measured) and _numeric(limit):
        return abs(float(measured) - float(limit))
    if violation.get('rule_id') == 'objective.all_loaded':
        boxes = set(violation.get('containers') or ()) | set(shut_out or ())
        if boxes:
            return len(boxes)
    return None


def analyze_report(report, table=None, *, instance=None):
    """Classify without modifying a report. None level is never returnable.

    The full checker flattens all parts into ``violations`` already; reading
    parts again would count the same breach repeatedly. Failed/missing part
    execution, no replay count, inconsistent passed flag, and unknown hard rule
    IDs fail closed. Per-occurrence raw evidence is kept beside its gap.
    """
    table = table or load_table()
    report = _dict(report)
    if not isinstance(report, dict):
        raise TypeError('Expected CheckReport or its JSON object')
    violations = report.get('violations', [])
    hard = defaultdict(list)
    diagnostics = []
    for violation in violations:
        violation = _dict(violation)
        if violation.get('severity') == 'hard':
            hard[violation.get('rule_id', 'checker.internal_error')].append(violation)
        else:
            diagnostics.append(violation)
    reasons = []
    for name, part in report.get('parts', {}).items():
        if not part.get('ran', True) or part.get('error'):
            reasons.append(f'Checker part did not complete: {name}')
    if report.get('count') is None:
        reasons.append('Checker supplied no replay count')
    if report.get('passed') is not (not hard):
        reasons.append('Checker verdict inconsistent with hard violations')
    if report.get('shut_out') and 'objective.all_loaded' not in hard:
        reasons.append('Shut-out boxes lack the ordinary all_loaded violation')
    ashore = sorted(set(report.get('shut_out') or ()) | {box
        for violation in hard.get('objective.all_loaded', ())
        for box in violation.get('containers', ())})
    ashore_teu = ashore_mass = 0.0 if not ashore else None
    if ashore and instance is not None:
        containers = instance.get('containers', []) if isinstance(instance, dict) else instance.containers
        boxes = {b.get('id') if isinstance(b, dict) else b.id: b for b in containers}
        if all(box in boxes for box in ashore):
            selected = [boxes[box] for box in ashore]
            lengths = [b.get('length_ft') if isinstance(b, dict) else b.length_ft for b in selected]
            masses = [b.get('gross_mass_kg') if isinstance(b, dict) else b.gross_mass_kg for b in selected]
            if all(_numeric(v) for v in lengths):
                ashore_teu = sum(lengths) / 20
            if all(_numeric(v) for v in masses):
                ashore_mass = sum(masses)
    broken_by_tier = {tier: [] for tier in TIERS}
    rules = []
    for rule_id in sorted(hard):
        tier = table.tier_for(rule_id)
        broken_by_tier[tier].append(rule_id)
        occurrences = []
        gaps = []
        for violation in hard[rule_id]:
            gap = violation_breach(violation, shut_out=ashore)
            derived = (rule_id == 'objective.all_loaded' and gap is not None and
                       not (_numeric(violation.get('measured')) and _numeric(violation.get('limit'))))
            source = ('derived:report.shut_out/violation.containers' if derived else
                      'measured/limit' if gap is not None else 'unknown')
            gaps.append(gap)
            occurrences.append(dict(violation, breach=gap, breach_source=source,
                                    breach_unit='boxes' if derived else violation.get('unit')))
        # all_loaded is one cargo shortfall even if repeated in a malformed
        # report. Its exact report-wide ID union is counted once.
        derived_all_loaded = (rule_id == 'objective.all_loaded' and
                              any(v['breach_source'].startswith('derived:') for v in occurrences))
        if derived_all_loaded:
            gaps = [len(ashore)]
        rules.append(dict(rule_id=rule_id, tier=tier,
                          breach=sum(gaps) if all(v is not None for v in gaps) else None,
                          breach_source='derived:report.shut_out/violation.containers' if derived_all_loaded else 'occurrences',
                          measured_breach_sum=sum(v for v in gaps if v is not None),
                          unmeasured_occurrences=sum(v is None for v in gaps),
                          occurrences=occurrences))
    if broken_by_tier['T0']:
        reasons.append('Unpeelable T0 hard violation')
    level = None if reasons else max((6-int(rule['tier'][1:]) for rule in rules), default=0)
    count = report.get('count') or {}
    lifts = count.get('weighted_nonproductive')
    total = sum(rule['breach'] for rule in rules) if all(rule['breach'] is not None for rule in rules) else None
    return dict(level=level, level_name=f'L{level}' if level is not None else None,
                eligible=level is not None, passed=report.get('passed') is True,
                tiers_peeled=list(table.peeled(level)) if level is not None else [],
                broken_rule_ids=sorted(hard), broken_rules=rules,
                broken_by_tier=broken_by_tier, total_breach=total,
                measured_breach_sum=sum(rule['measured_breach_sum'] for rule in rules),
                unmeasured_occurrences=sum(rule['unmeasured_occurrences'] for rule in rules),
                boxes_left_ashore=ashore, ashore_count=len(ashore),
                ashore_teu=ashore_teu, ashore_mass_kg=ashore_mass,
                ashore_evidence='report.shut_out/objective.all_loaded.containers',
                lifts=lifts if _numeric(lifts) else None,
                diagnostics=diagnostics, ineligible_reasons=reasons)


def rank_analysis(analysis):
    """D242 cargo-first policy inside a permitted level; unknowns rank last."""
    count = analysis.get('ashore_count', len(analysis.get('boxes_left_ashore', ())))
    teu = analysis.get('ashore_teu', 0.0 if not count else None)
    mass = analysis.get('ashore_mass_kg', 0.0 if not count else None)
    return (count, teu if teu is not None else math.inf,
            mass if mass is not None else math.inf,
            len(analysis['broken_rule_ids']),
            analysis['total_breach'] if analysis['total_breach'] is not None else math.inf,
            analysis['lifts'] if analysis['lifts'] is not None else math.inf)


def rank_report(report, table=None, *, instance=None):
    return rank_analysis(analyze_report(report, table, instance=instance))


def allowed(report, level, table=None):
    reached = analyze_report(report, table)['level']
    return reached is not None and reached <= level

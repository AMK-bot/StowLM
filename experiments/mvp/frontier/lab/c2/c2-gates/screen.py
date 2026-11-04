"""Fail-only public-checker gate. No result from this module is a pass.

The ladder helper omits strength only after complete learning diagnostics and
a known hard failure. Unknown/potentially successful plans use the full checker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from time import monotonic

from stowage.checker import (check, check_lashing, check_static_rules,
                             check_stability, check_visibility,
                             departure_condition)
from stowage.checker.perf import evaluation_cache
from stowage.frozen import active_stubs
from stowage.model import (VoyageSpec, arrival_remaining_fraction,
                          derive_voyage_liquid_state)
from stowage.moves import apply_plan
from stowage.rules.catalogue import rule_freeze_v1_enabled


_ROOT = Path(__file__).resolve().parents[6]
_LEARNING_SOURCES = {
    'src/stowage/checker/full.py': 'ff3e593771ef30c29e1e6cecd434090961ed099e2e51b66c37b268113eee7fa1',
    'src/stowage/checker/strength.py': 'f7d1dbdd49ec29f461800113cad05a9bb79ad1b1f3d6eeea3cef6880a21e1b81',
    'experiments/mvp/frontier/lab/c1/fx-scout/method.py': 'a51bb85ea09c79b713a4d06dae0c0af0dffb5dd00c8e12d83875af98e3f5eece',
}


def _learning_sources_supported():
    try:
        return all(hashlib.sha256((_ROOT/path).read_bytes()).hexdigest()==digest
                   for path,digest in _LEARNING_SOURCES.items())
    except OSError:
        return False


@dataclass
class Evidence:
    status: str = 'unknown'
    violations: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    complete_learning: bool = False
    used_full_check: bool = False

    @property
    def rules(self):
        return sorted({v.rule_id for v in self.violations})

    @property
    def bow_down(self):
        return any(('trim' in v.rule_id and v.measured is not None and v.measured < 0)
                   or ('draft' in v.rule_id and 'fwd' in v.message)
                   for v in self.violations)


def _samples(instance, ship):
    """D57 contexts exactly as full._voyage, with conservative validation.

    Copied declaration checks/context construction from checker/full.py; only
    public model functions derive liquids. Errors cause the caller's fallback.
    """
    raw = instance.voyage
    if raw is None:
        raise ValueError('missing voyage')
    leg = VoyageSpec.model_validate(raw.model_dump() if isinstance(raw, VoyageSpec) else raw)
    departure, arrival = leg.departure, leg.arrival
    calls = instance.rotation.calls
    if (departure.call_index != instance.call_index or departure.locode != instance.port.locode
            or not 0 <= departure.call_index < len(calls)
            or calls[departure.call_index].locode != departure.locode
            or arrival.call_index >= len(calls) or calls[arrival.call_index].locode != arrival.locode
            or abs(departure.water_density_kg_m3-instance.port.water_density_kg_m3) > 1e-9
            or arrival.id == f'{departure.id}:mid_voyage'):
        raise ValueError('inconsistent voyage')
    remaining = arrival_remaining_fraction(ship, leg, vessel=instance.vessel)
    for phase, identity, fraction in (('departure', departure, 1.),
                                     ('mid_voyage', departure, (1.+remaining)/2),
                                     ('arrival', arrival, remaining)):
        sample = derive_voyage_liquid_state(ship, leg, fraction, vessel=instance.vessel)
        state = ship.model_copy(deep=True, update={'tank_masses_kg': dict(sample.tank_masses_kg)})
        port = instance.port if phase == 'departure' else instance.port.model_copy(update={
            'locode': identity.locode, 'water_density_kg_m3': identity.water_density_kg_m3,
            'max_draught_m': None, 'port_rules': []})
        context = instance.model_copy(update={'call_index': identity.call_index,
                                             'port': port, 'onboard': state})
        yield phase, context, state


def _hard(violations):
    return any(v.severity == 'hard' for v in violations)


def _condition_violations(instance, phase, violations):
    """Match full._condition_report messages, including arbitrary condition IDs.

    The ladder's bow-down predicate searches 'fwd' in the complete message, so
    even a condition ID containing that substring must receive the same prefix.
    """
    raw = getattr(instance, 'voyage', None)
    if raw is None:  # isolated unit-test parts; real _samples rejects this
        return list(violations)
    leg = raw if isinstance(raw, VoyageSpec) else VoyageSpec.model_validate(raw)
    identity = leg.arrival if phase == 'arrival' else leg.departure
    identity_id = identity.id+(':mid_voyage' if phase == 'mid_voyage' else '')
    return [v.model_copy(update={'message': f'[{identity_id} {phase}] {v.message}',
                                'call_index': identity.call_index}) for v in violations]


def diagnose(instance, plan, *, complete_learning=False):
    """Return rejection evidence; exceptions never become speculative failure.

    Cheap-first: replay, departure visibility, static rules, lashing, stability.
    Complete mode establishes every ladder learning flag across D57 samples.
    """
    out = Evidence()
    if active_stubs():
        return out
    with evaluation_cache():
        started = monotonic()
        try:
            replay = apply_plan(instance, plan)
            out.timings['replay'] = monotonic()-started
            out.violations.extend(v.model_copy(update={'severity': 'hard'}) for v in replay.violations)
            if not replay.legal:
                out.status = 'fail'
                return out
            # A shut-out always fails full check. No invented rule evidence.
            if set(instance.load_list)-{p.container_id for p in replay.final_state.ship.placements}:
                out.status = 'fail'
                return out
            ship = departure_condition(instance, plan)
            samples = list(_samples(instance, ship))
            phase, context, state = samples[0]
            started = monotonic()
            report = check_visibility(context, state, call_index=context.call_index)
            out.timings[phase+'/visibility'] = monotonic()-started
            if not report.ran:
                return out
            out.violations.extend(_condition_violations(instance, phase, report.violations))
            if _hard(out.violations) and not complete_learning:
                out.status = 'fail'
                return out
            started = monotonic()
            report = check_static_rules(instance, ship, call_index=instance.call_index)
            out.timings['rules'] = monotonic()-started
            if not report.ran:
                return out
            out.violations.extend(report.violations)
            if _hard(out.violations) and not complete_learning:
                out.status = 'fail'
                return out
            for phase, context, state in samples:
                for name, function in (('visibility', check_visibility),
                                       ('lashing', check_lashing),
                                       ('stability', check_stability)):
                    if phase == 'departure' and name == 'visibility':
                        continue
                    if complete_learning:
                        # Positive learning flags cannot be undone by another
                        # sample. all-iscode is separately guarded before a
                        # partial verdict may replace a full verdict.
                        saturated = (
                            (name == 'visibility' and any('visibility' in r for r in out.rules))
                            or (name == 'lashing' and any('securing' in r for r in out.rules))
                            or (name == 'stability' and out.bow_down))
                        if saturated:
                            out.timings[phase+'/'+name+'/saturated'] = 0.
                            continue
                    started = monotonic()
                    report = function(context, state, call_index=context.call_index)
                    out.timings[phase+'/'+name] = monotonic()-started
                    if not report.ran:
                        return out
                    out.violations.extend(_condition_violations(instance, phase, report.violations))
                    if _hard(out.violations) and not complete_learning:
                        out.status = 'fail'
                        return out
                if not complete_learning:
                    break  # first sample only: screen is a necessary gate
            out.complete_learning = complete_learning and not rule_freeze_v1_enabled()
            out.status = 'fail' if _hard(out.violations) else 'unknown'
        except Exception:
            # Inability to evaluate this independent gate is not a certificate.
            out.status = 'unknown'
            out.complete_learning = False
    return out


def screen(instance, plan) -> str:
    """Return 'fail' only for a failure of unchanged full checker; else unknown."""
    return diagnose(instance, plan).status


def ladder_evidence(instance, plan):
    """Exact ladder decision flags; full fallback when omission could matter.

    Strength only emits iacs.s11a.longitudinal_strength/checker.*; neither
    affects learn(). With freeze-v1 disabled, static and three sampled public
    parts cover every visibility/securing/e6/trim/draft diagnostic. A known
    non-iscode violation makes both ballast_only and hopeful false regardless
    of omitted strength. Otherwise obtain the unchanged complete verdict.
    """
    # Activated in-port checks may add learning-relevant draft evidence.
    out = (Evidence() if rule_freeze_v1_enabled() or active_stubs()
           or not _learning_sources_supported()
           else diagnose(instance, plan, complete_learning=True))
    if (out.complete_learning and out.status == 'fail'
            and any(not r.startswith('iscode') for r in out.rules)):
        return out
    started = monotonic()
    report = check(instance, plan)
    out.timings['full_fallback'] = monotonic()-started
    out.status = 'unknown' if report.passed else 'fail'
    out.violations = list(report.violations)
    out.complete_learning = True
    out.used_full_check = True
    return out


def ladder_verdict(instance, plan):
    """Drop-in c1/fx-scout.method.verdict; successful verdict always full checked."""
    out = ladder_evidence(instance, plan)
    return out.status != 'fail', out.rules, out.bow_down

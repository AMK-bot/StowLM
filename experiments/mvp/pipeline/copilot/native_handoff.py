"""Host-pinned handoff of native receipts; never seeds a lattice cache.

The host pins both the source catalogue and each recorded native receipt
independently. Reuse requires the *current* physics epoch and engine, not merely
a historical passed flag. Current reader semantics are audited again cheaply.
No native physics or search is run here; misses use the ordinary real lookup.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_json


class CatalogueDrift(ValueError):
    """An independently pinned source changed; no alternative lookup allowed."""


class NativeHandoff:
    """Callable ToolSet ``lattice=`` adapter, configured only by the host.

    ``receipts`` maps entry keys to ``{'path': ..., 'sha256': ...}``. These pins
    must come from the host's trusted preparation record, never model input.
    Source catalogue drift fails closed; absent/invalid receipt evidence falls
    back to the real lattice, which may need expensive fresh native checking.
    """

    native_authenticated = True

    def __init__(self, *, store_dir, catalogue_sha256, receipts):
        self.store_dir = Path(store_dir).resolve()
        self.catalogue_sha256 = catalogue_sha256
        self.receipts = deepcopy(receipts)
        self.last_miss_reason = None  # Host diagnostics only; never a certificate.

    def _catalogue(self):
        raw = (self.store_dir / 'catalogue.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.catalogue_sha256:
            raise CatalogueDrift('Native handoff catalogue differs from the independent host pin')
        catalogue = json.loads(raw)
        if not isinstance(catalogue, dict):
            raise ValueError('Native handoff catalogue must be an object')
        return catalogue

    def __call__(self, instance_sha256, rules):
        result = self.validated_receipt(instance_sha256, rules)
        if result is not None:
            return result
        from experiments.mvp.pipeline.lattice import lookup
        return lookup(instance_sha256, rules, store_dir=self.store_dir)

    lookup = __call__

    def validated_receipt(self, instance_sha256, rules):
        """Pure preflight: validate recorded evidence only, never native fallback."""
        from experiments.mvp.pipeline.lattice import store
        # Catalogue drift is a trust failure, not permission to use another row.
        catalogue = self._catalogue()
        key = store.entry_key(instance_sha256, rules)
        self.last_miss_reason = None
        try:
            result = self._reuse(key, instance_sha256, rules, catalogue)
        except CatalogueDrift:
            raise
        except (ValueError, TypeError, KeyError, OSError, AttributeError) as error:
            self.last_miss_reason = type(error).__name__ + ': ' + str(error)
            return None
        if result is None:
            self.last_miss_reason = 'Recorded evidence missing or failed current source, input, rule or native-gate validation'
        return result

    def _reuse(self, key, instance_sha256, rules, catalogue):
        from experiments.mvp.pipeline.lattice import store
        from experiments.mvp.pipeline.lattice.gates import engine_identity, gauge_verdict
        from experiments.mvp.reader.pipeline import intrinsic_violations
        from experiments.mvp.reader.schema import context_from_instance, validate_answer
        from stowage.checker import report_matches_inputs
        from stowage.frozen import active_stubs, require_clean
        from stowage.model import CheckReport, Instance, MovePlan

        pin = self.receipts.get(key)
        if pin is None:
            return None
        row = store._read(key, self.store_dir, catalogue=catalogue)
        if (row is None or not store._binding_source_matches(row)
                or row.get('instance_sha256') != instance_sha256
                or store.canonical_rule_set(row.get('canonical_rules', [])) != store.canonical_rule_set(rules)):
            return None
        token = store._current_token(row)
        if token is None or active_stubs():
            return None
        raw = Path(pin['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != pin['sha256']:
            return None
        recorded = json.loads(raw)
        # A native receipt may replace the source's old gate, never its inputs,
        # provenance, dates or plan. Other source fields remain byte-canonical.
        if any(canonical_json(recorded.get(name)) != canonical_json(value)
               for name, value in row.items() if name != 'gate'):
            return None
        gate = recorded['gate']
        identity = engine_identity()
        if (identity.get('passed') is not True or gate.get('engine_identity') != identity
                or row.get('engine_manifest_sha256') != token[1]
                or row.get('engine_version') != identity.get('engine_version')
                or gate['gauges']['physics'].get('physics_epoch') != token[2]
                or not row.get('computed_utc') or not gate.get('checked_utc')
                or recorded.get('checker') != gate.get('checker')
                or recorded.get('rechecked_utc') != gate.get('checked_utc')
                or row.get('status', 'checked_plan') != 'checked_plan'):
            return None
        instance = Instance.model_validate(row['instance'])
        plan = MovePlan.model_validate(row['plan'])
        report = CheckReport.model_validate(gate['checker'])
        require_clean(instance, plan, report, what='copilot recorded native handoff')
        if (sha256_json(instance.model_dump(mode='json')) != instance_sha256
                or gate.get('instance_sha256') != instance_sha256
                or gate.get('plan_sha256') != sha256_json(plan.model_dump(mode='json'))
                or gate.get('passed') is not True or not report.passed or report.count is None
                or not report_matches_inputs(report, instance, plan)
                or not report.parts or report.shut_out
                or any(v.severity == 'hard' for v in report.violations)
                or any(not part.ran or part.error or any(v.severity == 'hard' for v in part.violations)
                       for part in report.parts.values())
                or gate.get('lifts') != report.count.weighted_nonproductive
                or gate.get('total_lifts_now') != report.count.total_lifts_now
                or gate.get('base_passed') is False):
            return None
        gauges = gate['gauges']
        actual = gauge_verdict(gauges['physics'], len(plan.moves))
        if not actual['passed'] or any(gauges.get(name) != actual[name] for name in
                ('passed', 'all_frames', 'n_frames', 'outside_limits_steps', 'unavailable_steps')):
            return None
        canonical = store.canonical_rule_set(rules)
        audited = gate.get('rules', [])
        if (store.canonical_rule_set([item['envelope'] for item in audited]) != canonical
                or any(item.get('passed') is not True or item.get('violations') for item in audited)):
            return None
        context = context_from_instance(instance)
        for envelope in canonical:
            answer = validate_answer(canonical_json(envelope), context)
            if list(intrinsic_violations(instance, answer, plan)):
                return None
        # Detect source/reader/numerical drift during the pure validation too.
        if (self._catalogue() != catalogue or store._current_token(row) != token
                or store._read(key, self.store_dir, catalogue=catalogue) != row
                or not store._binding_source_matches(row) or active_stubs()):
            return None
        result = deepcopy(recorded)
        result.update(canonical_rules=deepcopy(rules), mode='stored', label='reused-recorded',
                      gate_source='recorded_native_handoff', freshly_rechecked=False,
                      reused_recorded=True, instruction_rechecked=True,
                      instruction_gate_identity=token[3],
                      rechecked_utc=gate['checked_utc'],
                      stored_message=('Reused recorded native gate, computed on '+row['computed_utc']+
                                      ', native checked at '+gate['checked_utc']+
                                      '; current source, epoch, inputs and reader rules verified now.'))
        result['native_handoff'] = dict(catalogue_sha256=self.catalogue_sha256,
                                       receipt_sha256=pin['sha256'], source_row_sha256=token[0],
                                       physics_epoch=token[2], engine_manifest_sha256=token[1])
        return result

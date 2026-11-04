"""Default directional reserves from measured response-family evidence.

For each metric, bound and strength station, reserve
``1.1 * (max(family unsafe, physical unsafe) + reference transfer)``.
The 10 percent allowance applies to observed unsafe error; deliberate maximum
FSM and conservative cargo-height arms remain separate physical reserves.
These are empirical envelopes, not uniform proofs. Every accepted departure
still requires the exact checker. A certificate records the reference epoch,
domain, stations and evidence so stale measurements cannot silently be reused.
An optional separately qualified ``boundary_list`` bank field replaces only
LIST's angle allowance with a directional kg m reserve at the exact operating
boundary. It requires independent passing holdouts and a branch qualification;
without that field the existing angular policy remains in force. Current
production entries carry this independent qualification and use boundary
mass-moment reserves; legacy entries without it retain angular reserves.
Source: elementary one-sided error propagation and the triangle inequality.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from stowage.conventions import canonical_vessel_id

_BANK_PATH = Path(__file__).with_name("margin_policy_data.json")
_ROOT = Path(__file__).resolve().parents[3]
_POLICY = "measured-directional-1.1"
LINEAR_MARGIN_POLICY_VERSION = "1.5"
_MULTIPLIER = 1.1
_SIDES = ("lower_unsafe", "upper_unsafe")
_METRICS = ("gm_m", "trim_m", "list_rad", "draught_m", "sf_n", "bm_nm",
            "draught_aft_m", "draught_forward_m")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def policy_application_identity():
    """Bind reserve application/qualification source independently of physics."""
    from .dependencies import dependency_fingerprint
    return dependency_fingerprint('application')


def policy_inputs():
    """Small content identity for persisted calibration-policy dependencies."""
    return {"format": 1, "policy": _POLICY, "builder_version": LINEAR_MARGIN_POLICY_VERSION,
            "application_identity": policy_application_identity(),
            "bank_sha256": hashlib.sha256(_BANK_PATH.read_bytes()).hexdigest()
            if _BANK_PATH.exists() else None}


def _inputs_hash(certificate):
    """Hash the actual measured inputs; qualification time is audit-only."""
    inputs = {key: certificate[key] for key in (
        "response_identity", "reference_identity", "domain", "station_x_m",
        "water_density_kg_m3", "family_calibration_cache_key", "measured_unsafe",
        "reference_transfer", "evidence", "policy_inputs")}
    if "boundary_list" in certificate:
        inputs["boundary_list"] = certificate["boundary_list"]
    if "holdout_qualification" in certificate:
        inputs["holdout_qualification"] = certificate["holdout_qualification"]
    if "necessary" in certificate:
        inputs["necessary"] = certificate["necessary"]
    return _digest(inputs)


def _boundary_builder_inputs_hash(record):
    """D30 identity of precisely the boundary builder's physical inputs."""
    from .boundary_calibration import BOUNDARY_CALIBRATION_VERSION
    inputs = {key:record[key] for key in ('response_identity', 'reference_identity',
        'domain', 'station_x_m', 'water_density_kg_m3', 'limit_rad', 'gm_min_m', 'kind')}
    inputs['builder_version'] = BOUNDARY_CALIBRATION_VERSION
    if record.get('direction') == 'necessary':
        inputs['direction'] = 'necessary'
        inputs['residual_definition'] = record.get('residual_definition')
    return _digest(inputs)


def _boundary_list(record, model, *, response, reference, domain, direction="conservative",
                   check_evidence=True):
    """Validate optional independently qualified boundary mass-moment evidence.

    At signed heel limit, exact outward capacity is ``s*My_boundary``.
    Its unsafe residual against the affine capacity is
    ``tan(limit)*R - s*My_boundary`` in kg m, where R includes maximum FSM.
    Reserving that moment avoids dividing an interior angle error by small GM.
    Finite holdouts remain empirical; the exact checker is mandatory.
    """
    if not isinstance(record, dict) or record.get("format") != 1:
        raise ValueError("invalid boundary LIST qualification")
    if direction == "necessary" and (record.get("direction") != "necessary" or
            record.get("residual_definition") != "exact outward capacity minus affine capacity"):
        raise ValueError("necessary boundary LIST requires opposite residual evidence")
    expected = dict(response_identity=response, reference_identity=reference,
        domain=domain, station_x_m=np.asarray(model.station_x_m).tolist(),
        water_density_kg_m3=model.water_density_kg_m3,
        limit_rad=min(model.max_list_rad, model.band.max_abs_heel_rad),
        gm_min_m=model.gm_min_m, kind="boundary-moment-kgm")
    if any(record.get(key) != value for key, value in expected.items()):
        raise ValueError("boundary LIST domain/reference/geometry mismatch")
    if not np.isfinite(expected["limit_rad"]) or not 0 < expected["limit_rad"] < np.pi/2:
        raise ValueError("invalid boundary LIST angle")
    from . import boundary_calibration
    primitive = Path(boundary_calibration.__file__).resolve()
    if (record.get('builder_version') != boundary_calibration.BOUNDARY_CALIBRATION_VERSION
            or record.get('builder_inputs_hash') != _boundary_builder_inputs_hash(record)):
        raise ValueError('boundary LIST builder identity mismatch')
    primitive_sha = hashlib.sha256(primitive.read_bytes()).hexdigest()
    pins = record.get('evidence')
    if not isinstance(pins, list) or not any(
            isinstance(pin, dict) and pin.get('sha256') == primitive_sha
            and (_ROOT/str(pin.get('path', ''))).resolve() == primitive for pin in pins):
        raise ValueError('boundary LIST primitive source pin mismatch')
    values = {}
    measured_field = "measured_optimistic_kgm" if direction == "necessary" else "measured_unsafe_kgm"
    for field in (measured_field, "reference_transfer_kgm"):
        raw = record.get(field)
        if not isinstance(raw, dict) or set(raw) != set(_SIDES):
            raise ValueError("invalid directional boundary LIST envelope")
        values[field] = {}
        for side in _SIDES:
            value = np.asarray(raw[side], dtype=float)
            if value.shape != () or not np.isfinite(value) or value < 0:
                raise ValueError("invalid directional boundary LIST envelope")
            values[field][side] = float(value)
    qualification = record.get("qualification")
    if (not isinstance(qualification, dict)
            or qualification.get("independent_holdout_passed") is not True
            or qualification.get("branch_monotonicity_checked") is not True):
        raise ValueError("boundary LIST requires independent passing holdout and branch qualification")
    seeds = [qualification.get(name) for name in ("calibration_seed", "holdout_seed")]
    if any(type(seed) is not int or seed < 0 for seed in seeds) or seeds[0] == seeds[1]:
        raise ValueError("boundary LIST requires independent holdout seed")
    for field in ("calibration_samples", "holdout_samples"):
        counts = qualification.get(field)
        if (not isinstance(counts, dict) or set(counts) != set(_SIDES)
                or any(type(counts[side]) is not int or counts[side] <= 0 for side in _SIDES)):
            raise ValueError("boundary LIST requires measured populations for both directions")
    excess = qualification.get("max_holdout_excess_kgm")
    if not isinstance(excess, dict) or set(excess) != set(_SIDES):
        raise ValueError("boundary LIST requires measured passing holdout excess")
    for side in _SIDES:
        value = np.asarray(excess[side], dtype=float)
        if value.shape != () or not np.isfinite(value) or value > 0:
            raise ValueError("boundary LIST holdout exceeds its reserve")
    if direction == "necessary":
        interior = record.get("interior_qualification")
        if (not isinstance(interior, dict) or interior.get("independent_holdout_passed") is not True
                or interior.get("affine_gm_filter_applied") is not False):
            raise ValueError("necessary boundary LIST requires unfiltered interior holdout evidence")
        interior_seeds = [interior.get(name) for name in ("calibration_seed", "holdout_seed")]
        if (any(type(seed) is not int or seed < 0 for seed in interior_seeds)
                or interior_seeds[0] == interior_seeds[1] or
                any(type(interior.get(name)) is not int or interior[name] <= 0 for name in
                    ("calibration_samples", "holdout_samples"))):
            raise ValueError("necessary boundary LIST requires independent interior populations")
        for side in _SIDES:
            value = np.asarray(interior.get("max_holdout_excess_kgm", {}).get(side), dtype=float)
            if value.shape != () or not np.isfinite(value) or value > 0:
                raise ValueError("necessary interior LIST holdout exceeds its reserve")
    if check_evidence:
        _evidence(record.get("evidence"))
    return {side: _MULTIPLIER*(values[measured_field][side]
                              +values["reference_transfer_kgm"][side]) for side in _SIDES}


def _envelope(values, station_count, *, name):
    if not isinstance(values, dict):
        raise ValueError(f"invalid {name} directional envelope")
    result = {}
    for side in _SIDES:
        if not isinstance(values.get(side), dict):
            raise ValueError(f"invalid {name} directional envelope")
        result[side] = {}
        for metric in _METRICS:
            try:
                value = np.asarray(values[side][metric], dtype=float)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"missing or invalid {name} {side}/{metric}") from exc
            shape = (station_count,) if metric in ("sf_n", "bm_nm") else ()
            if value.shape != shape or not np.isfinite(value).all() or np.any(value < 0):
                raise ValueError(f"invalid {name} {side}/{metric}")
            result[side][metric] = value
    return result


def _zeros(station_count):
    return {side: {metric: [0.]*station_count if metric in ("sf_n", "bm_nm") else 0.
                   for metric in _METRICS} for side in _SIDES}


def optimistic_envelope(signed, station_count):
    """D49 outer-bound errors from independently retained opposite residuals.

    Keys retain their historical bound labels for the row consumer: lower uses
    max(exact-linear), upper uses max(linear-exact). These are the stored
    conservatism observations, never an inference from unsafe envelope values.
    LIST angles are diagnostics only; outer LIST requires boundary moments.
    """
    if not isinstance(signed, dict):
        raise ValueError("missing optimistic directional observations")
    return _envelope({side: signed.get(side.replace("unsafe", "conservatism"))
                      for side in _SIDES}, station_count, name="optimistic")


def _necessary_reserves(record, model, diagnostics, *, response, reference, domain,
                        check_evidence=True):
    if not isinstance(record, dict) or record.get("format") != 1 or record.get("direction") != "necessary":
        raise ValueError("necessary direction lacks independent qualification")
    count = len(model.station_x_m)
    measured = _envelope(record.get("measured_optimistic"), count, name="necessary measured")
    transfer = _envelope(record.get("reference_transfer"), count, name="necessary reference-transfer")
    reserves = _envelope(record.get("reserves"), count, name="necessary reserve")
    fresh = optimistic_envelope(diagnostics.get("signed_errors"), count)
    for side in _SIDES:
        for metric in _METRICS:
            if (np.any(measured[side][metric] < fresh[side][metric]) or
                    not np.array_equal(reserves[side][metric],
                        _MULTIPLIER*(measured[side][metric]+transfer[side][metric]))):
                raise ValueError("necessary reserve understates its optimistic measured envelope")
    from .qualification import validate_holdout_record
    qualification = record.get("holdout_qualification")
    if not isinstance(qualification, dict) or qualification.get("direction") != "necessary":
        raise ValueError("necessary direction lacks independent qualification")
    validate_holdout_record(qualification, measured, transfer, station_x_m=model.station_x_m)
    if "boundary_list" not in record:
        raise ValueError("necessary direction requires independent boundary LIST qualification")
    reserves["list_moment_kgm"] = _boundary_list(record["boundary_list"], model,
        response=response, reference=reference, domain=domain, direction="necessary",
        check_evidence=check_evidence)
    edge = record.get('edge_holdout_qualification')
    if (not isinstance(edge,dict) or edge.get('direction') != 'necessary'
            or edge.get('kind') != 'necessary-domain-edges-v1'):
        raise ValueError('necessary direction lacks independent edge holdout qualification')
    validate_holdout_record(edge,measured,transfer,station_x_m=model.station_x_m)
    if (edge.get('holdout_seed') == qualification.get('holdout_seed')
            or edge.get('holdout_seed') in qualification.get('calibration_seeds',
                [qualification.get('calibration_seed')])):
        raise ValueError('necessary edge holdout must be independent of ordinary holdout')
    if check_evidence and 'evidence' in record:
        _evidence(record['evidence'])
    return reserves


def _legacy_outer_missing_edges(record):
    necessary = record.get('necessary') if isinstance(record, dict) else None
    return (isinstance(necessary, dict) and necessary.get('format') == 1
        and necessary.get('direction') == 'necessary'
        and 'edge_holdout_qualification' not in necessary)


def _conservative_evidence(record):
    evidence = record.get('conservative_evidence', record.get('evidence'))
    if 'conservative_evidence' not in record and _legacy_outer_missing_edges(record):
        # The original D49 bank mixed this outer-only experiment producer into
        # shared evidence. N1 revoked that outer block; its changed source must
        # not revoke the independently qualified inward policy. No numerical,
        # ordinary, or shared pin is excluded by this exact-path migration.
        outer_builder = (_ROOT/'experiments/linear-model/epoch_requalification/necessary.py').resolve()
        if isinstance(evidence, list):
            evidence = [pin for pin in evidence if not (isinstance(pin, dict)
                and isinstance(pin.get('path'), str)
                and (_ROOT/pin['path']).resolve() == outer_builder)]
    return evidence


def _evidence(records):
    if not isinstance(records, list) or not records:
        raise ValueError("measured margin bank requires pinned evidence")
    for record in records:
        try:
            path = Path(record["path"])
            actual = hashlib.sha256((path if path.is_absolute() else _ROOT/path).read_bytes()).hexdigest()
            if actual != record["sha256"]:
                raise ValueError("margin-policy evidence checksum mismatch")
        except (KeyError, TypeError, OSError) as exc:
            raise ValueError("margin-policy evidence missing or malformed") from exc


def _evidence_metadata(records):
    """Separate malformed qualification metadata from unavailable file bytes."""
    if (not isinstance(records,list) or not records
            or any(not isinstance(record,dict)
                or not isinstance(record.get('path'),str) or not record['path']
                or not isinstance(record.get('sha256'),str) or len(record['sha256']) != 64
                or any(character not in '0123456789abcdef' for character in record['sha256'])
                for record in records)):
        raise ValueError('margin-policy evidence metadata is malformed')


def _known_production_missing(family, response_identity, bank):
    known = bank.get("known_vessel_ids", [])
    if not isinstance(known, list) or any(not isinstance(value, str) for value in known):
        raise ValueError("invalid production margin-policy vessel registry")
    parameters = getattr(family, "parameters", {})
    return canonical_vessel_id(parameters.get("id", "")) in [canonical_vessel_id(value) for value in known] and response_identity not in bank["entries"]


def _production_holdout_missing(family, response_identity, bank):
    """Known failed tails cannot fall back to a calibration-only certificate."""
    parameters = getattr(family, "parameters", {})
    record = bank["entries"].get(response_identity)
    return (canonical_vessel_id(parameters.get("id", "")) in
            [canonical_vessel_id(value) for value in bank.get("known_vessel_ids", [])]
            and isinstance(record, dict) and "holdout_qualification" not in record)


def _policy_application_stale(family, response_identity, bank):
    """Production measurements require qualification by the current rules.

    Unknown research banks may supply unversioned empirical observations through
    the explicit offline hook. Once a bank binds application source, however,
    even research must reject a mismatch. Known production entries never gain a
    current application identity simply by assembling a new certificate.
    """
    record = bank['entries'].get(response_identity)
    if not isinstance(record, dict):
        return False
    if 'policy_application_identity' in record:
        return record['policy_application_identity'] != policy_application_identity()
    parameters = getattr(family, 'parameters', {})
    return canonical_vessel_id(parameters.get('id', '')) in [
        canonical_vessel_id(value) for value in bank.get('known_vessel_ids', [])]


def assert_policy_epoch(family, response_identity, *, allow_unqualified=False):
    """Reject a missing known production epoch before expensive calibration.

    Unknown research geometries keep their empirical family behavior. A known
    production parent or slice requires its exact response profile in the
    default measured bank. Explicit private forced rebuilding may collect new
    qualification evidence, but its certificate cannot be used by solvers.
    """
    bank = json.loads(_BANK_PATH.read_text()) if _BANK_PATH.exists() else {"format": 1, "entries": {}}
    if not isinstance(bank, dict) or bank.get("format") != 1 or not isinstance(bank.get("entries"), dict):
        raise ValueError("invalid measured margin bank")
    if _known_production_missing(family, response_identity, bank) and not allow_unqualified:
        raise ValueError("production linear policy epoch is stale; requalify")
    if _production_holdout_missing(family, response_identity, bank) and not allow_unqualified:
        raise ValueError("production linear policy requires independent holdout qualification")
    if _policy_application_stale(family, response_identity, bank) and not allow_unqualified:
        raise ValueError("production linear policy application epoch is stale; requalify")


def qualify_policy(family, instance, diagnostics, *, bank=None, allow_reference_qualification=False):
    """Bind fresh family measurements and any matching measured physical bank.

    A new geometry without bank evidence still receives an explicit empirical
    family certificate. A known geometry's changed same-method reference epoch
    is rejected until its bank is updated; it must not regress to an already
    observed underfit family envelope. Other reference methods remain distinct.
    ``bank`` is an explicit offline/test hook; production loads the data file.
    """
    from .cache import response_calibration_identity
    model = family.template
    identity = response_calibration_identity(family)
    stations = np.asarray(model.station_x_m, dtype=float)
    fresh = _envelope(diagnostics.get("signed_errors"), len(stations), name="family")
    reference = diagnostics.get("reference_identity", diagnostics.get("reference_inputs"))
    domain = diagnostics.get("family_domain")
    key = diagnostics.get("family_calibration_cache_key")
    if not isinstance(reference, dict) or not isinstance(domain, dict) or not isinstance(key, str):
        raise ValueError("margin policy requires reference, domain and family calibration identity")
    source = "inline" if bank is not None else "default"
    production_usable = True
    inputs = policy_inputs()
    if bank is None:
        bank = json.loads(_BANK_PATH.read_text()) if _BANK_PATH.exists() else {"format": 1, "entries": {}}
    if not isinstance(bank, dict) or bank.get("format") != 1 or not isinstance(bank.get("entries"), dict):
        raise ValueError("invalid measured margin bank")
    if source == "default" and _known_production_missing(family, identity, bank):
        if not allow_reference_qualification:
            raise ValueError("production linear policy epoch is stale; requalify")
        production_usable = False
    if source == "default" and _production_holdout_missing(family, identity, bank):
        if not allow_reference_qualification:
            raise ValueError("production linear policy requires independent holdout qualification")
        production_usable = False
    record = bank["entries"].get(identity)
    if _policy_application_stale(family, identity, bank):
        if not allow_reference_qualification:
            raise ValueError("measured margin bank policy application epoch is stale; requalify")
        record = None
        production_usable = False
    physical = _zeros(len(stations)); transfer = _zeros(len(stations)); evidence = []
    if record is not None:
        if not isinstance(record, dict):
            raise ValueError("invalid measured margin bank entry")
        recorded_reference = record.get("reference_identity")
        if recorded_reference != reference:
            if not allow_reference_qualification:
                raise ValueError("measured margin bank reference epoch is stale; requalify")
            record = None
            production_usable = False
        if record is not None:
            if record.get("domain") != domain or not np.array_equal(record.get("station_x_m"), stations):
                raise ValueError("measured margin bank domain/stations differ from response family")
            evidence = _conservative_evidence(record)
            _evidence(evidence)
            if not record.get("equivalence"):
                raise ValueError("measured margin bank requires fresh numerical equivalence evidence")
            physical = record.get("physical_unsafe")
            transfer = record.get("reference_transfer")
    measured = _envelope(physical, len(stations), name="physical")
    reference_error = _envelope(transfer, len(stations), name="reference-transfer")
    maxima = {side: {metric: np.maximum(fresh[side][metric], measured[side][metric]).tolist()
                    for metric in _METRICS} for side in _SIDES}
    reserves = {side: {metric: (_MULTIPLIER*(np.asarray(maxima[side][metric])+
                                reference_error[side][metric])).tolist()
                      for metric in _METRICS} for side in _SIDES}
    if record is not None and "holdout_qualification" in record:
        from .qualification import validate_holdout_record
        validate_holdout_record(record["holdout_qualification"], maxima,
                                reference_error, station_x_m=stations)
    certificate = dict(format=1, policy=_POLICY, status="empirical_measured",
        builder_version=LINEAR_MARGIN_POLICY_VERSION,
        response_identity=identity, reference_identity=reference, domain=domain,
        station_x_m=stations.tolist(), water_density_kg_m3=float(model.water_density_kg_m3),
        family_calibration_cache_key=key, multiplier=_MULTIPLIER,
        measured_unsafe=maxima,
        reference_transfer={side: {metric: value.tolist() for metric, value in items.items()}
                            for side, items in reference_error.items()},
        reserves=reserves, evidence=evidence, bank_enriched=record is not None,
        bank_source=source, policy_inputs=inputs,
        mandatory_exact_checker=True, production_usable=production_usable)
    if record is not None and "holdout_qualification" in record:
        certificate["holdout_qualification"] = json.loads(json.dumps(
            record["holdout_qualification"], allow_nan=False))
    if record is not None and "boundary_list" in record:
        _boundary_list(record["boundary_list"], model, response=identity,
                       reference=reference, domain=domain)
        # JSON isolation keeps caller-owned bank objects out of live certificates.
        certificate["boundary_list"] = json.loads(json.dumps(record["boundary_list"], allow_nan=False))
    necessary_source = record.get('necessary') if record is not None else None
    # N1 invalidated the old interior-only outer qualification. Keep the
    # independently qualified conservative policy available while that outer
    # block is remeasured. A present malformed edge certificate still fails;
    # absence never authorizes necessary rows or an inferred reserve.
    legacy_outer = _legacy_outer_missing_edges(record)
    if legacy_outer:
        certificate['necessary_unavailable'] = 'N1: independent edge holdout qualification required'
    if record is not None and "necessary" in record and not legacy_outer:
        necessary = json.loads(json.dumps(record["necessary"], allow_nan=False))
        if 'necessary_evidence' in record:
            necessary['evidence'] = json.loads(json.dumps(record['necessary_evidence'], allow_nan=False))
        opposite = optimistic_envelope(diagnostics.get("signed_errors"), len(stations))
        physical = _envelope(necessary.pop("physical_optimistic", None), len(stations), name="necessary physical")
        necessary["measured_optimistic"] = {side: {metric:
            np.maximum(opposite[side][metric], physical[side][metric]).tolist()
            for metric in _METRICS} for side in _SIDES}
        transfer = _envelope(necessary.get("reference_transfer"), len(stations), name="necessary transfer")
        necessary["reserves"] = {side: {metric: (_MULTIPLIER*(np.asarray(
            necessary["measured_optimistic"][side][metric])+transfer[side][metric])).tolist()
            for metric in _METRICS} for side in _SIDES}
        _necessary_reserves(necessary, model, diagnostics, response=identity,
                            reference=reference, domain=domain, check_evidence=False)
        # Large immutable edge archives are validated once when assembling
        # qualification, like inward evidence. Per-row calls validate the saved
        # certificate digest and bank identity, without rereading these archives.
        # A stale outer-only source revokes only this direction. All numerical
        # metadata above remains fail-closed, including malformed edge gates.
        if 'evidence' in necessary:
            _evidence_metadata(necessary['evidence'])
        boundary_evidence = necessary['boundary_list'].get('evidence')
        if boundary_evidence is not None:
            _evidence_metadata(boundary_evidence)
        try:
            if 'evidence' in necessary:
                _evidence(necessary['evidence'])
            if boundary_evidence is not None:
                _evidence(boundary_evidence)
        except ValueError as exc:
            certificate['necessary_unavailable'] = 'necessary qualification evidence unavailable: '+str(exc)
        else:
            certificate["necessary"] = necessary
    certificate["inputs_hash"] = _inputs_hash(certificate)
    certificate["qualified_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    certificate["sha256"] = _digest(certificate)
    return certificate


def default_reserves(model, *, direction):
    """Validate one assembled certificate and return its per-bound reserves.

    Legacy hand-built algebra models retain their old error-margin contract.
    Production family models require a certificate even when no physical bank
    exists: missing/stale policy metadata demands a rebuild, never a silent
    return to an underfit historic 192-state envelope.
    """
    if direction not in ("conservative", "necessary"):
        raise ValueError("margin direction must be conservative or necessary")
    diagnostics = getattr(model, "measurement_diagnostics", {})
    if not isinstance(diagnostics, dict):
        raise ValueError("invalid margin-policy diagnostics")
    certificate = diagnostics.get("margin_policy")
    if certificate is None:
        if "family_calibration_cache_key" in diagnostics or "family_domain" in diagnostics:
            raise ValueError("family model lacks default margin-policy certificate; rebuild")
        if direction == "necessary":
            raise ValueError("necessary direction lacks independent qualification")
        return None
    if not isinstance(certificate, dict):
        raise ValueError("invalid default margin-policy certificate")
    body = {key: value for key, value in certificate.items() if key != "sha256"}
    try:
        intact = certificate.get("sha256") == _digest(body)
    except (TypeError, ValueError):
        intact = False
    if (not intact or certificate.get("format") != 1 or certificate.get("policy") != _POLICY
            or certificate.get("builder_version") != LINEAR_MARGIN_POLICY_VERSION
            or certificate.get("status") != "empirical_measured"
            or certificate.get("multiplier") != _MULTIPLIER
            or certificate.get("mandatory_exact_checker") is not True
            or certificate.get("production_usable") is not True):
        raise ValueError("invalid default margin-policy certificate")
    try:
        qualified = datetime.strptime(certificate["qualified_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        valid_inputs = certificate.get("inputs_hash") == _inputs_hash(certificate)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid margin-policy input hash or UTC qualification timestamp") from exc
    if not valid_inputs or (qualified-datetime.now(timezone.utc)).total_seconds() > 60:
        raise ValueError("invalid margin-policy input hash or UTC qualification timestamp")
    for field, expected in (("response_identity", diagnostics.get("response_calibration_identity")),
                            ("reference_identity", diagnostics.get("reference_identity", diagnostics.get("reference_inputs"))),
                            ("domain", diagnostics.get("family_domain")),
                            ("family_calibration_cache_key", diagnostics.get("family_calibration_cache_key"))):
        if expected is None or certificate.get(field) != expected:
            raise ValueError(f"stale margin-policy {field}")
    if (certificate.get("water_density_kg_m3") != model.water_density_kg_m3 or
            not np.array_equal(certificate.get("station_x_m"), model.station_x_m)):
        raise ValueError("stale margin-policy density/stations")
    if (not isinstance(certificate.get('policy_inputs'), dict)
            or certificate['policy_inputs'].get('application_identity') != policy_application_identity()):
        raise ValueError("margin-policy application source changed; requalify")
    if certificate.get("bank_source") == "default" and certificate.get("policy_inputs") != policy_inputs():
        raise ValueError("measured margin bank changed; rebuild margin-policy certificate")
    count = len(model.station_x_m)
    measured = _envelope(certificate.get("measured_unsafe"), count, name="certified")
    transfer = _envelope(certificate.get("reference_transfer"), count, name="reference-transfer")
    reserves = _envelope(certificate.get("reserves"), count, name="default reserve")
    fresh = _envelope(diagnostics.get("signed_errors"), count, name="family")
    for side in _SIDES:
        for metric in _METRICS:
            if (np.any(measured[side][metric] < fresh[side][metric]) or
                    not np.array_equal(reserves[side][metric],
                                       _MULTIPLIER*(measured[side][metric]+transfer[side][metric]))):
                raise ValueError("default margin-policy reserve understates its measured envelope")
    if "boundary_list" in certificate:
        reserves["list_moment_kgm"] = _boundary_list(certificate["boundary_list"], model,
            response=certificate["response_identity"], reference=certificate["reference_identity"],
            domain=certificate["domain"])
    if "holdout_qualification" in certificate:
        from .qualification import validate_holdout_record
        validate_holdout_record(certificate["holdout_qualification"], measured,
                                transfer, station_x_m=model.station_x_m)
    if direction == "necessary":
        return _necessary_reserves(certificate.get("necessary"), model, diagnostics,
            response=certificate["response_identity"], reference=certificate["reference_identity"],
            domain=certificate["domain"], check_evidence=False)
    return reserves

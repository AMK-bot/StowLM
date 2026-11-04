"""Content identities and checker-bound solver-reference comparisons.

Reference proof labels preserve the solver's declared domain: a proof over the
engine model does not certify optimality over every physically feasible plan.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
from pathlib import Path

from stowage.checker import report_matches_inputs
from stowage.model import CheckReport, PlannerResult
from stowage.versions import component_versions


_EPOCH_KEYS = ('epoch_id', 'source_sha256', 'regen_manifest_sha256')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _require_evidence(value):
    """A clean wrapper must not conceal diagnostic reference lineage."""
    if isinstance(value, dict):
        if (any(value.get(key) is True for key in ('diagnostic', 'tiny_run', 'allow_drift'))
                or value.get('official') is False or value.get('evidence_eligible') is False):
            raise ValueError('official reference refuses diagnostic or ineligible provenance')
        for nested in value.values():
            _require_evidence(nested)
    elif isinstance(value, list):
        for nested in value:
            _require_evidence(nested)


def report_has_current_versions(report):
    """Input hashes bind bytes; independently require the current checker stack."""
    return (report.provenance.versions == component_versions()
            and not report.provenance.input_versions)


def validate_reference_run(config, summary, expected):
    """Admit an official reference only from a matching, configuration-bound run."""
    if not isinstance(config, dict) or not isinstance(summary, dict):
        raise ValueError('official reference requires run configuration and summary')
    _require_evidence(config)
    _require_evidence(summary)
    if (config.get('tiny_run') is not False or summary.get('official') is not True
            or summary.get('tiny_run') is not False or config.get('active_stubs') != {}):
        raise ValueError('official reference requires explicit official run provenance')
    fingerprint = _digest(config)
    if summary.get('config_sha256') != fingerprint:
        raise ValueError('reference summary is not bound to its run configuration')
    for key in _EPOCH_KEYS:
        if (not isinstance(expected.get(key), str) or not expected[key]
                or config.get(key) != expected[key] or summary.get(key) != expected[key]):
            raise ValueError(f'official reference used different or missing {key}')
    versions = component_versions()
    if (config.get('versions') != versions or expected.get('versions') != versions
            or summary.get('versions') != versions):
        raise ValueError('official reference component versions differ from the current run')
    for key in ('split_version', 'split_entries_sha256', 'split'):
        if config.get(key) != expected.get(key):
            raise ValueError(f'official reference used different {key}')
    return fingerprint


def _validate_official_record(record, row, config):
    _require_evidence(record)
    if (record.get('official') is not True or record.get('tiny_run') is not False
            or record.get('diagnostic') is not False or record.get('evidence_eligible') is not True):
        raise ValueError('official reference row lacks explicit official provenance')
    if record.get('config_sha256') != _digest(config):
        raise ValueError('reference row is not bound to its run configuration')
    for key in (*_EPOCH_KEYS, 'split_version', 'split_entries_sha256'):
        if not row.get(key) or record.get(key) != row[key] or config.get(key) != row[key]:
            raise ValueError(f'official reference row used different or missing {key}')
    inputs = config.get('inputs')
    matching = ([item for item in inputs if isinstance(item, dict)
                 and item.get('spec_key') == row['spec_key']] if isinstance(inputs, list) else [])
    input_keys = ('input_sha256', 'spec_key', 'split', 'rung', *_EPOCH_KEYS,
                  'split_version', 'split_entries_sha256')
    if (len(matching) != 1 or any(matching[0].get(key) != record.get(key) for key in input_keys)
            or ('spec' in row and matching[0].get('spec') != row['spec'])):
        raise ValueError('reference row is not bound to its configured input inventory')
    versions = component_versions()
    result = record.get('result') or {}
    for value in (record, result, result.get('plan') or {}, record.get('report') or {}):
        provenance = value.get('provenance') or {}
        if provenance.get('versions') != versions or provenance.get('input_versions'):
            raise ValueError('reference artifact component versions differ from the current run')


def _file_digest(path: Path) -> str:
    """Hash weights in bounded memory, without materialising model tensors."""
    digest = hashlib.sha256()
    before = path.stat()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError(f'weight content changed while hashing: {path}')
    return digest.hexdigest()


def _path_digest(value: str | Path) -> str:
    root = Path(value).expanduser().resolve()
    if root.is_file():
        return _file_digest(root)
    if not root.is_dir():
        raise ValueError(f'weight identity requires an existing local path: {value}')
    paths = sorted(root.rglob('*'))
    if any(path.is_symlink() and path.is_dir() for path in paths):
        raise ValueError('weight identity cannot traverse nested directory symlinks')
    files = [path for path in paths if path.is_file()]
    if not files:
        raise ValueError(f'weight identity requires a nonempty directory: {value}')
    # Include filenames so shard/config replacement and file addition/removal
    # change the identity. All content is pinned, including tokenizer and config.
    contents = [(path.relative_to(root).as_posix(), _file_digest(path)) for path in files]
    if sorted(root.rglob('*')) != paths:
        raise ValueError(f'weight content changed while hashing: {root}')
    return hashlib.sha256(json.dumps(contents, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def pin_weight_content(configuration: Mapping) -> dict:
    """Copy JSON planner configuration and freshly pin local model/adapter bytes.

    Deliberately do not cache by pathname, size or timestamp: in-place checkpoint
    replacement must invalidate resume even if those attributes are preserved.
    Optional adapter=None means no adapter. Missing local weights fail closed.
    """
    result = dict(configuration)
    for name in ('model', 'adapter'):
        path = result.get(f'{name}_path')
        if path is not None:
            if not isinstance(path, (str, Path)):
                raise ValueError(f'{name}_path must be a local path')
            result[f'{name}_content_sha256'] = _path_digest(path)
    return result


def validate_reference(record, row, *, instance=None, official=False, run_config=None):
    """Validate a solver reference and return its value plus explicit proof scope.

    A checker-approved incumbent can be compared even after a solver deadline;
    this helper never turns it into a shippable planner result. When available,
    the actual evaluated instance is mandatory for checking serialized linkage.
    Official comparisons require explicit row/run linkage in addition to input hashes.
    """
    if record is None:
        return None
    if any(record.get(key) != row.get(key) for key in (
            'input_sha256', 'spec_key', 'split', 'rung')):
        raise ValueError('reference solver used different input bytes or split')
    if official:
        if not isinstance(run_config, dict):
            raise ValueError('official reference requires its run configuration')
        _validate_official_record(record, row, run_config)
    result_data = record.get('result') or {}
    if result_data.get('planner') not in ('solver.exact', 'solver.scalable'):
        raise ValueError('reference method must be solver.exact or solver.scalable')
    result = PlannerResult.model_validate(result_data)
    from eval.plan_diagnostics import restore_check_report
    report = restore_check_report(record.get('report'))
    required = {'moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints'}
    if (result.plan is None or result.later_plans or result.status not in ('ok', 'timeout')
            or not report.passed or report.count is None or report.shut_out
            or not required.issubset(report.parts)
            or any(v.severity == 'hard' for v in report.violations)
            or any(not part.ran or part.error or any(v.severity == 'hard' for v in part.violations)
                   for part in report.parts.values())):
        raise ValueError('reference solver requires a complete passing full-checker report')
    if instance is not None and (instance.plan_calls or not report_matches_inputs(report, instance, result.plan)):
        raise ValueError('reference solver report is not bound to evaluated inputs')
    objective = report.count.weighted_nonproductive
    if not math.isfinite(objective) or objective < 0:
        raise ValueError('reference solver objective must be finite and nonnegative')
    metrics = result.metrics
    status = metrics.get('optimality_status')
    scope = metrics.get('optimality_scope')
    physical = metrics.get('physical_domain_optimality_certified') is True
    proof = 'feasible_incumbent'
    # A repaired candidate is a different plan; original solve metadata is no
    # longer a certificate for it. Likewise, an interrupted proof is incomplete.
    certified_objective = metrics.get('objective')
    same_objective = (isinstance(certified_objective, (int, float))
                      and not isinstance(certified_objective, bool)
                      and math.isfinite(certified_objective)
                      and math.isclose(certified_objective, objective, rel_tol=1e-9, abs_tol=1e-9))
    if (not result.repaired and same_objective and result.status == 'ok' and status == 'OPTIMAL'
            and not metrics.get('watchdog_timeout') and metrics.get('termination') != 'deadline'):
        if physical and scope:
            proof = 'proven_optimum'
        elif scope in ('engine_model_weighted_lifts', 'configured_engine_weighted_lifts'):
            proof = 'proven_model_optimum'
    if result.repaired:
        scope, physical = None, False
    return {'method': result.planner, 'planner_version': result.planner_version,
            'weighted_nonproductive': objective, 'proof_status': proof,
            'optimality_status': status, 'optimality_scope': scope,
            'physical_domain_optimality_certified': physical,
            'repaired': result.repaired}

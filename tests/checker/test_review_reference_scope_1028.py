"""Document the live cache bypass and keep diagnostic output under pytest tmp_path."""
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

from stowage.checker import full, stability, stability_policy
from stowage.checker.perf import current_cache, reuse_enabled


def test_reference_documents_shared_canonical_and_parse_paths():
    description = full.check_reference.__doc__
    assert 'six' in description
    assert 'canonical projection' in description
    assert 'parsed-parameter LRU' in description
    assert 'public-builder' in description


def test_reference_shares_projection_baseline_and_parameter_parse_lru(monkeypatch):
    events = []
    values = (-1., 1., 10., 20., 0.)
    def projection(*args):
        events.append('projection')
        return values
    monkeypatch.setattr(stability_policy, 'projected_policy', projection)
    def inspect_paths(*args, **kwargs):
        assert not reuse_enabled() and current_cache() is None
        for _ in range(2):
            assert stability._parsed_parameter_payload(b'id="scope-probe"')[0]['id'] == 'scope-probe'
            assert stability._canonical_policy_baseline('scope-probe', 'digest', 'construction',
                stability_policy.provider_identity()) == values
        return 'scope-observed'
    monkeypatch.setattr(full, '_check', inspect_paths)
    stability._parsed_parameter_payload.cache_clear()
    stability._canonical_policy_baseline.cache_clear()
    try:
        assert full.check_reference(None, None) == 'scope-observed'
        assert events == ['projection']
        assert stability._parsed_parameter_payload.cache_info().hits == 1
        assert stability._canonical_policy_baseline.cache_info().hits == 1
        assert reuse_enabled()
    finally:
        stability._parsed_parameter_payload.cache_clear()
        stability._canonical_policy_baseline.cache_clear()


def test_cold_diagnostic_writes_only_to_explicit_test_output(monkeypatch, tmp_path):
    """Run the real diagnostic orchestration with cheap physics stubs in a mirror."""
    import os
    from stowage import io, moves
    from stowage.model import PartReport
    root = Path(__file__).resolve().parents[2]
    source = root/'experiments/checker/followup-1028/canonical-policy-cold-regression.py'
    mirror = tmp_path/'repo/experiments/checker/followup-1028'
    mirror.mkdir(parents=True)
    script = mirror/source.name
    script.write_bytes(source.read_bytes())
    output = tmp_path/'diagnostic-output'
    monkeypatch.setattr(sys, 'argv', [str(script), '--output-dir', str(output)])
    monkeypatch.setattr(io, 'load_instance', lambda path: SimpleNamespace(id='scope-probe', call_index=0))
    monkeypatch.setattr(moves, 'apply_plan', lambda *args: SimpleNamespace(
        final_state=SimpleNamespace(ship=object())))
    monkeypatch.setattr(stability, 'check_stability', lambda *args, **kwargs: PartReport(name='stability'))
    # Register the original environment values so direct changes made by the
    # diagnostic are restored when monkeypatch unwinds.
    for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                'MKL_NUM_THREADS', 'STOWAGE_STABILITY_TIME_BUDGET_S', 'STOWAGE_VESSEL_CACHE_DIR'):
        if key in os.environ:
            monkeypatch.setenv(key, os.environ[key])
        else:
            monkeypatch.setenv(key, 'scope-probe')
    runpy.run_path(str(script), run_name='__main__')
    assert list(mirror.iterdir()) == [script]
    assert (output/'canonical-policy-cold-regression.json').is_file()

"""Exact strength eigenproblem reuse and dependency invalidation."""
import numpy as np
import pytest

from stowage.checker import strength_assessment
from stowage.checker.perf import evaluation_cache, without_reuse


def test_companion_reuse_is_bitwise_identical_for_signed_envelopes(monkeypatch):
    original = np.polynomial.polynomial.polyroots
    calls = []

    def roots(coefficients):
        calls.append(coefficients.copy())
        return original(coefficients)

    monkeypatch.setattr(np.polynomial.polynomial, 'polyroots', roots)
    # Count actual eigensolves while declaring this test spy the known pure
    # implementation. Unknown providers are covered by the bypass test below.
    dependencies = strength_assessment._root_dependencies()
    monkeypatch.setattr(strength_assessment, '_ORIGINAL_ROOT_DEPENDENCIES', dependencies)
    monkeypatch.setattr(strength_assessment, '_ROOT_DEPENDENCY_IDENTITIES', tuple(map(id, dependencies)))
    rng = np.random.default_rng(1028)
    for degree in (2, 3, 4):
        for _ in range(100):
            coefficients = rng.standard_normal(degree+1)
            with without_reuse():
                reference = strength_assessment._interior_roots(coefficients)
                negative_reference = strength_assessment._interior_roots(-coefficients)
            before = len(calls)
            with evaluation_cache():
                actual = strength_assessment._interior_roots(coefficients)
                negative = strength_assessment._interior_roots(-coefficients)
            assert actual == reference
            assert negative == negative_reference
            assert len(calls) == before+1


def test_companion_reuse_invalidates_coefficient_changes():
    with evaluation_cache():
        first = strength_assessment._interior_roots(np.array([.1, -1., 1.]))
        second = strength_assessment._interior_roots(np.array([.2, -1., 1.]))
    assert first != second


def test_live_root_provider_change_bypasses_populated_scoped_cache(monkeypatch):
    coefficients = np.array([.1, -1., 1.])
    calls = []

    def replacement(value):
        calls.append(value)
        return np.array([.25])

    with evaluation_cache():
        before = strength_assessment._interior_roots(coefficients)
        monkeypatch.setattr(np.polynomial.polynomial, 'polyroots', replacement)
        assert strength_assessment._interior_roots(coefficients) == [.25]
        assert strength_assessment._interior_roots(coefficients) == [.25]
    assert before != [.25]
    assert len(calls) == 2


@pytest.mark.parametrize('provider', ['real', 'imag'])
def test_live_root_acceptance_provider_bypasses_populated_cache(monkeypatch, provider):
    coefficients = np.array([.1 if provider == 'real' else .3, -1., 1.])
    calls = []

    def replacement(value):
        calls.append(value)
        return .25 if provider == 'real' else 0.

    with evaluation_cache():
        before = strength_assessment._interior_roots(coefficients)
        monkeypatch.setattr(np, provider, replacement)
        actual = strength_assessment._interior_roots(coefficients)
        with without_reuse():
            expected = strength_assessment._interior_roots(coefficients)
    assert calls
    assert actual == expected
    assert actual != before

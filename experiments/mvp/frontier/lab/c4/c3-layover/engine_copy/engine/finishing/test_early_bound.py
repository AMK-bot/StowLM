"""Keep the retained spring-chain certificate's original deterministic tests."""
import pytest

from .early_bound import selftest, _verified_rod_bound


def test_original_spring_chain_selftest():
    result = selftest()
    assert result['cases'] == 128


def test_invalid_chain_cannot_make_certificate():
    with pytest.raises(ValueError):
        _verified_rod_bound([1.], [0.], [1.], 1., cg_heights=[2.])

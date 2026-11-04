"""Expose the copied finisher's original numerical and hook-restoration tests."""
from .fast_finish import selftest


def test_original_finisher_selftest():
    result = selftest()
    assert result['interior_cases'] > 0
    assert result['general_cases'] > 0

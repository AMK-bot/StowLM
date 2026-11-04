"""Limits on synthetic generated cargo, distinct from its sourced CSC plate."""

# APPROXIMATION: D60 truncates the generated gross-mass distribution at the
# maximum covered by the synthetic IRS Guidelines Table 3.3.1 securing design.
# This does not change individual CSC ratings or the checker's evaluated mass.
# tests/generate/test_d60_mass_cap.py asserts agreement with checker/lashing.py.
MAX_GENERATED_GROSS_MASS_KG = 30_500.0


def triangular_cdf(x: float, low: float, mode: float, high: float) -> float:
    """CDF of the original triangular draw, including point masses."""
    if x >= high:
        return 1.0
    if x <= low:
        return 0.0
    if x <= mode:
        return (x - low) ** 2 / ((high - low) * (mode - low))
    return 1.0 - (high - x) ** 2 / ((high - low) * (high - mode))

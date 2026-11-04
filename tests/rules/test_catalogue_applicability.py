"""Catalogue scope agrees with the existing executable wave-load rejection."""

import pytest

from stowage.checker.strength_limits import wave_loads
from stowage.rules.catalogue import load_catalogue


@pytest.mark.parametrize("length,applicable", [(89.999, False), (90., True),
                                              (500., True), (500.001, False),
                                              (501., False)])
def test_s11a_length_scope_matches_wave_load_domain(length, applicable):
    # IACS S11A.1.1.2(i), data/raw/iacs/ur-s11a.txt PDF p1:
    # "90 m ≤ L ≤ 500 m". Keep all other scope conditions inside their ranges.
    parameters = load_catalogue()["iacs.s11a.longitudinal_strength"].parameters
    assert (parameters["minimum_length"].value <= length
            <= parameters["maximum_length"].value) is applicable
    kwargs = dict(length_m=length, beam_m=length/7, draught_m=length/21,
                  block_coefficient=.7, waterplane_coefficient=.8,
                  bow_flare_coefficient=.1)
    if applicable:
        assert wave_loads(length/2, **kwargs)
    else:
        with pytest.raises(ValueError, match="outside S11A.*applicability"):
            wave_loads(length/2, **kwargs)

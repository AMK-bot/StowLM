"""Failed independent strength tails must never become qualification passes."""
import pytest

from stowage.linear.margin_policy import _zeros
from stowage.linear.qualification import qualify_holdout, validate_holdout_record


def inputs():
    measured, transfer, holdout = _zeros(2), _zeros(2), _zeros(2)
    return measured, transfer, holdout, dict(station_x_m=[0., 5.], calibration_seed=1,
        holdout_seed=2, calibration_samples=128, holdout_samples=64)


def test_zero_holdout_passes_and_upper_gm_is_ignored():
    measured, transfer, holdout, kwargs = inputs()
    holdout["upper_unsafe"]["gm_m"] = 100.
    result = qualify_holdout(measured, transfer, holdout, **kwargs)
    assert result["independent_holdout_passed"]
    assert "gm_m" not in result["max_excess"]["upper_unsafe"]


def test_all_failed_bounds_and_station_positions_are_reported():
    measured, transfer, holdout, kwargs = inputs()
    holdout["lower_unsafe"]["sf_n"] = [1349.66, 0.]
    holdout["upper_unsafe"]["bm_nm"] = [0., 7137.46]
    holdout["upper_unsafe"]["sf_n"] = [55304.14, 0.]
    with pytest.raises(ValueError) as error:
        qualify_holdout(measured, transfer, holdout, **kwargs)
    message = str(error.value)
    for text in ("lower_unsafe/sf_n@0", "upper_unsafe/bm_nm@5", "upper_unsafe/sf_n@0",
                 "1349.66", "7137.46", "55304.14"):
        assert text in message


def test_transfer_reserve_and_ten_percent_are_used_exactly():
    measured, transfer, holdout, kwargs = inputs()
    measured["lower_unsafe"]["gm_m"] = .01
    transfer["lower_unsafe"]["gm_m"] = .02
    holdout["lower_unsafe"]["gm_m"] = 1.1*(.01+.02)
    qualify_holdout(measured, transfer, holdout, **kwargs)
    holdout["lower_unsafe"]["gm_m"] += 1e-12
    with pytest.raises(ValueError, match="lower_unsafe/gm_m"):
        qualify_holdout(measured, transfer, holdout, **kwargs)


@pytest.mark.parametrize("key,value", [("holdout_seed", 1), ("calibration_samples", 0),
    ("holdout_samples", -1), ("holdout_seed", True)])
def test_independence_and_population_required(key, value):
    measured, transfer, holdout, kwargs = inputs()
    kwargs[key] = value
    with pytest.raises(ValueError):
        qualify_holdout(measured, transfer, holdout, **kwargs)


def test_record_recomputed_instead_of_trusting_pass_flag():
    measured, transfer, holdout, kwargs = inputs()
    record = qualify_holdout(measured, transfer, holdout, **kwargs)
    validate_holdout_record(record, measured, transfer, station_x_m=[0., 5.])
    record["holdout_unsafe"]["lower_unsafe"]["sf_n"][0] = 1.
    with pytest.raises(ValueError, match="holdout failed"):
        validate_holdout_record(record, measured, transfer, station_x_m=[0., 5.])


@pytest.mark.parametrize("field,value", [("station_x_m", [0., 6.]), ("multiplier", 2.)])
def test_roundtrip_record_tamper_rejected(field, value):
    measured, transfer, holdout, kwargs = inputs()
    record = qualify_holdout(measured, transfer, holdout, **kwargs)
    record[field] = value
    with pytest.raises(ValueError, match="mismatch"):
        validate_holdout_record(record, measured, transfer, station_x_m=[0., 5.])


@pytest.mark.parametrize("stations", [[5., 0.], [0., 0.]])
def test_station_order_and_uniqueness_required(stations):
    measured, transfer, holdout, kwargs = inputs()
    kwargs["station_x_m"] = stations
    with pytest.raises(ValueError, match="ordered"):
        qualify_holdout(measured, transfer, holdout, **kwargs)


def test_enriched_calibration_requires_a_new_untouched_seed():
    measured,transfer,holdout,kwargs=inputs()
    record=qualify_holdout(measured,transfer,holdout,**kwargs)
    record['calibration_seeds']=[1,3,4]
    result=validate_holdout_record(record,measured,transfer,station_x_m=[0.,5.])
    assert result['calibration_seeds']==[1,3,4]
    record['calibration_seeds'].append(2)
    with pytest.raises(ValueError,match='overlaps'):
        validate_holdout_record(record,measured,transfer,station_x_m=[0.,5.])


@pytest.mark.parametrize('seeds', [[],[3],[1,1],[True,1],[1,-1]])
def test_invalid_enrichment_seed_provenance_rejected(seeds):
    measured,transfer,holdout,kwargs=inputs()
    record=qualify_holdout(measured,transfer,holdout,**kwargs)
    record['calibration_seeds']=seeds
    with pytest.raises(ValueError,match='calibration seeds'):
        validate_holdout_record(record,measured,transfer,station_x_m=[0.,5.])

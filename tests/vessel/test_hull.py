"""Assembly must not conceal absent operational safety data."""
import pytest

from stowage.params import load_param_file


def _assert_offset_tables(hull):
    assert hull.stations == []
    assert len(hull.cross_curves) == 31
    assert all(len(curve.displacement_kg) == len(hull.hydrostatics)
               for curve in hull.cross_curves)
    assert hull.bonjean
    assert all(rows[0][0] == 0.0 and rows[-1][0] >= hull.depth_m
               for rows in hull.bonjean.values())


def test_missing_operational_data_rejected():
    from stowage.vessel.hull import build_hull_model

    params = load_param_file("fleet", "feeder_500")
    # Remove a mandatory input even after the production params provide it.
    params["hull"].pop("max_trim_m", None)
    params.get("operating_limits", {}).pop("max_trim_m", None)
    with pytest.raises(ValueError, match="missing vessel operational/windage inputs"):
        build_hull_model(params)


@pytest.mark.parametrize("ship", ["feeder_500", "feeder_800", "feeder_1700"])
def test_full_fleet_loader_assembles_real_hull(ship):
    from stowage.checker.strength_limits import permissible_stations
    from stowage.vessel.fleet import load_vessel

    params = load_param_file("fleet", ship)
    vessel = load_vessel(ship, with_hull=True)
    hull = vessel.hull
    assert hull is not None
    supplied = {**params["hull"], **params.get("operating_limits", {})}
    for name in ("min_draught_fwd_m", "max_trim_m", "max_list_rad", "min_gm_m",
                 "bilge_keel", "windage_area_m2", "windage_lever_m"):
        value = supplied[name]
        if isinstance(value, dict):
            value = value["value"]
        assert getattr(hull, name) == value
    assert len(vessel.tanks) == len(params["tanks"])
    assert hull.stations
    assert hull.stations == permissible_stations(params, hull)
    assert hull.hydrostatics[-1].draught_m == hull.scantling_draught_m
    assert hull.hydrostatics[0].displacement_kg < hull.lightship_mass_kg
    assert hull.cross_curves[-1].heel_rad > 1.04
    assert len(hull.cross_curves) == 31
    assert all(len(curve.displacement_kg) == len(hull.hydrostatics)
               for curve in hull.cross_curves)
    assert hull.bonjean
    assert all(rows[0][0] == 0.0 and rows[-1][0] >= hull.depth_m
               for rows in hull.bonjean.values())
    assert sum(b[2] for b in hull.lightship_distribution) == pytest.approx(hull.lightship_mass_kg)


@pytest.mark.parametrize("ship", ["feeder_500", "feeder_800", "feeder_1700"])
def test_offsets_model_assembly_at_each_feeder_dimensions(ship):
    """Explicit D20 geometry fixture while fleet-file migration has another owner.

    All operating/lightship inputs are from the real params. Only hull_form is
    replaced by the documented public-offset mapping, without test-only limits.
    """
    from stowage.vessel.hull import build_hull_model
    from stowage.vessel.hydrostatics import HullGeometry

    params = load_param_file("fleet", ship)
    params["hull_form"] = dict(family="stowlm_offsets_v1", parent="kcs",
        offsets_format="kcs_fix", offsets_path="data/raw/kcs/kcs.fix",
        offsets_sha256="aafc7e4f4d773bc50452d397985554c6a20f054150b8865ef70d61f836809190",
        reference_lpp_m=230.0, reference_beam_m=32.2,
        reference_draught_m=10.8, reference_depth_m=16.0)
    hull = build_hull_model(params)
    _assert_offset_tables(hull)
    geometry = HullGeometry.from_params(params)
    row = hull.hydrostatics[-1]
    expected = geometry.hydrostatic_row(row.draught_m, hull.water_density_kg_m3,
                                      kg_m=hull.lightship_vcg_m)
    assert row.mct_nm_per_cm == expected.mct_nm_per_cm


def test_production_assembly_rejects_retired_analytic_family():
    from stowage.vessel.hull import build_hull_model

    params = load_param_file("fleet", "feeder_500")
    params["hull_form"] = {"family": "stowlm_separable_rounded_bilge_v1"}
    with pytest.raises(ValueError, match="production hull assembly requires checksum-pinned offsets"):
        build_hull_model(params)

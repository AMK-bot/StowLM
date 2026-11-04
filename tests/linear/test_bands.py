"""Cheap manifest/band checks; no full hull assembly or error calibration."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.linear.bands import (
    ballast_windows, band_for_instance, departure_manifest, nominal_height,
    sample_manifest_loading,
)
from stowage.model.fixtures import build_instance


def test_departure_manifest_discharges_and_deduplicates():
    instance = build_instance(layover=True)
    expected = ({p.container_id for p in instance.onboard.placements}
                - set(instance.discharge_list)) | set(instance.load_list)
    assert tuple(box.id for box in departure_manifest(instance)) == tuple(sorted(expected))
    duplicate = instance.model_copy(update={'load_list': instance.load_list*2})
    assert departure_manifest(duplicate) == departure_manifest(instance)


def test_ballast_windows_validate_adjustability_and_capacity():
    instance = build_instance()
    result = ballast_windows(instance, {'WB-P': (1000., 2000.)})
    assert result['WB-P'] == (1000., 2000.)
    assert result['WB-S'] == (0., 102500.)
    for invalid in ({'FO-1': (0., 1.)}, {'WB-P': (-1., 2.)},
                    {'WB-P': (3., 2.)}, {'WB-P': (0., 1e9)}):
        with pytest.raises(ValueError):
            ballast_windows(instance, invalid)


def test_uniform_known_heights_and_empty_manifest():
    instance = build_instance()
    high_cubes = [box.model_copy(update={'height_m': 2.896, 'oog': None})
                  for box in instance.containers]
    assert nominal_height(instance.model_copy(update={'containers': high_cubes})) == pytest.approx(2.896)
    assert nominal_height(instance.model_copy(update={'load_list': []})) == pytest.approx(2.591)


def test_fixed_manifest_band_and_window_metadata(monkeypatch):
    instance = build_instance()
    fake = SimpleNamespace(max_z_m=11., submerged=lambda a,f,*args:
                           SimpleNamespace(displacement_kg=1e6*(a+f)/2))
    monkeypatch.setattr('stowage.linear.physics.geometry_for', lambda instance: fake)
    band = band_for_instance(instance, ballast_windows_kg={'WB-P': (1000., 2000.), 'WB-S': (3000., 4000.)})
    cargo = sum(box.gross_mass_kg for box in departure_manifest(instance))
    assert band._cargo_mass_kg == cargo
    assert band.displacement_max_kg-band.displacement_min_kg == pytest.approx(2000.)
    assert band._tank_mass_bounds_kg['WB-P'] == (1000., 2000.)
    assert band.draught_min_m < band.draught_max_m


def test_explicit_trim_restricts_local_validity_and_draught_expansion(monkeypatch):
    instance=build_instance()
    fake=SimpleNamespace(max_z_m=11.,submerged=lambda a,f,*args:
                         SimpleNamespace(displacement_kg=1e6*(a+f)/2))
    monkeypatch.setattr('stowage.linear.physics.geometry_for',lambda instance:fake)
    windows={'WB-P':(1000.,2000.),'WB-S':(3000.,4000.)}
    default=band_for_instance(instance,ballast_windows_kg=windows)
    narrow=band_for_instance(instance,ballast_windows_kg=windows,max_abs_trim_m=.4)
    assert default.max_abs_trim_m==instance.vessel.hull.max_trim_m
    assert narrow.max_abs_trim_m==pytest.approx(.4)
    assert narrow.displacement_min_kg==default.displacement_min_kg
    assert narrow.displacement_max_kg==default.displacement_max_kg
    assert narrow.draught_min_m==pytest.approx(narrow.displacement_min_kg/1e6-.2)
    assert narrow.draught_max_m==pytest.approx(narrow.displacement_max_kg/1e6+.2)
    assert narrow._tank_mass_bounds_kg==default._tank_mass_bounds_kg


@pytest.mark.parametrize('limit',[0.,-.1,np.nan,np.inf,2.01])
def test_explicit_trim_rejects_invalid_or_above_vessel_limits(limit):
    with pytest.raises(ValueError,match='max_abs_trim_m'):
        band_for_instance(build_instance(),max_abs_trim_m=limit)


def test_sampler_keeps_real_manifest_mass_and_heights():
    instance = build_instance()
    cells = sorted(instance.vessel.cells, key=lambda cell: cell.id)
    constant = 4_247_500.
    model = SimpleNamespace(
        cell_ids=tuple(cell.id for cell in cells),
        cell_lcg_m=np.array([cell.lcg_m for cell in cells]),
        cell_vcg_m=np.array([cell.vcg_base_m+2.591/2 for cell in cells]),
        const_mass_kg=constant, const_moment_x_kgm=60*constant,
        tank_lcg_m=np.zeros(3), trim_line=(0., -1e-8, 6e-7),
        band=SimpleNamespace(max_abs_trim_m=2.),
    )
    sampled = sample_manifest_loading(model, instance, np.zeros(3), np.random.default_rng(3721))
    assert sampled is not None
    masses, heights = sampled
    assert masses.sum() == pytest.approx(sum(box.gross_mass_kg for box in departure_manifest(instance)))
    assert np.count_nonzero(masses) == 5  # One 40 ft occupies two cells, three 20 ft occupy one each.
    assert np.isfinite(heights).all()
    assert np.all(heights[masses>0] > np.array([cell.vcg_base_m for cell in cells])[masses>0])


def test_sampler_adapts_remaining_moment_without_changing_real_boxes():
    instance=build_instance()
    cells=sorted(instance.vessel.cells,key=lambda c:c.id)
    constant=4_247_500.
    model=SimpleNamespace(cell_ids=tuple(c.id for c in cells),
        cell_lcg_m=np.array([c.lcg_m for c in cells]),
        cell_vcg_m=np.array([c.vcg_base_m+2.591/2 for c in cells]),
        const_mass_kg=constant,const_moment_x_kgm=60*constant,
        tank_lcg_m=np.zeros(3),trim_line=(0.,-1e-8,6e-7),
        band=SimpleNamespace(max_abs_trim_m=2.))
    class CentralTrimRandom:
        def __init__(self,seed): self.generator=np.random.default_rng(seed)
        def uniform(self,lower,upper): return (lower+upper)/2
        def __getattr__(self,name): return getattr(self.generator,name)
    cargo=sum(b.gross_mass_kg for b in departure_manifest(instance))
    target=cargo*50. # Midpoint target; the40ft box contributes at exactly50m.
    for seed in range(12):
        first=sample_manifest_loading(model,instance,np.zeros(3),CentralTrimRandom(seed))
        repeat=sample_manifest_loading(model,instance,np.zeros(3),CentralTrimRandom(seed))
        assert first is not None
        np.testing.assert_array_equal(first[0],repeat[0])
        np.testing.assert_array_equal(first[1],repeat[1])
        mass,heights=first
        assert mass.sum()==pytest.approx(cargo)
        assert np.count_nonzero(mass)==5
        # Three equal12t boxes cannot split evenly across46.9/53.1m.
        # Adaptive proposals attain the nearest discrete moment, rather than
        # allowing all three random draws to accumulate on the same end.
        assert abs(model.cell_lcg_m@mass-target)<=12_000.*(53.1-46.9)/2+1e-5
        assert np.all(heights[mass>0]>np.array([c.vcg_base_m for c in cells])[mass>0])


@pytest.mark.parametrize("vessel_name", ["feeder_500", "feeder_800", "feeder_1700"])
def test_full_fleet_column_shortcut_preserves_mass_and_limits(vessel_name):
    """Stress the shortcut at stack capacities, not legal ISO box gross masses.

    Production stack capacities can exceed 30.48 t per available cell. The
    separate full-manifest tests cover physical boxes with that gross limit.
    """
    from stowage.conventions import make_container_id
    from stowage.linear.bands import _sample_whole_columns
    from stowage.model.cargo import SpecialStowage
    from stowage.vessel.fleet import load_vessel

    base = build_instance()
    vessel = load_vessel(vessel_name, with_hull=False)  # No hull/KN build in this quick test.
    stacks = sorted(vessel.stacks, key=lambda stack: stack.id)
    lookup = {(stack.bay, stack.row, stack.on_deck): stack for stack in stacks}
    left = next(stack for stack in stacks if stack.on_deck and (stack.bay-1)%4 == 0
                and (stack.bay+2, stack.row, True) in lookup)
    right = lookup[(left.bay+2, left.row, True)]
    assert len(left.cell_ids) == len(right.cell_ids)
    assert left.max_mass_kg == pytest.approx(right.max_mass_kg)
    specs = [(40, 2.896, 2*left.max_mass_kg/len(left.cell_ids), 'on_deck')
             for _ in left.cell_ids]
    for stack in stacks:
        if stack.id in (left.id, right.id):
            continue
        specs.extend((20, 2.591, stack.max_mass_kg/len(stack.cell_ids),
                      'on_deck' if stack.on_deck else 'under_deck') for _ in stack.cell_ids)
    containers = [base.containers[1].model_copy(update={
        'id': make_container_id('QZXU', i+1), 'length_ft': length, 'height_m': height,
        'gross_mass_kg': gross, 'dg': None, 'oog': None,
        'special_stowage': [SpecialStowage(tag)],
    }) for i,(length,height,gross,tag) in enumerate(specs)]
    instance = base.model_copy(update={'vessel': vessel, 'containers': containers,
                                       'load_list': [box.id for box in containers]})
    cells = sorted(vessel.cells, key=lambda cell: cell.id)
    model = SimpleNamespace(cell_ids=tuple(cell.id for cell in cells),
                            cell_vcg_m=np.array([cell.vcg_base_m+2.591/2 for cell in cells]))
    sampled = _sample_whole_columns(model, instance, np.random.default_rng(3722))
    assert sampled is not None
    masses, heights = sampled
    assert masses.sum() == pytest.approx(sum(stack.max_mass_kg for stack in stacks))
    assert masses.sum() == pytest.approx(sum(box.gross_mass_kg for box in containers))
    assert np.count_nonzero(masses) == len(vessel.cells)
    assert {model.cell_ids[i] for i in np.flatnonzero(masses)} == {cell.id for cell in vessel.cells}
    index = {cid: i for i,cid in enumerate(model.cell_ids)}
    for stack in stacks:
        indices = [index[cid] for cid in stack.cell_ids]
        assert masses[indices].sum() == pytest.approx(stack.max_mass_kg)
        assert np.all(np.diff(heights[indices]) > 0)
        box_height = 2*(heights[indices][0]-stack.base_z_m)
        assert heights[indices][-1]+box_height/2 <= stack.base_z_m+stack.max_height_m+1e-7

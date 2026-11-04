"""One checked exclusion of the unchanged historical N1 TRAINING point.

At rho1000 that physical point exceeds the actual family displacement band.
Every original grid, independent holdout, lineage, sample minimum and disjoint-
state check is retained. Other hulls/densities use the original review rule.
"""
from __future__ import annotations
from dataclasses import asdict
import hashlib
import inspect
import json
import math
from pathlib import Path

RUN = Path(__file__).resolve().parent
OWNER = RUN.parent.parent
ROOT = OWNER.parents[2]
HISTORICAL = OWNER.parent / 'fix-t3-linear'
CERTIFICATE = RUN / 'review-exclusion/feeder_500.json'


def pin(path):
    path = Path(path).resolve()
    return dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def _expected(raw, template):
    from compute import abort_check
    from profile_run import adapter_pin
    abort_check()
    point_path = HISTORICAL / 'claude-edge-seed51/state-011.json'
    fixture_path = HISTORICAL / 'claude-review/feeder_500/frozen-instance.json'
    point = json.loads(point_path.read_text())
    fixture = json.loads(fixture_path.read_text())
    masses = point['all_tank_masses_kg']
    ids = point['tank_ids']
    tanks = sorted(fixture['vessel']['tanks'], key=lambda tank: tank['id'])
    if (ids != [tank['id'] for tank in tanks] or ids != list(template.tank_ids)
            or len(masses) != len(ids)):
        raise ValueError('historical reviewer tank order/count differs from immutable full fixture')
    if any(not math.isfinite(float(mass)) or mass < 0 for mass in masses):
        raise ValueError('historical reviewer tank masses are invalid')
    lightship = float(fixture['vessel']['hull']['lightship_mass_kg'])
    fixed_point = {key: point['point_load'][key] for key in ('x_m', 'y_m', 'z_m', 'mass_kg')}
    total = math.fsum([lightship, fixed_point['mass_kg'], *masses])
    upper = float(template.band.displacement_max_kg)
    if (float(template.water_density_kg_m3) != 1000. or raw['ship'] != 'feeder_500'
            or not total > upper or float(fixture['vessel']['hull']['lpp_m']) != float(template._reference_params['hull']['lpp_m'])):
        raise ValueError('historical training exclusion is not the exact freshwater500 out-of-band case')
    return dict(format=1, status='excluded historical TRAINING diagnostic; not a holdout',
        ship='feeder_500', water_density_kg_m3=1000., original_point=pin(point_path),
        original_full_fixture=pin(fixture_path), point_load=fixed_point,
        tank_ids=ids, all_tank_masses_kg=masses, lightship_mass_kg=lightship,
        mass_construction='math.fsum(lightship mass, unchanged fixed-point mass, every original tank mass); independent of water density',
        total_mass_kg=total, band_upper_kg=upper, positive_exceedance_kg=total-upper,
        family_band=asdict(template.band), family_domain=raw['diagnostics']['family_domain'],
        response_identity=raw['diagnostics']['response_calibration_identity'],
        reference_identity=raw['diagnostics']['reference_identity'],
        raw_source=pin(RUN/'ordinary/feeder_500/raw.json'),
        raw_key=raw['key'], station_x_m=template.station_x_m.tolist(),
        physical_inputs=raw['physical_inputs'], profile_adapter=adapter_pin(),
        necessary_adapter=pin(Path(__file__)), original_necessary=pin(OWNER/'necessary.py'),
        exclusion_scope='Only the original historical reviewer TRAINING point; no grid or independent holdout observation excluded or modified',
        physical_point_changed=False, tank_vector_changed=False, holdouts_skipped=0)


def validate_exclusion(promote, folder, raw, template):
    if raw.get('ship') != 'feeder_500' or float(template.water_density_kg_m3) != 1000.:
        return False
    expected = _expected(raw, template)
    actual = promote.read(CERTIFICATE)
    if actual != expected:
        raise ValueError('freshwater reviewer exclusion certificate is incomplete, changed or stale')
    if folder.resolve() != (RUN/'edges-v3/feeder_500').resolve():
        raise ValueError('review exclusion applies only to this final freshwater edge campaign')
    return True


def install(module):
    original = inspect.getsource(module.load_edge_populations)
    before = "if ship == 'feeder_500' or review_path.exists():"
    after = "if review_path.exists() or (ship == 'feeder_500' and not _validated_review_exclusion(promote, folder, raw, template)):"
    if original.count(before) != 1:
        raise ValueError('unchanged necessary review-load guard no longer matches reviewed source')
    module._validated_review_exclusion = validate_exclusion
    # The entire original function is retained; one historical training-only
    # file requirement gains the mandatory, exact exclusion proof above.
    exec(compile(original.replace(before, after), str(Path(__file__)), 'exec'), module.__dict__)


def main():
    from profile_run import load_driver
    driver = load_driver()
    family, _, _, _, _ = driver.prepare('feeder_500', RUN/'ordinary')
    raw = json.loads((RUN/'ordinary/feeder_500/raw.json').read_text())
    from stowage.linear.cache import response_calibration_identity
    from stowage.linear.family_calibration import domain_for
    assert raw['diagnostics']['response_calibration_identity'] == response_calibration_identity(family)
    expected = _expected(raw, family.template)
    if CERTIFICATE.exists():
        if json.loads(CERTIFICATE.read_text()) != expected:
            raise ValueError('preserve conflicting reviewer exclusion certificate')
    else:
        CERTIFICATE.parent.mkdir(parents=True, exist_ok=True)
        CERTIFICATE.write_text(json.dumps(expected, indent=2, allow_nan=False)+'\n')
    print(json.dumps(dict(certificate=str(CERTIFICATE.relative_to(ROOT)),
        total_mass_kg=expected['total_mass_kg'], upper_kg=expected['band_upper_kg'],
        exceedance_kg=expected['positive_exceedance_kg'], holdouts_skipped=0)), flush=True)


if __name__ == '__main__':
    main()

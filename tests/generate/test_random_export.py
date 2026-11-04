"""Analytic expectations of the configured booking distribution, without draws."""
from copy import deepcopy

import pytest

from stowage.generate.random_export import ExportPolicy, expected_cargo_mix
from stowage.params import load_param_file


def profile(*, low=0, mode=5, high=10, tare=0, empty=0, multiplier=1):
    number = lambda value: {"value": value}
    item = dict(iso_size_type="22G1", share=number(1), tare_kg=number(tare),
                max_gross_kg=number(high), empty_share=number(empty),
                empty_weight_kg={"value": number(tare)},
                loaded_weight_kg=dict(family="triangular", min=number(low),
                                      mode=number(mode), max=number(high)))
    pod = dict(share=number(1), payload_weight_multiplier=number(multiplier))
    return dict(equipment=[item], rotations=[dict(pods=[pod])])


@pytest.mark.parametrize("mode", [0, 2, 5, 10])
def test_unclipped_triangular_mean_including_endpoint_modes(mode):
    assert expected_cargo_mix(profile(mode=mode)) == pytest.approx((1, (10 + mode) / 3))


def test_clipping_and_asymmetric_pod_shares():
    # Symmetric Tri(0,5,10), multiplier2: E[min(10,2X)] =25/3.
    p = profile(multiplier=2)
    assert expected_cargo_mix(p)[1] == pytest.approx(25 / 3)
    p["rotations"][0]["pods"].append(dict(share={"value": 3},
                                               payload_weight_multiplier={"value": .5}))
    # Normalized shares1/4 and3/4; E[.5X]=2.5.
    assert expected_cargo_mix(p)[1] == pytest.approx((25 / 3 + 3 * 2.5) / 4)


def test_lower_clip_tare_and_empty_point_mass():
    # Tri(2,2,4), tare1, multiplier0: lower clip gives2; empties weigh1.
    assert expected_cargo_mix(profile(low=2, mode=2, high=4, tare=1,
                                      empty=.25, multiplier=0))[1] == pytest.approx(1.75)
    # Tri(2,3,4), tare1: max(2,1+(X-1)/2) has mean2+1/12.
    assert expected_cargo_mix(profile(low=2, mode=3, high=4, tare=1,
                                      multiplier=.5))[1] == pytest.approx(25 / 12)


def test_equipment_normalization_lengths_and_degenerate_distribution():
    p = profile(low=2, mode=2, high=2, tare=1, empty=1)
    other = deepcopy(p["equipment"][0])
    other.update(iso_size_type="L5G1", share={"value": 3})
    p["equipment"].append(other)
    assert expected_cargo_mix(p) == pytest.approx((1.9375, 1))


def test_real_profile_uses_first_route_and_preserves_source():
    p = load_param_file("cargo", "feeder_export")
    before = deepcopy(p)
    assert expected_cargo_mix(p) == pytest.approx((1.6725, 15901.435568524617))
    assert p == before
    p["rotations"] = p["rotations"][:1]
    assert expected_cargo_mix(p) == pytest.approx((1.6725, 15901.435568524617))


def test_d60_conditional_mean_keeps_original_triangle_and_pod_transform():
    # Decreasing Tri(0,0,H), truncated at H/2, has retained probability3/4
    # and first momentH/6, hence conditional mean2H/9. Replacing high by
    # H/2 would instead give H/6; capping would preserve the discarded tail.
    high = 61_000.
    assert expected_cargo_mix(profile(low=0, mode=0, high=high))[1] == pytest.approx(2 * high / 9)
    # With payload multiplier2, surviving raw draws end at H/4. Integrating
    # 2X against the original decreasing density yields conditional5H/21.
    assert expected_cargo_mix(profile(low=0, mode=0, high=high,
                                      multiplier=2))[1] == pytest.approx(5 * high / 21)


def test_d60_expected_mix_rejects_impossible_loaded_support():
    with pytest.raises(ValueError, match="D60 loaded distribution has no mass"):
        expected_cargo_mix(profile(low=31_000, mode=32_000, high=34_000, tare=4000))
    # An entirely empty profile never samples its unused loaded component.
    assert expected_cargo_mix(profile(low=31_000, mode=32_000, high=34_000,
                                      tare=4000, empty=1)) == pytest.approx((1, 4000))


def test_invalid_shares_reject():
    p = profile()
    p["equipment"][0]["share"]["value"] = 0
    with pytest.raises(ValueError, match="shares"):
        expected_cargo_mix(p)


def test_slot_limited_export_has_bounded_ballast_and_mass_headroom():
    policy = ExportPolicy(372, 9_727_131, 2_900_000, 531_801, 0,
                          3_177_500, 1.6725, 16_013.87)
    count = policy.target_count
    assert count == 189
    audit = policy.audit(count * policy.mean_mass_kg, count * policy.mean_teu)
    assert .75 <= audit["teu_fraction"] <= .9
    assert .75 <= audit["cargo_mass_fraction"] <= .9
    assert audit["design_displacement_fraction"] == pytest.approx(.93)
    assert 0 < audit["ballast_reserve_kg"] <= policy.ballast_capacity_kg
    assert not policy.mass_limited
    assert not policy.is_export_count(12)
    assert policy.ballast_kg(0) == policy.ballast_capacity_kg


def test_dense_large_ship_cannot_reach_teu_minimum_without_exceeding_mass():
    policy = ExportPolicy(1604, 27_985_050, 8_074_000, 1_448_551, 0,
                          7_175_000, 1, 15_595.19)
    assert policy.mass_limited
    count = policy.target_count
    audit = policy.audit(count * policy.mean_mass_kg, count)
    assert audit["teu_fraction"] < .75
    assert .75 <= audit["cargo_mass_fraction"] <= .9
    assert .9 <= audit["design_displacement_fraction"] <= 1
    assert .75 * 1604 * policy.mean_mass_kg > policy.design_displacement_kg - policy.base_kg


@pytest.mark.parametrize("count", [1, 42])
def test_region_preserves_full_ship_mass_utilisation_screen(monkeypatch, count):
    from types import SimpleNamespace
    from stowage.generate import random_export
    policy = ExportPolicy(100, 10_000_000, 3_000_000, 500_000, 0,
                          3_000_000, 1, 50_000)
    parent = SimpleNamespace(cells=[None] * 100)
    region = SimpleNamespace(cells=[None] * 50)
    monkeypatch.setattr(random_export, "full_export_policy",
                        lambda vessel, params: policy if vessel is parent else None)
    light = [SimpleNamespace(length_ft=20, gross_mass_kg=1000)] * count
    assert "iscode.loading.displacement" in random_export.export_rejections(
        region, light, {}, full_vessel=parent)


def test_export_design_cache_uses_content_when_file_stat_is_unchanged(monkeypatch, tmp_path):
    import os
    from stowage.generate import random_export
    from stowage.params import params_dir
    from stowage.vessel.fleet_slices import load_fleet_preset
    vessel = load_fleet_preset("feeder_500", "full", with_hull=False)
    source = params_dir("fleet") / "feeder_500.toml"
    raw = source.read_bytes()
    target = tmp_path / source.name
    target.write_bytes(raw)
    original_stat = target.stat()
    monkeypatch.setattr("stowage.params.params_dir", lambda kind: tmp_path if kind == "fleet" else params_dir(kind))
    monkeypatch.setattr("stowage.vessel.fleet.list_fleet", lambda: ["feeder_500"])
    random_export._fleet_design.cache_clear()
    params = load_param_file("cargo", "feeder_export")
    before = random_export.full_export_policy(vessel, params)
    import re
    changed = re.sub(rb"(?m)^(lightship_mass_kg\s*=\s*)([0-9.eE+-]+)",
                     lambda match: match[1] + str(float(match[2]) + 1).encode(), raw, count=1)
    assert changed != raw and len(changed) == len(raw)
    target.write_bytes(changed)
    os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    after = random_export.full_export_policy(vessel, params)
    assert after.lightship_kg == before.lightship_kg + 1


def test_region_rounding_keeps_unchanged_minimum_teu_band(monkeypatch):
    from types import SimpleNamespace
    from stowage.generate import random_yard
    from stowage.vessel.fleet_slices import load_fleet_preset
    parent = load_fleet_preset("feeder_500", "full", with_hull=False)
    region = random_yard._tiny_region(parent)
    params = load_param_file("cargo", "feeder_export")
    params["equipment"] = [next(e for e in params["equipment"] if e["id"] == "dry_20")]
    policy = SimpleNamespace(capacity_teu=len(parent.cells), target_count=400,
                             mass_limited=False)
    monkeypatch.setattr(random_yard, "full_export_policy",
                        lambda vessel, params: policy if vessel is parent else None)
    count, _, _ = random_yard.capacity_load_size(region, random_yard.SIZE_LADDER["tiny"],
                                                params, full_vessel=parent)
    assert count == 5  # floor(400*6/516)=4 would violate the hard .75 band.


@pytest.fixture
def cli_shard(tmp_path, monkeypatch):
    """Exercise persistence with a valid fixture, independently of generation physics."""
    from pathlib import Path
    from stowage.generate import random_yard as random
    from stowage.io import load_instance
    from stowage.params import repo_root

    # Other executives may edit dependency files during this persistence test.
    # Pin their bytes while retaining explicit mutation tests below.
    read_bytes = Path.read_bytes
    source_root = Path(random.__file__).resolve().parents[1]
    source_bytes = {path: read_bytes(path) for path in source_root.rglob("*")
                    if path.is_file() and path.suffix in {".py", ".json", ".toml"}}
    monkeypatch.setattr(Path, "read_bytes", lambda path: source_bytes[path]
                        if path in source_bytes else read_bytes(path))
    calls = []

    def generate(size, *, seed, blocking_ratio, vessel_name, port_name, report):
        calls.append(seed)
        instance = load_instance(repo_root() / "data/fixtures/empty_ship.json")
        instance.family = {"vessel": vessel_name, "port": port_name, "size": size,
                           "blocking_ratio": repr(float(blocking_ratio)), "seed": str(seed),
                           "generator": random.RANDOM_YARD_VERSION}
        instance.seed = seed
        instance.provenance = random.current_provenance(seed=seed, created_by="generate.random_yard")
        instance.provenance.versions["random_yard"] = random.RANDOM_YARD_VERSION
        report.accepted, report.attempts = True, 1
        report.targets = len(instance.load_list)
        return instance

    monkeypatch.setattr(random, "generate_random_instance", generate)
    args = ["--size", "tiny", "--output", str(tmp_path)]
    assert random.main(args) == 0
    return random, args, tmp_path / "tiny/seed-00000000.json", calls


@pytest.mark.parametrize("kind", ["cargo", "fleet", "ports"])
def test_cli_resume_rejects_changed_parameter_content(cli_shard, monkeypatch, kind):
    random, args, _, calls = cli_shard
    actual = random.load_param_file

    def changed(selected, name):
        params = actual(selected, name)
        if selected == kind:
            params["assumption"] = "Changed source content without a version bump"
        return params

    monkeypatch.setattr(random, "load_param_file", changed)
    with pytest.raises(ValueError, match="resume .*mismatch"):
        random.main(args)
    assert calls == [0]


def test_cli_resume_rejects_changed_component_version(cli_shard, monkeypatch):
    from stowage import versions
    random, args, _, calls = cli_shard
    monkeypatch.setattr(versions, "CHECKER_VERSION", "new-checker")
    with pytest.raises(ValueError, match="resume .*mismatch"):
        random.main(args)
    assert calls == [0]


def test_cli_resume_rejects_unversioned_export_dependency_change(cli_shard, monkeypatch):
    from pathlib import Path
    from stowage.generate import random_export
    random, args, _, calls = cli_shard
    actual = Path.read_bytes
    changed = Path(random_export.__file__)
    monkeypatch.setattr(Path, "read_bytes", lambda path: actual(path) +
                        (b"\n# unversioned source change\n" if path == changed else b""))
    with pytest.raises(ValueError, match="resume .*mismatch"):
        random.main(args)
    assert calls == [0]


@pytest.mark.parametrize("corruption", ["empty", "unrelated", "invalid_json", "audit_value", "other_shard"])
def test_cli_resume_rejects_invalid_or_unbound_audit(cli_shard, corruption):
    import json
    random, args, path, calls = cli_shard
    sidecar = path.with_suffix(".audit.json")
    if corruption == "other_shard":
        random.main([*args, "--seed", "1"])
        sidecar.write_bytes(sidecar.with_name("seed-00000001.audit.json").read_bytes())
    elif corruption == "invalid_json":
        sidecar.write_text("{")
    elif corruption in {"empty", "unrelated"}:
        sidecar.write_text(json.dumps({} if corruption == "empty" else {"accepted": True}))
    else:
        payload = json.loads(sidecar.read_text())
        payload["attempts"] += 1
        sidecar.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="resume audit mismatch"):
        random.main(args)
    assert calls == ([0, 1] if corruption == "other_shard" else [0])


def test_cli_resume_verifies_shard_content_and_resumes_unchanged(cli_shard, capsys):
    import json
    random, args, path, calls = cli_shard
    capsys.readouterr()
    assert random.main(args) == 0
    assert json.loads(capsys.readouterr().out) == {"resumed": 1}
    assert calls == [0]
    payload = json.loads(path.read_text())
    payload["id"] += ".tampered"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="resume audit mismatch"):
        random.main(args)


def test_cli_regenerates_shard_with_missing_audit(cli_shard):
    random, args, path, calls = cli_shard
    path.with_suffix(".audit.json").unlink()
    assert random.main(args) == 0
    assert calls == [0, 0]
    assert path.with_suffix(".audit.json").is_file()


def test_cli_rejects_input_changes_during_generation(cli_shard, monkeypatch):
    from stowage import versions
    random, args, path, calls = cli_shard
    path.unlink()
    path.with_suffix(".audit.json").unlink()
    actual = random.generate_random_instance

    def changing(*args, **kwargs):
        instance = actual(*args, **kwargs)
        monkeypatch.setattr(versions, "CHECKER_VERSION", "changed-during-generation")
        return instance

    monkeypatch.setattr(random, "generate_random_instance", changing)
    with pytest.raises(ValueError, match="generation inputs changed while building"):
        random.main(args)
    assert not path.exists() and not path.with_suffix(".audit.json").exists()

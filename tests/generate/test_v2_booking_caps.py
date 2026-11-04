"""Fixed-count v2 campaigns fail before launching incompatible tiny jobs."""
import pytest

from stowage.generate import instances_v2, instances_v3


@pytest.mark.parametrize("generator", instances_v2.GENERATORS)
@pytest.mark.parametrize("profiles", [None, {"tiny"}, {"tiny", "small"}])
@pytest.mark.parametrize("caps", [dict(booking_slack=.25), dict(booking_mass_fraction=.55)])
def test_v2_rejects_tiny_cap_campaign_before_generation(generator, profiles, caps):
    with pytest.raises(ValueError, match=r"v2 tiny.*fixed n_boxes=6.*instances_v3"):
        instances_v2.scenario_specs(pilot=True, profiles=profiles,
                                    generators={generator}, **caps)


@pytest.mark.parametrize("generator", instances_v2.GENERATORS)
@pytest.mark.parametrize("flag", ["--booking-slack", "--booking-mass-fraction"])
def test_v2_cli_rejects_conflict_without_creating_output(generator, flag, tmp_path, capsys):
    output = tmp_path / "unstarted-campaign"
    with pytest.raises(SystemExit) as exc:
        instances_v2.main(["--pilot", "--generators", generator, flag, ".25",
                           "--output", str(output)])
    assert exc.value.code == 2
    assert "fixed n_boxes=6" in capsys.readouterr().err
    assert not output.exists()


def test_v2_default_and_non_tiny_specs_keep_existing_counts_and_arguments():
    default = instances_v2.scenario_specs(pilot=True)
    assert default == instances_v2.scenario_specs(
        pilot=True, booking_slack=None, booking_mass_fraction=None)
    assert all(spec["n_boxes"] == 6 for spec in default if spec["size"] == "tiny")
    uncapped = instances_v2.scenario_specs(pilot=True, profiles={"small"})
    capped = instances_v2.scenario_specs(pilot=True, profiles={"small"},
                                        booking_slack=.25, booking_mass_fraction=.55)
    assert uncapped == [{key: value for key, value in spec.items()
                         if key not in {"booking_slack", "booking_mass_fraction"}}
                        for spec in capped]


@pytest.mark.parametrize("generator", instances_v2.GENERATORS)
def test_v3_keeps_automatic_tiny_underbooking(generator):
    specs = instances_v3.scenario_specs(pilot=True, profiles={"tiny"},
                                       generators={generator}, booking_slack=.25,
                                       booking_mass_fraction=.55)
    assert all(spec["n_boxes"] is None for spec in specs)

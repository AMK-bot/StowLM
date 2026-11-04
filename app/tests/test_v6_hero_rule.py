"""Demo v6: the hero's rule is the selected hero wording's own reading (D268 as amended, D272), looked up in the lattice, with no fixture
file; a package built for another plan is withheld until its rebuild. No hero fact is typed here: every expectation is read from data."""
from __future__ import annotations

import json
import shutil

import pytest

from app import v6, v6_package
from app.v6_fixtures import Withheld


def test_the_hero_file_names_the_selection_not_a_fixture():
    assert v6.hero_config()["instructions"][v6.hero_config()["hero_slot"]] == v6.SELECTED


def test_the_rule_is_what_the_reader_read_and_its_lattice_entry_is_the_after_plan():
    from experiments.mvp.pipeline.lattice.store import entry_key
    pick, slot = v6.hero_wording(), v6.hero()
    read = json.loads(pick["typed"]["evidence"]["raw"])
    assert slot["instruction"]["sentence"] == pick["sentence"] and slot["instruction"]["rule"] == read
    assert slot["keys"]["after"] == entry_key(slot["instance_sha256"], [read])
    stored = v6._stored_row(slot["keys"]["after"])
    assert stored["status"] == "checked_plan" and slot["computed_utc"] == stored["computed_utc"] and slot["after_plan"]


def test_shot_five_shows_the_readers_reading_and_no_stand_in():
    data = v6.reading()
    assert data["sentence"] == v6.hero_wording()["sentence"] and data["reader"]["kind"] == "stowlm_reader" and data["stand_in_note"] is None


@pytest.mark.parametrize("typed", [None, {"binds": False, "evidence": {"raw": "{}"}}, {"binds": True, "evidence": {"raw": '{"refusal": {"reason": "x"}}'}}])
def test_no_exact_typed_reading_is_no_rule(monkeypatch, typed):
    monkeypatch.setattr(v6, "_build_wording", lambda radio, case: {"sentence": "s", "typed": typed})
    assert v6._selected_instruction(v6.hero_config(), {}) is None


@pytest.fixture
def hero_package(monkeypatch, tmp_path):
    copy = tmp_path / "package"
    shutil.copytree(v6_package.package_dir(), copy)
    hero = tmp_path / "hero.json"
    hero.write_text(json.dumps({**v6.hero_config(), "package_dir": str(copy)}))
    monkeypatch.setattr(v6_package, "HERO_FILE", hero)
    return copy


def test_a_package_for_another_plan_is_withheld(hero_package):
    receipt = json.loads((hero_package / "receipt.json").read_text())
    shown = v6.hero()["after_sha256"] or v6.hero()["before_sha256"]
    (hero_package / "receipt.json").write_text(json.dumps({**receipt, "plan_sha256": "0" * 64}))
    with pytest.raises(Withheld, match="another plan"):
        v6_package.require_hero_plan(hero_package)
    (hero_package / "receipt.json").write_text(json.dumps({**receipt, "plan_sha256": shown}))
    v6_package.require_hero_plan(hero_package)

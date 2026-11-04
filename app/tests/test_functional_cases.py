"""Fresh functional cases use synthetic cargo and never call a model."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from app import functional_cases as cases
from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.pipeline.intake import _documents, parser_baseline
from experiments.mvp.pipeline.intake.paper import grounded
from experiments.mvp.pipeline.intake.provenance import is_csv_document
from experiments.mvp.pipeline.intake.interpretation import proposal
from experiments.mvp.pipeline.intake.realistic import finish
from experiments.mvp.pipeline.intake.sheets import parse_sheet
from stowage.io import load_instance
from stowage.conventions import make_container_id
from stowage.model import Instance
from stowage.model.fixtures import build_instance


@pytest.fixture(autouse=True)
def check(monkeypatch, tmp_path):
    # Importing runner.envelope normally executes runner.__init__, whose core
    # changes the process-wide vessel-cache environment during collection.
    # Exercise the unchanged pure gate without importing the runner lifecycle.
    name = "experiments.mvp.pipeline.runner.envelope"
    source = cases.ROOT / "experiments/mvp/pipeline/runner/envelope.py"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(cases, "CACHE", tmp_path / "generation-cache")
    monkeypatch.setattr(cases, "_produce", lambda *args, **kwargs:
                        pytest.fail("Case tests must supply a synthetic parent, not run the producer"))
    return module.check


def test_collection_preserves_generation_environment():
    script = """
import os
import runpy
import sys
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = 'collection-cache-sentinel'
os.environ['STOWAGE_STABILITY_LIQUID_DIRECT'] = 'collection-physics-sentinel'
from app import check_cache
loaded_environment = check_cache._loaded[1]
before = {key: value for key, value in os.environ.items() if key.startswith('STOWAGE_')}
runpy.run_path(sys.argv[1])
after = {key: value for key, value in os.environ.items() if key.startswith('STOWAGE_')}
assert after == before, (before, after)
assert check_cache._environment() == loaded_environment
assert 'experiments.mvp.pipeline.runner' not in sys.modules
"""
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    result = subprocess.run([sys.executable, "-c", script, str(Path(__file__).resolve())],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def parent():
    payload = build_instance().model_dump(mode="json")
    for box in payload["containers"]:
        box.update(dg=None, oog=None, special_stowage=[], max_gross_mass_kg=34000)
    return Instance.model_validate(payload)


def request(**updates):
    value = dict(ship="feeder_500", size_class="tiny", fill_pct=60,
                 generator="random_yard", port="laem_chabang_like", seed=987654321)
    value.update(updates)
    return value


def test_options_are_generation_free_and_offer_the_contract(monkeypatch):
    monkeypatch.setattr(cases, "_produce", lambda *a, **k: pytest.fail("options generated cargo"))
    result = cases.options()
    assert {s["id"] for s in result["ships"]} == {"feeder_500", "feeder_800", "feeder_1700"}
    assert result["size_classes"] == ["tiny", "small", "medium"]
    assert set(result["generators"]) == {"random_yard", "simulated_yard"}
    assert "laem_chabang_like" in result["ports"]
    assert result["fill_pct"] == dict(min=10, max=100, envelope_max=70, default=60)
    assert result["envelope"]["max_boxes"] == 195
    assert result["envelope"]["max_fill"] == .70
    assert {"dev", "sealed", "ext-1", "test-v3"} <= result["published_seeds"].keys()
    assert all(result["published_seeds"][name] > 0 for name in ("dev", "sealed", "ext-1", "test-v3"))
    assert set(request()) - {"seed"} <= result["defaults"].keys()
    feeder = next(s for s in result["ships"] if s["id"] == "feeder_1700")
    assert feeder["in_envelope_sizes"] == []


@pytest.mark.parametrize("seed,cohort,sealed", [
    (306100000, "dev", False),
    (306110000, "sealed", True),
    (306320000, "ext-1", True),
    (187000000, "test-v3", True),
    (187100029, "test-v3", True),
    (0, "instances-v2", True),
    (19, "instances-v2/val", True),
    (10000003, "instances-v2/test", True),
    (8000000, "corpus", True),
    (20000000, "corpus", True),
    (10000000, "corpus", False),
    (29000000, "corpus", False),
    (9000100, "corpus", False),
])
def test_published_seed_status(seed, cohort, sealed):
    status = cases.seed_status(seed)
    assert status["seed"] == seed
    assert cohort in status["known_in"]
    assert status["unseen"] is False
    assert status["sealed"] is sealed
    prefix = "Refused: held-out seed of " if sealed else "Published seed: "
    assert status["label"].startswith(prefix)
    assert any(name in status["label"] for name in status["known_in"])


@pytest.mark.parametrize("seed,label", [
    (987654321, "Fresh seed: in no published map set"),
    (306100000, "Published seed: dev"),
    (306110000, "Refused: held-out seed of sealed"),
    (306320000, "Refused: held-out seed of ext-1"),
    (8000000, "Refused: held-out seed of corpus/test"),
    (20000000, "Refused: held-out seed of corpus/val"),
    (19, "Refused: held-out seed of instances-v2/val"),
    (10000003, "Refused: held-out seed of instances-v2/test"),
])
def test_seed_label_names_the_actual_refused_or_published_set(seed, label):
    assert cases.seed_status(seed)["label"] == label


@pytest.mark.parametrize("seed,train,held_out", [
    (0, "instances-v2/train", ["splits-v1/val", "splits-v1/test"]),
    (10000003, "corpus/train", ["instances-v2/test"]),
])
def test_any_held_out_membership_wins_over_a_training_membership(seed, train, held_out):
    status = cases.seed_status(seed)
    assert train in status["known_in"]
    assert all(name in status["known_in"] for name in held_out)
    assert status["sealed"] is True
    assert status["label"] == "Refused: held-out seed of " + ", ".join(held_out)
    assert "train" not in status["label"]


@pytest.mark.parametrize("cohort,end", [
    ("corpus/test", 8300000), ("corpus/test-v2", 28300000),
    ("corpus/val", 20300000), ("corpus/val-v2", 40300000),
    ("corpus/val-v2.1", 41200000), ("test-v3", 187200000),
])
def test_held_out_namespace_reservation_covers_unemitted_roots_but_has_a_boundary(cohort, end):
    reserved = cases.seed_status(end - 1)
    assert reserved["sealed"] is True and reserved["unseen"] is False
    assert cohort in reserved["known_in"]
    assert reserved["label"] == "Refused: held-out seed of " + cohort
    boundary = cases.seed_status(end)
    assert boundary["sealed"] is False and boundary["unseen"] is True


def test_new_seeds_are_unseen():
    for _ in range(20):
        seed = cases.new_seed()
        assert type(seed) is int
        assert seed >= 0
        assert cases.seed_status(seed) == dict(seed=seed, known_in=[], unseen=True, sealed=False,
                                               label="Fresh seed: in no published map set")


def test_new_seed_skips_every_published_cohort(monkeypatch):
    draws = iter([306100000, 306110000, 306320000, 187000000, 0, 8000000, 987654321])
    monkeypatch.setattr(cases.secrets, "randbelow", lambda _: next(draws))
    assert cases.new_seed() == 987654321


@pytest.mark.parametrize("changes", [
    {"seed": True}, {"seed": -1}, {"seed": 1.25}, {"seed": "123"},
    {"fill_pct": True}, {"fill_pct": 9}, {"fill_pct": 101},
    {"fill_pct": float("nan")}, {"fill_pct": float("inf")},
    {"ship": "cargo_999"}, {"size_class": "full"},
    {"generator": "recorded"}, {"port": "UNKNOWN"},
    {"seed": 306110000},
    {"seed": 187000000},
    {"seed": 20000000}, {"seed": 8000000}, {"seed": 306320000},
    {"seed": 0}, {"seed": 19}, {"seed": 10000003},
    {"seed": 8299999}, {"seed": 28299999}, {"seed": 20299999},
    {"seed": 40299999}, {"seed": 41199999}, {"seed": 187199999},
])
def test_invalid_requests_fail_before_the_producer(monkeypatch, tmp_path, changes):
    monkeypatch.setattr(cases, "_produce", lambda *a, **k: pytest.fail("bad request reached producer"))
    with pytest.raises(cases.CaseError):
        cases.generate_case(request(**changes), tmp_path / "call")


def test_nested_order_is_deterministic_and_preserves_boxes(parent):
    cargo = [b.model_dump(mode="json") for b in parent.containers]
    ordered = cases._ordered(cargo, 712345678)
    assert ordered == cases._ordered(cargo, 712345678)
    assert sorted(b["id"] for b in ordered) == sorted(b["id"] for b in cargo)
    assert cargo == [b.model_dump(mode="json") for b in parent.containers]


def test_selection_preserves_yard_and_respects_physical_fill(parent):
    selected = cases._select(parent, request(fill_pct=25))
    boxes = {b.id: b for b in selected.containers}
    physical = sum(1 if boxes[cid].length_ft == 20 else 2 for cid in selected.load_list)
    assert 0 < physical <= int(len(parent.vessel.cells) * .25)
    assert len(selected.load_list) <= 195
    assert set(selected.load_list) <= set(parent.load_list)
    assert selected.yard == parent.yard
    assert selected.vessel == parent.vessel
    assert not selected.onboard.placements
    assert not selected.discharge_list


def test_generated_papers_have_labels_and_exact_yard_identities(parent, tmp_path):
    papers = cases._write_papers(parent, tmp_path)
    assert {p["path"] for p in papers} >= {
        "papers/generated/load-list.csv", "papers/generated/B1-booking-note.txt"}
    assert all(p["label"] == "Synthetic document" for p in papers)
    csv_path = tmp_path / "papers/generated/load-list.csv"
    text = csv_path.read_text()
    assert "synthetic document" in text.splitlines()[0].lower()
    sheet = parse_sheet(dict(id="load-list", path=str(csv_path), mime_type="text/csv"))
    assert sheet["issues"] == []
    assert sheet["mapping"]["header_row"] == 2
    rows = list(csv.DictReader(io.StringIO("\n".join(text.splitlines()[1:]))))
    assert {r["id"] for r in rows} == set(parent.load_list)
    yard_ids = {cid for stack in parent.yard.stacks for cid in stack.boxes}
    assert {r["id"] for r in rows} <= yard_ids
    gross = {o["row_id"]: o["value"] for o in sheet["observations"] if o["field"] == "gross_mass_kg"}
    assert gross == {b.id: b.gross_mass_kg for b in parent.containers if b.id in parent.load_list}
    note = (tmp_path / next(p["path"] for p in papers if p["role"] == "booking")).read_text()
    assert "synthetic document" in note.splitlines()[0].lower()
    assert all(cid in note for cid in parent.load_list)


def test_positive_dg_papers_preserve_declarations(tmp_path):
    parent = build_instance()
    papers = cases._write_papers(parent, tmp_path)
    assert papers
    csv_text = (tmp_path / "papers/generated/load-list.csv").read_text()
    note = (tmp_path / next(p["path"] for p in papers if p["role"] == "booking")).read_text()
    dg_box = next(box for box in parent.containers if box.dg)
    assert "dg.un_number" in csv_text
    assert "dg.imdg_class" in csv_text
    assert "dg.packing_group" in csv_text
    assert dg_box.id in csv_text and dg_box.id in note
    assert dg_box.dg.un_number in csv_text
    assert dg_box.dg.un_number in note


def test_booking_note_dg_entries_ground_their_own_printed_facts(tmp_path):
    payload = build_instance().model_dump(mode="json")
    box = next(box for box in payload["containers"] if box["dg"])
    box["dg"]["additional"] = [dict(un_number="1006", imdg_class="2.2", packing_group=None,
                                      stowage_category="A", limited_quantity=True, marine_pollutant=True)]
    instance = Instance.model_validate(payload)
    papers = cases._write_papers(instance, tmp_path)
    text = (tmp_path / next(p["path"] for p in papers if p["role"] == "booking")).read_text()
    document = dict(id="booking", text=text)
    dg_box = next(b for b in instance.containers if b.dg)
    for prefix, entry in [("dg.", dg_box.dg), ("dg.additional.0.", dg_box.dg.additional[0])]:
        line = next(line for line in text.splitlines() if f"UN {entry.un_number};" in line)
        for field in ("un_number", "imdg_class", "packing_group", "limited_quantity", "marine_pollutant"):
            observation = dict(row_id=dg_box.id, field=prefix + field, value=getattr(entry, field),
                               document="booking", quote=line)
            assert grounded(observation, document), observation


@pytest.mark.parametrize("seed", [987654321, None, 306100000, 10000000])
def test_generate_writes_verifiable_case_without_a_model(parent, monkeypatch, tmp_path, seed, check):
    produced = []
    def fake_produce(req, selected_seed, private_dir):
        produced.append((req, selected_seed, Path(private_dir)))
        return parent
    monkeypatch.setattr(cases, "_produce", fake_produce)
    call_dir = tmp_path / "call"
    result = cases.generate_case(request(seed=seed), call_dir)
    assert len(produced) == 1
    assert produced[0][1] == result["seed"]
    assert result == json.loads((call_dir / "case.json").read_text())
    instance = load_instance(call_dir / "base-instance.json")
    assert result["schema"] == "stowlm.functional-case/1"
    assert result["seed_source"] == ("random" if seed is None else "planner")
    assert result["seed_status"] == cases.seed_status(result["seed"])
    assert result["instance_sha256"] == sha256_json(instance.model_dump(mode="json"))
    assert result["file_sha256"] == hashlib.sha256((call_dir / "base-instance.json").read_bytes()).hexdigest()
    assert result["instance_id"] == instance.id
    assert result["boxes"] == len(instance.load_list)
    assert result["envelope"] == check(instance)
    assert result["fill"] == result["envelope"]["numbers"]["physical_cell_fill"]
    assert result["wall_s"] >= 0
    assert result["label"] == "Synthetic ship and cargo, simulated port"
    assert result["generator"]["producer"] == "experiments/mvp/corpus_tools/build.py"
    for field in ("producer_sha256", "module_sha256"):
        assert len(result["generator"][field]) == 64
    assert all((call_dir / paper["path"]).is_file() for paper in result["papers"])
    reading = parser_baseline(
        [dict(id="generated-load-list", path=str(call_dir / "papers/generated/load-list.csv"), mime_type="text/csv")],
        base_instance=instance,
    )
    assert reading.status == "complete", [q.model_dump(mode="json") for q in reading.questions]
    assert {r["id"] for r in reading.rows} == set(instance.load_list)
    documents = _documents([dict(paper, path=str(call_dir / paper["path"]), mime_type=paper["media_type"])
                            for paper in result["papers"]])
    read_ids = []
    for scoped, booking_documents in _booking_batches(instance, documents):
        reading = finish(_grounded_generated_reading(scoped, booking_documents), booking_documents,
                         scoped.model_dump(mode="json"))
        assert reading.status == "complete", [q.model_dump(mode="json") for q in reading.questions]
        read_ids.extend(row["id"] for row in reading.rows)
    assert sorted(read_ids) == sorted(instance.load_list)
    assert not list(call_dir.rglob("*.partial"))


def test_integrity_hashes_are_not_evidence_of_different_cargo(parent, monkeypatch, tmp_path):
    monkeypatch.setattr(cases, "_produce", lambda *a: parent)
    first = cases.generate_case(request(seed=987654321, fill_pct=100), tmp_path / "first")
    second = cases.generate_case(request(seed=987654322, fill_pct=100), tmp_path / "second")
    first_instance = load_instance(tmp_path / "first/base-instance.json")
    second_instance = load_instance(tmp_path / "second/base-instance.json")
    assert first_instance.containers == second_instance.containers
    assert first_instance.yard == second_instance.yard
    assert first_instance.vessel == second_instance.vessel
    assert first_instance.load_list == second_instance.load_list
    assert first_instance.id != second_instance.id
    assert first_instance.provenance != second_instance.provenance
    assert first["instance_sha256"] != second["instance_sha256"]
    assert first["file_sha256"] != second["file_sha256"]
    assert first["seed_status"]["unseen"] and second["seed_status"]["unseen"]
    assert first["request"]["fill_pct"] == second["request"]["fill_pct"] == 100


@pytest.mark.parametrize("ship", ["feeder_500", "feeder_1700"])
@pytest.mark.parametrize("size_class", ["tiny", "small", "medium"])
def test_headline_envelope_keeps_runner_gate_and_ship_claim_distinct(parent, monkeypatch, tmp_path, ship, size_class, check):
    payload = parent.model_dump(mode="json")
    payload["size_class"] = size_class
    payload["vessel"]["slice_of"] = ship
    parent = Instance.model_validate(payload)
    monkeypatch.setattr(cases, "_produce", lambda *a: parent)
    result = cases.generate_case(request(ship=ship, size_class=size_class), tmp_path)
    instance = load_instance(tmp_path / "base-instance.json")
    assert result["envelope"] == check(instance)
    headline = result["headline_envelope"]
    assert all(reason in headline["reasons"] for reason in result["envelope"]["reasons"])
    if ship == "feeder_1700":
        assert headline["in_envelope"] is False
        assert "ship" in {reason["test"] for reason in headline["reasons"]}
        assert result["envelope"]["in_envelope"] is (size_class != "medium")
    else:
        assert headline["in_envelope"] is True
        assert result["envelope"]["in_envelope"] is True


def test_headline_envelope_preserves_runner_fill_failure(parent, monkeypatch, tmp_path):
    parent = _seventeen_box_instance(parent, False)
    monkeypatch.setattr(cases, "_produce", lambda *a: parent)
    result = cases.generate_case(request(fill_pct=100), tmp_path)
    runner = result["envelope"]
    headline = result["headline_envelope"]
    assert runner["in_envelope"] is False
    assert "fill" in {reason["test"] for reason in runner["reasons"]}
    assert headline["in_envelope"] is False
    assert all(reason in headline["reasons"] for reason in runner["reasons"])


def _generated_authority_documents(instance, call_dir):
    papers = cases._write_papers(instance, call_dir)
    manifest = json.loads((call_dir / "papers/generated/manifest.json").read_text())
    assert manifest == papers
    assert cases.BOOKING_MAX_BOXES == 6
    assert len(manifest) == 1 + 7 * ((len(instance.load_list) + 5) // 6)
    assert manifest[0]["role"] == "unspecified" and manifest[0]["booking_ref"] is None
    assert manifest[0]["path"] == "papers/generated/load-list.csv"
    assert {item["role"] for item in manifest} == {
        "unspecified", "booking", "shipping_instructions", "vgm", "dg_declaration",
        "gate_slip", "reefer_list", "oog_list"}
    refs = {item["booking_ref"] for item in manifest[1:]}
    assert None not in refs and len(refs) == (len(manifest) - 1) // 7
    assert len({item["id"] for item in manifest}) == len(manifest)
    assert len({item["path"] for item in manifest}) == len(manifest)
    roles = ["booking", "shipping_instructions", "vgm", "dg_declaration", "gate_slip", "reefer_list", "oog_list"]
    for index, offset in enumerate(range(1, len(manifest), 7), 1):
        block = manifest[offset:offset + 7]
        assert [item["role"] for item in block] == roles
        assert len({item["booking_ref"] for item in block}) == 1
        assert all(Path(item["path"]).name.startswith(f"B{index}-") for item in block)
    for item in manifest:
        assert item["synthetic"] is True
        assert item["label"] == "Synthetic document"
        path = Path(item["path"])
        assert not path.is_absolute() and ".." not in path.parts
        text = (call_dir / path).read_text()
        first_cell = next(csv.reader(io.StringIO(text)))[0] if path.suffix == ".csv" else text.splitlines()[0]
        assert first_cell.startswith("SYNTHETIC DOCUMENT")
        if item["role"] in {"dg_declaration", "reefer_list", "oog_list"}:
            assert item["complete_booking_list"] is True
        if item["role"] != "unspecified":
            assert path.suffix == ".txt" and item["media_type"] == "text/plain"
    return _documents([dict(item, path=str(call_dir / item["path"]), mime_type=item["media_type"])
                       for item in manifest])


def _booking_batches(instance, documents):
    """Match the worker's per-booking boundary without sharing the global sheet."""
    references = list(dict.fromkeys(doc["booking_ref"] for doc in documents if doc["booking_ref"] is not None))
    seen = set()
    for reference in references:
        docs = [doc for doc in documents if doc["booking_ref"] == reference]
        assert len(docs) == 7 and all(doc["role"] != "unspecified" for doc in docs)
        booking = next(doc for doc in docs if doc["role"] == "booking")
        ids = set(re.findall(r"\b[A-Z]{4}\d{7}\b", booking["text"]))
        assert 0 < len(ids) <= 6
        assert not ids & seen
        seen.update(ids)
        assert ids <= set(instance.load_list)
        for doc in docs:
            assert set(re.findall(r"\b[A-Z]{4}\d{7}\b", doc["text"])) <= ids
        payload = instance.model_dump(mode="json")
        payload["load_list"] = [cid for cid in instance.load_list if cid in ids]
        yield Instance.model_validate(payload), docs
    assert seen == set(instance.load_list)


def _grounded_generated_reading(instance, documents):
    """Fake only Spark's interpretation; source bytes and authority remain real."""
    assert len(documents) == 7 and all(doc["role"] != "unspecified" for doc in documents)
    assert len({doc["booking_ref"] for doc in documents}) == 1
    raw = dict(schema_version="stowlm.intake-interpretation/7", header_maps=[], observations=[], exceptions=[],
               metadata=dict(policy_version=8, mode="generated", model=None, cost_usd=0))
    docs = {document["role"]: document for document in documents}
    assert all(document["media_type"] == "text/plain" and document["path"].endswith(".txt")
               for document in documents)
    assert all(not is_csv_document(document) for document in documents)

    def line_for(role, cid):
        return next(line for line in docs[role]["text"].splitlines() if line.startswith(cid + ";"))

    def emit(role, cid, field, value, quote=None):
        quote = quote or line_for(role, cid)
        observation = dict(row_id=cid, field=field, value=value, document=docs[role]["id"],
                           quote=quote, uncertain=False)
        if field in {"gross_mass_kg", "tare_kg", "max_gross_mass_kg"}:
            observation["unit"] = "kg"
        elif field == "reefer_setpoint_c":
            observation["unit"] = "C"
        assert grounded(observation, docs[role]), observation
        raw["observations"].append(observation)

    for box in instance.containers:
        if box.id not in instance.load_list:
            continue
        vessel = instance.rule_overrides.get("intake.policy", {}).get("vessel", instance.vessel.id.upper())
        for field, value in [("pol", box.pol), ("pod", box.pod), ("booked_vessel", vessel), ("booking_status", "booked")]:
            emit("booking", box.id, field, value)
        for field in ("iso_type", "pol", "empty", "reefer", "tare_kg", "max_gross_mass_kg"):
            if getattr(box, field) is not None:
                emit("shipping_instructions", box.id, field, getattr(box, field))
        si_line = line_for("shipping_instructions", box.id)
        commodity = si_line.split("; COMMODITY ", 1)[1].split(";", 1)[0]
        emit("shipping_instructions", box.id, "commodity", commodity)
        if box.reefer_setpoint_c is not None:
            emit("shipping_instructions", box.id, "reefer_setpoint_c", box.reefer_setpoint_c)
        for field in ("gross_mass_kg", "tare_kg", "max_gross_mass_kg"):
            if getattr(box, field) is not None:
                emit("vgm", box.id, field, getattr(box, field))
        gate_line = line_for("gate_slip", box.id)
        emit("gate_slip", box.id, "id", box.id)
        emit("gate_slip", box.id, "iso_type", box.iso_type)
        emit("gate_slip", box.id, "seal", gate_line.split("; Seal ", 1)[1].split(";", 1)[0])
        if box.reefer:
            emit("reefer_list", box.id, "reefer", True)
            if box.reefer_setpoint_c is not None:
                emit("reefer_list", box.id, "reefer_setpoint_c", box.reefer_setpoint_c)
        if box.special_stowage:
            emit("booking", box.id, "special_stowage", [str(value) for value in box.special_stowage])
        if box.dg is None:
            continue
        for prefix, entry in [("dg.", box.dg), *[(f"dg.additional.{i}.", value)
                                                for i, value in enumerate(box.dg.additional)]]:
            quote = next(line for line in docs["dg_declaration"]["text"].splitlines()
                         if box.id in line and f"UN {entry.un_number};" in line)
            for field in ("un_number", "imdg_class", "packing_group", "limited_quantity", "marine_pollutant"):
                emit("dg_declaration", box.id, prefix + field, getattr(entry, field), quote)
    return proposal(raw, documents, recorded=True)


@pytest.mark.parametrize("cargo_mode", ["dry-and-reefer", "dg-reefer-oog", "dg-null-pg", "all-lists-empty"])
def test_generated_role_manifest_passes_real_authority_intake(parent, tmp_path, cargo_mode):
    payload = (build_instance() if cargo_mode == "dg-reefer-oog" else parent).model_dump(mode="json")
    if cargo_mode == "dg-null-pg":
        payload["containers"][1]["dg"] = dict(un_number="1006", imdg_class="2.2", packing_group=None,
                                                 stowage_category="A", limited_quantity=False, marine_pollutant=False)
    payload["rule_overrides"]["intake.policy"] = dict(vessel=payload["vessel"]["id"].upper(), document_authority=True)
    for box in payload["containers"]:
        box.update(max_gross_mass_kg=34000, special_stowage=[])
        if cargo_mode == "all-lists-empty":
            box.update(reefer=False, reefer_setpoint_c=None)
    instance = Instance.model_validate(payload)
    all_documents = _generated_authority_documents(instance, tmp_path)
    [(instance, documents)] = list(_booking_batches(instance, all_documents))
    raw = _grounded_generated_reading(instance, documents)
    assert raw["header_maps"] == []
    result = finish(raw, documents, instance.model_dump(mode="json"))
    questions = [q.model_dump(mode="json") for q in result.questions]
    scope_reasons = {"missing_document", "document_scope_mismatch", "incomplete_document_scope",
                     "list_membership_conflict", "unverified_booking_scope", "unverified_closed_world"}
    assert not [q for q in questions if q["reason"] in scope_reasons], questions
    assert not [q for q in questions if q["reason"] in {
        "unmapped_sheet_headers", "unbound_column_map", "model_sheet_value", "invalid_csv"}], questions
    for observation in raw["observations"]:
        assert any(source.get("accepted") and all(source.get(key) == observation[key]
                   for key in ("row_id", "field", "document", "value"))
                   for source in result.field_sources), observation
    assert {row["id"] for row in result.rows} == set(instance.load_list)
    assert all(any(source.get("row_id") == cid and source["field"] == "seal" and source.get("accepted")
                   for source in result.field_sources) for cid in instance.load_list)
    assert all(any(source.get("row_id") == cid and source["field"] == "commodity" and source.get("accepted")
                   for source in result.field_sources) for cid in instance.load_list)
    if cargo_mode == "all-lists-empty":
        assert all(row["reefer"] is False and row.get("reefer_setpoint_c") is None for row in result.rows)
    else:
        reef = next(box for box in instance.containers if box.reefer)
        row = next(row for row in result.rows if row["id"] == reef.id)
        assert row["reefer"] is True and row["reefer_setpoint_c"] == reef.reefer_setpoint_c
    if cargo_mode != "dg-reefer-oog":
        assert result.status == "complete", questions
        assert questions == []
        assert all(row["oog"] is None for row in result.rows)
        if cargo_mode in {"dry-and-reefer", "all-lists-empty"}:
            assert all(row["dg"] is None for row in result.rows)
        else:
            dg_row = next(row for row in result.rows if row["dg"] is not None)
            assert dg_row["dg"]["un_number"] == "1006"
            assert dg_row["dg"]["packing_group"] is None
    else:
        # Frozen intake cannot authenticate a structured OOG dict from text.
        # The generated declaration remains visible and needs a planner answer.
        oog = next(box for box in instance.containers if box.oog)
        assert result.status == "needs_answers"
        assert len(questions) == 1, questions
        assert {q["reason"] for q in questions} == {"missing_oog_declaration"}, questions
        assert all(q["row"] == instance.load_list.index(oog.id) for q in questions)
        dg_box = next(box for box in instance.containers if box.dg)
        dg_row = next(row for row in result.rows if row["id"] == dg_box.id)
        assert dg_row["dg"]["un_number"] == dg_box.dg.un_number
        assert dg_row["dg"]["imdg_class"] == dg_box.dg.imdg_class
        assert not any(source.get("row_id") == oog.id and source["field"] == "oog" and source.get("accepted")
                       for source in result.field_sources)


def _seventeen_box_instance(parent, positive_lists):
    payload = parent.model_dump(mode="json")
    template = next(box for box in payload["containers"] if box["length_ft"] == 20)
    containers = [dict(template, id=make_container_id("QZXU", 100 + i), pod="THBKK",
                       dg=None, oog=None, special_stowage=[], reefer=False, reefer_setpoint_c=None)
                  for i in range(17)]
    if positive_lists:
        containers[8]["dg"] = next(box.dg.model_dump(mode="json") for box in build_instance().containers if box.dg)
        containers[16].update(iso_type="22R1", reefer=True, reefer_setpoint_c=-18, tare_kg=3000)
    payload.update(id="fixture-seventeen-booking-cargo", containers=containers,
                   load_list=[box["id"] for box in containers])
    payload["yard"]["stacks"] = [dict(key=f"A-{1 + start // 3:03d}-01", length_ft=20,
                                        boxes=[box["id"] for box in containers[start:start + 3]])
                                     for start in range(0, 17, 3)]
    payload["rule_overrides"]["intake.policy"] = dict(vessel=payload["vessel"]["id"].upper(), document_authority=True)
    return Instance.model_validate(payload)


@pytest.mark.parametrize("positive_lists", [False, True], ids=["all-dry", "dg-and-reefer-in-separate-bookings"])
def test_large_load_list_has_disjoint_bounded_bookings_and_complete_intake(parent, tmp_path, positive_lists):
    instance = _seventeen_box_instance(parent, positive_lists)
    docs = _generated_authority_documents(instance, tmp_path)
    batches = list(_booking_batches(instance, docs))
    assert [len(scoped.load_list) for scoped, _ in batches] == [6, 6, 5]
    assert len(docs) == 22
    read_ids = []
    for scoped, booking_docs in batches:
        scoped_boxes = [box for box in scoped.containers if box.id in scoped.load_list]
        for role, condition in [("dg_declaration", lambda box: box.dg is not None),
                                ("reefer_list", lambda box: box.reefer),
                                ("oog_list", lambda box: box.oog is not None)]:
            doc = next(doc for doc in booking_docs if doc["role"] == role)
            printed_ids = set(re.findall(r"\b[A-Z]{4}\d{7}\b", doc["text"]))
            assert printed_ids == {box.id for box in scoped_boxes if condition(box)}
        raw = _grounded_generated_reading(scoped, booking_docs)
        result = finish(raw, booking_docs, scoped.model_dump(mode="json"))
        assert result.status == "complete", [q.model_dump(mode="json") for q in result.questions]
        assert not result.questions
        assert {row["id"] for row in result.rows} == set(scoped.load_list)
        read_ids.extend(row["id"] for row in result.rows)
    assert sorted(read_ids) == sorted(instance.load_list)
    assert len(read_ids) == len(set(read_ids)) == 17


@pytest.mark.parametrize("original,expected", [(None, -18.0), (4.0, 4.0)], ids=["synthetic-default", "existing-declaration"])
def test_operating_reefer_setpoint_is_a_base_fact_shared_by_every_generated_paper(parent, monkeypatch, tmp_path, original, expected):
    payload = parent.model_dump(mode="json")
    reefer = next(box for box in payload["containers"] if box["reefer"])
    reefer["reefer_setpoint_c"] = original
    source = Instance.model_validate(payload)
    monkeypatch.setattr(cases, "_produce", lambda *a: source)
    case = cases.generate_case(request(fill_pct=100), tmp_path)
    instance = load_instance(tmp_path / "base-instance.json")
    generated = next(box for box in instance.containers if box.id == reefer["id"])
    assert generated.reefer_setpoint_c == expected
    if original is None:
        assert json.loads(instance.family["functional_reefer_setpoint_defaults"]) == {reefer["id"]: expected}
    else:
        assert "functional_reefer_setpoint_defaults" not in instance.family
    assert next(box for box in source.containers if box.id == reefer["id"]).reefer_setpoint_c == original
    manifest = json.loads((tmp_path / "papers/generated/manifest.json").read_text())
    assert manifest == case["papers"]
    documents = _documents([dict(item, path=str(tmp_path / item["path"]), mime_type=item["media_type"])
                            for item in manifest])
    [(scoped, booking_docs)] = list(_booking_batches(instance, documents))
    raw = _grounded_generated_reading(scoped, booking_docs)
    assert raw["header_maps"] == []
    result = finish(raw, booking_docs, scoped.model_dump(mode="json"))
    assert result.status == "complete", [q.model_dump(mode="json") for q in result.questions]
    row = next(row for row in result.rows if row["id"] == reefer["id"])
    assert row["reefer_setpoint_c"] == expected
    for role in ("shipping_instructions", "reefer_list"):
        doc = next(doc for doc in booking_docs if doc["role"] == role)
        assert any(source.get("accepted") and source["document"] == doc["id"]
                   and source["field"] == "reefer_setpoint_c" and source["value"] == expected
                   for source in result.field_sources)
    load_list = parse_sheet(dict(id="global-load-list", path=str(tmp_path / "papers/generated/load-list.csv"), mime_type="text/csv"))
    assert not load_list["issues"]
    assert any(item["row_id"] == reefer["id"] and item["field"] == "reefer_setpoint_c" and item["value"] == expected
               for item in load_list["observations"])

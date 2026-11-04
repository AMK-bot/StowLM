"""Read-only contracts for the completed follow-up-2 artifacts.

No production module imports, geometry construction or stability solves. Only
JSON/CSV/hash inspection and scalar arithmetic. Created for serial parent
verification after all20 shards finish; the author did not run this file.
Hash maps must agree within each shard, not across historical driver epochs.
"""
import csv
import hashlib
import json
import math
from pathlib import Path
import re

import pytest


HERE=Path(__file__).resolve().parent
OUT=HERE/"followup2"
COEFFICIENTS={
    "area_0_30_mrad":.009,
    "area_0_40_mrad":.016,
    "area_30_40_mrad":.006,
    "gz_at_or_above_30_m":.033,
    "gz_max_m":.042,
    "area_0_flood_mrad":.029,
}
FACTORS={(False,False),(True,False),(False,True),(True,True)}
SHARD_PATTERN=re.compile(r"(feeder_\d+)-(C1|C2|D58)-(departure|mid_voyage|arrival)-v(4p5|7p5|native)\.json")


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def factors(record):
    return {(row["coaming_credit"],row["cover_credit"]):row for row in record["rows"]}


def assert_stable_within_record(record):
    before,after=record["source_hashes_before"],record["source_hashes_after"]
    assert before and before==after
    assert all(isinstance(p,str) and re.fullmatch(r"[0-9a-f]{64}",h)
               for p,h in before.items())


@pytest.fixture(scope="module")
def shards():
    paths=sorted(OUT.glob("feeder_*-*-v*.json"))
    assert len(paths)==20,"Run after all20 follow-up2 shards are present"
    loaded=[]
    for path in paths:
        match=SHARD_PATTERN.fullmatch(path.name)
        assert match is not None,path.name
        loaded.append((path,read(path),match.groups()))
    return loaded


def test_complete_state_matrix_and_eighty_factor_rows(shards):
    expected=set()
    five_hundred=(("C1","arrival"),("C2","departure"),("C2","arrival"),
                  ("D58","departure"),("D58","mid_voyage"),("D58","arrival"))
    for label,phase in five_hundred:
        for vent in ("4p5","7p5"):
            expected.add(("feeder_500",label,phase,vent))
    for ship in ("feeder_800","feeder_1700"):
        for label,phase in (("C1","arrival"),("D58","departure"),
                            ("D58","mid_voyage"),("D58","arrival")):
            expected.add((ship,label,phase,"native"))
    actual={identity for _,_,identity in shards}
    assert actual==expected
    assert sum(len(record["rows"]) for _,record,_ in shards)==80


def test_each_shard_hash_binding_factors_and_physical_dprime(shards):
    for path,record,(ship,label,phase,vent) in shards:
        assert (record["ship"],record["label"],record["phase"])==(ship,label,phase)
        assert_stable_within_record(record)
        assert record["input_sha256"]==digest(OUT/f"{ship}-{label}-input.json")
        assert record["params_sha256"]==digest(OUT/f"{ship}-params.json")
        physical=read(OUT/f"{ship}-params.json")["envelope"]["hatch_coamings"]
        assert record["physical_coamings_for_Dprime"]==physical
        assert len(record["rows"])==4 and set(factors(record))==FACTORS
        baseline=factors(record)[(True,False)]
        dp=baseline["metrics"]["containership_depth_prime_m"]
        for row in record["rows"]:
            assert type(row["coaming_credit"]) is bool and type(row["cover_credit"]) is bool
            assert row["phase"]==phase
            assert row["metrics"]["containership_depth_prime_m"]==pytest.approx(dp,rel=0,abs=1e-12)
            assert row["openings"]==baseline["openings"]
            for key in ("mass_kg","lcg_m","tcg_m","kg_m","fsc_m"):
                assert row[key]==pytest.approx(baseline[key],rel=0,abs=1e-10)
            expected_delta=row["metrics"]["area_0_flood_mrad"]-baseline["metrics"]["area_0_flood_mrad"]
            assert row["delta_total_area_mrad"]==pytest.approx(expected_delta,rel=0,abs=1e-12)


def test_all_six_ratios_and_binding_term_use_code_coefficients(shards):
    for path,record,_ in shards:
        for row in record["rows"]:
            metrics=row["metrics"];C=metrics["containership_form_factor"]
            assert math.isfinite(C) and C>0
            assert set(row["all_b_ratios"])==set(COEFFICIENTS)
            independently_computed={}
            for metric,coefficient in COEFFICIENTS.items():
                expected=metrics[metric]*C/coefficient
                assert math.isfinite(expected)
                assert row["all_b_ratios"][metric]==pytest.approx(expected,rel=1e-12,abs=1e-12)
                independently_computed[metric]=expected
                if metric in row["required_b_areas_mrad"]:
                    assert row["required_b_areas_mrad"][metric]==pytest.approx(coefficient/C,rel=1e-12,abs=1e-12)
            minimum=min(independently_computed.values())
            assert row["min_b_ratio"]==pytest.approx(minimum,rel=1e-12,abs=1e-12)
            assert row["binding_b_term"] in COEFFICIENTS
            assert independently_computed[row["binding_b_term"]]==pytest.approx(minimum,rel=1e-12,abs=1e-12)


def test_500_vent_pair_changes_only_hold_apertures_by_exact_three_metres(shards):
    pairs={identity:record for _,record,identity in shards if identity[0]=="feeder_500"}
    old_keys=[identity for identity in pairs if identity[-1]=="4p5"]
    assert len(old_keys)==6
    for old_key in old_keys:
        old=pairs[old_key];new=pairs[(*old_key[:-1],"7p5")]
        assert old["physical_coamings_for_Dprime"]==new["physical_coamings_for_Dprime"]
        for factor,old_row in factors(old).items():
            new_row=factors(new)[factor]
            assert old_row["vent_height_above_deck_m"]==4.5
            assert new_row["vent_height_above_deck_m"]==7.5
            assert new_row["metrics"]["containership_depth_prime_m"]==pytest.approx(
                old_row["metrics"]["containership_depth_prime_m"],rel=0,abs=1e-12)
            for key in ("mass_kg","lcg_m","tcg_m","kg_m","fsc_m"):
                assert new_row[key]==pytest.approx(old_row[key],rel=0,abs=1e-10)
            old_open={o["id"]:o for o in old_row["openings"]}
            new_open={o["id"]:o for o in new_row["openings"]}
            assert old_open.keys()==new_open.keys()
            assert any(o["kind"]=="hold_ventilator" for o in old_open.values())
            assert any(o["kind"]!="hold_ventilator" for o in old_open.values())
            for identifier,a in old_open.items():
                b=new_open[identifier]
                if a["kind"]=="hold_ventilator":
                    assert b["z_m"]-a["z_m"]==pytest.approx(3.,rel=0,abs=1e-12)
                    assert {k:v for k,v in b.items() if k!="z_m"}=={k:v for k,v in a.items() if k!="z_m"}
                else:
                    assert b==a


def test_every_available_public_baseline_matches_all_shared_criteria_metrics(shards):
    public_count=0
    for path,record,_ in shards:
        report=record.get("public_checker")
        if report is None:continue
        public_count+=1
        assert report["ran"] and not report["error"]
        baseline=factors(record)[(True,False)]
        assert baseline["metrics"].keys()<=report["metrics"].keys()
        for metric,value in baseline["metrics"].items():
            # Two independent bounded peak refinements each use xatol=1e-8
            # rad. Their angle difference has a 2*xatol comparison allowance;
            # area/lever tolerances and production rule limits are unchanged.
            allowance=2e-8 if metric=="angle_gz_max_rad" else 1e-8
            assert report["metrics"][metric]==pytest.approx(value,rel=0,abs=allowance),(path.name,metric)
        # Public-only instrumentation/counter fields are not criteria outputs;
        # compare the additional physical values that have stored counterparts.
        for public_key,row_key in (("displacement_kg","mass_kg"),("kg_m","kg_m"),
                                   ("fsc_m","fsc_m"),("gm_m","gm_m"),
                                   ("draught_aft_m","draft_aft_m"),("draught_fwd_m","draft_fwd_m")):
            assert report["metrics"][public_key]==pytest.approx(baseline[row_key],rel=0,abs=1e-8)
        assert report["metrics"]["angle_flooding_rad"]==pytest.approx(
            math.radians(baseline["flooding_deg"]),rel=0,abs=1e-8)
    assert public_count>0,"Public-baseline comparison must not be vacuous"


def test_peer_loading_clip_and_full_graph_supported_range():
    peer=read(OUT/"peer17p2.json")
    assert_stable_within_record(peer)
    assert peer["loading"]["volume_m3"]==54148.
    assert peer["loading"]["kg_m"]==13.674
    assert peer["loading"]["source_depth_m"]==17.2
    assert peer["loading"]["fixed_trim_m"]==0.
    assert peer["section_diagnostics"]
    assert all(d["upper_top_m"]<=17.2+1e-8 for d in peer["section_diagnostics"])
    expected_angles=[float(v) for v in range(0,51,5)]
    assert [r["heel_deg"] for r in peer["rows"]]==expected_angles
    source={float(r["heel_deg"]):r for r in csv.DictReader((HERE/"marlantes-fig22-samples.csv").open())
            if r["abs_gz_m"] and r["csi_gz_m"]}
    assert sorted(source)==expected_angles
    for row in peer["rows"]:
        computed=row["computed"];angle=row["heel_deg"]
        assert computed["heel_deg"]==angle
        assert computed["volume_m3"]==pytest.approx(54148.,rel=0,abs=1e-4)
        assert abs(computed["volume_residual_m3"])<1e-4
        assert computed["gz_m"]==pytest.approx(
            computed["kn_m"]-13.674*math.sin(math.radians(angle)),rel=0,abs=1e-10)
        for author in ("abs","csi"):
            ref=float(source[angle][f"{author}_gz_m"])
            error=computed["gz_m"]-ref;bar=max(.05,.05*abs(ref))
            assert row[author]["reference_gz_m"]==pytest.approx(ref,rel=0,abs=1e-12)
            assert row[author]["error_gz_m"]==pytest.approx(error,rel=0,abs=1e-12)
            assert row[author]["l5_style_bar_m"]==pytest.approx(bar,rel=0,abs=1e-12)
            assert row[author]["within_l5_style_bar"] is (abs(error)<=bar)
    for author in ("abs","csi"):
        summary=peer["summary"][author]
        assert summary["points"]==11
        assert summary["passed"]==sum(abs(r[author]["error_gz_m"])<=r[author]["l5_style_bar_m"] for r in peer["rows"])
        assert summary["passed20to50"]==sum(abs(r[author]["error_gz_m"])<=r[author]["l5_style_bar_m"]
                                               for r in peer["rows"] if 20<=r["heel_deg"]<=50)


def test_peer_free_trim_uses_same_reference_support_and_errors():
    primary=read(OUT/"peer17p2.json")
    free=read(OUT/"peer17p2-free-trim.json")
    assert_stable_within_record(free)
    assert free["lcg_m"]==pytest.approx(primary["upright_at_source_volume"]["lcb_m"],rel=0,abs=1e-12)
    assert [r["heel_deg"] for r in free["rows"]]==[float(v) for v in range(0,51,5)]
    for row,ref in zip(free["rows"],primary["rows"]):
        assert abs(row["volume_residual_m3"])<1e-4
        assert row["solver_scaled_residual"]<2e-6
        for author in ("abs","csi"):
            error=row["gz_m"]-ref[author]["reference_gz_m"]
            assert row[author]["error_m"]==pytest.approx(error,rel=0,abs=1e-12)
            assert row[author]["within_l5_style_bar"] is (abs(error)<=ref[author]["l5_style_bar_m"])

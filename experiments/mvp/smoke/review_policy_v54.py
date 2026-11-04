"""Review exact canonical metadata projection; never requalify linear evidence.

Produces a candidate only. A separate --promote step verifies unchanged source
bytes/provider identities before atomically installing the reviewed metadata.
No policy limits or numerical banks are modified.

ASSUMPTION: --reuse-current-public-cache trusts the full public builder's
content-addressed vessel artifact for its five values. Its complete source,
parameter and library identity is independently pinned before/after review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import time
import tomllib

from stowage.checker import stability, stability_policy
from stowage.vessel import fleet, fleet_cache

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
TARGET = ROOT / "src/stowage/checker/stability_policy_contract.json"
CANDIDATE = OUT / "v54-policy-contract-candidate.json"
REVIEW = OUT / "v54-policy-review.json"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def bits(values):
    return [struct.pack(">d", value).hex() for value in values]


def providers():
    return {name: stability_policy.provider_record(function)
            for name, function in stability_policy.runtime_providers().items()}


def source_hashes():
    return {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in fleet_cache._source_files()}


def review():
    original = TARGET.read_bytes()
    contract = json.loads(original)
    sources = source_hashes()
    auxiliary = {path: digest((ROOT / path).read_bytes()) for path in contract["auxiliary_sha256"]}
    params = {name: fleet._parameter_bytes(name) for name in fleet.list_fleet()}
    current_providers = providers()
    rows = []
    for name, raw in params.items():
        began = time.monotonic()
        public = fleet.load_vessel(name)
        expected = tuple(stability._hull_policy_number(public.hull, field)
                         for field in stability._CANONICAL_POLICY_FIELDS)
        projected = stability_policy._project_reviewed_prefix(tomllib.loads(raw.decode()))
        assert len(expected) == len(projected) == 5
        assert bits(expected) == bits(projected), (name, expected, projected)
        key = fleet_cache._cache_key(name, "full", True, raw)
        rows.append(dict(ship=name, uncached_public_builder=os.environ.get("STOWAGE_VESSEL_CACHE") == "0", bitwise_equal=True,
                         fields=dict(zip(stability._CANONICAL_POLICY_FIELDS, expected, strict=True)),
                         float64_hex=bits(expected), elapsed_s=time.monotonic() - began))
        (OUT / "v54-policy-review-progress.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(json.dumps(rows[-1]), flush=True)
        contract["constructions"][name] = dict(identity=key, parameter_sha256=digest(raw))
    assert source_hashes() == sources, "construction source bytes changed during review"
    assert all(digest((ROOT / path).read_bytes()) == value for path, value in auxiliary.items())
    assert all(fleet._parameter_bytes(name) == raw for name, raw in params.items())
    assert providers() == current_providers
    assert TARGET.read_bytes() == original
    contract.update(source_sha256=sources, auxiliary_sha256=auxiliary, providers=current_providers)
    contract["verification"] = str(REVIEW.relative_to(ROOT))
    payload = (json.dumps(contract, indent=2, sort_keys=True) + "\n").encode()
    CANDIDATE.write_bytes(payload)
    # APPROXIMATION: the opt-in cache shortcut trusts the current content-keyed
    # full-public reference artifact; it does not claim fresh uncached tables.
    reference_assumption = ("Content-keyed current full-public vessel cache is trusted; no fresh uncached tables claimed."
                            if os.environ.get("STOWAGE_VESSEL_CACHE") != "0" else None)
    REVIEW.write_text(json.dumps(dict(old_contract_sha256=digest(original), candidate_sha256=digest(payload),
        assumption=reference_assumption,
        source_sha256=sources, auxiliary_sha256=auxiliary, providers=current_providers,
        parameter_sha256={name: digest(raw) for name, raw in params.items()}, rows=rows,
        changed_sources=sorted(path for path, value in sources.items() if json.loads(original)["source_sha256"].get(path) != value),
        note="Exact metadata projection only; no linear qualification, policy values, thresholds or measured banks changed."),
        indent=2) + "\n")
    print(json.dumps(rows))


def promote():
    record = json.loads(REVIEW.read_text())
    payload = CANDIDATE.read_bytes()
    assert digest(payload) == record["candidate_sha256"]
    assert digest(TARGET.read_bytes()) == record["old_contract_sha256"]
    assert source_hashes() == record["source_sha256"]
    assert providers() == record["providers"]
    assert all(digest((ROOT / path).read_bytes()) == value for path, value in record["auxiliary_sha256"].items())
    assert all(digest(fleet._parameter_bytes(name)) == value for name, value in record["parameter_sha256"].items())
    candidate_identity = (payload, stability_policy.provider_identity()[1])
    expected_rows = {row["ship"]: row for row in record["rows"]}
    for name in fleet.list_fleet():
        raw = fleet._parameter_bytes(name)
        projected = stability_policy.projected_policy(name, digest(raw),
            fleet_cache._cache_key(name, "full", True, raw), candidate_identity)
        assert projected is not None
        assert bits(projected) == expected_rows[name]["float64_hex"]
    with tempfile.NamedTemporaryFile(dir=TARGET.parent, prefix=".policy-v54-", delete=False) as stream:
        stream.write(payload)
        temporary = Path(stream.name)
    os.replace(temporary, TARGET)
    for name in fleet.list_fleet():
        raw = fleet._parameter_bytes(name)
        assert stability_policy.projected_policy(name, digest(raw),
            fleet_cache._cache_key(name, "full", True, raw), stability_policy.provider_identity()) is not None
    (OUT / "v54-policy-promotion.json").write_text(json.dumps(dict(
        promoted_epoch=time.time(), old_contract_sha256=record["old_contract_sha256"],
        new_contract_sha256=digest(TARGET.read_bytes()), candidate_dispatch_bitwise_equal=True,
        fields_changed=False, linear_qualification_changed=False, assumption=record.get("assumption")), indent=2) + "\n")
    print("Promoted exact canonical metadata; projection available for all three ships.")


def annotate_verification():
    original = TARGET.read_bytes()
    before = json.loads(original)
    after = dict(before, verification=str(REVIEW.relative_to(ROOT)))
    assert {key for key in after if before.get(key) != after[key]} == {"verification"}
    payload = (json.dumps(after, indent=2, sort_keys=True) + "\n").encode()
    assert TARGET.read_bytes() == original
    with tempfile.NamedTemporaryFile(dir=TARGET.parent, prefix=".policy-v54-ref-", delete=False) as stream:
        stream.write(payload)
        temporary = Path(stream.name)
    os.replace(temporary, TARGET)
    promotion_path = OUT / "v54-policy-promotion.json"
    promotion = json.loads(promotion_path.read_text())
    first_promoted = promotion.get("first_promoted_contract_sha256", promotion["new_contract_sha256"])
    revision = dict(revised_epoch=time.time(), old_baseline_contract_sha256=promotion["old_contract_sha256"],
        first_promoted_contract_sha256=first_promoted, before_sha256=digest(original),
        after_sha256=digest(payload), verification_before=before["verification"],
        verification_after=after["verification"], annotation_only=True,
        numerical_provider_source_metadata_unchanged=True,
        note="Traceability annotation only. The first candidate/review hash remains unchanged; no retroactive numerical proof claim.")
    (OUT / "v54-policy-verification-revision.json").write_text(json.dumps(revision, indent=2) + "\n")
    promotion.update(first_promoted_contract_sha256=first_promoted, new_contract_sha256=digest(payload),
                     verification_revision="v54-policy-verification-revision.json")
    promotion_path.write_text(json.dumps(promotion, indent=2) + "\n")
    print(json.dumps(revision))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--annotate-verification", action="store_true")
    parser.add_argument("--reuse-current-public-cache", action="store_true",
                        help="Use current content-addressed full-public vessel artifacts; record cache reuse explicitly.")
    args = parser.parse_args()
    if args.annotate_verification:
        annotate_verification()
    elif args.promote:
        promote()
    else:
        os.environ["STOWAGE_VESSEL_CACHE"] = "1" if args.reuse_current_public_cache else "0"
        review()

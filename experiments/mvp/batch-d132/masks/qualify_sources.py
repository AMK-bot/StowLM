"""Compare completed scratch worker epochs to the frozen assembly inventories."""
from hashlib import sha256
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSEMBLY = HERE.parent / "assembly"
BASELINE_EXPECTED = "1598d7cf10e1b5017696d6b5c2fb669ec534bb7f670dcee94df065b3faa95811"
COMBINED_EXPECTED = "7db1cd9a526cf7b4824d6157fad7aa3ba7c300133fd4749924b6336f03c3f470"
baseline_path = ASSEMBLY / "baseline-manifest.json"
candidate_path = ASSEMBLY / "combined-manifest.json"
assert sha256(baseline_path.read_bytes()).hexdigest() == BASELINE_EXPECTED
assert sha256(candidate_path.read_bytes()).hexdigest() == COMBINED_EXPECTED
baseline = json.loads(baseline_path.read_text())
candidate = json.loads(candidate_path.read_text())
checks = {}
epochs = {}
for arm, expected in (("baseline", baseline["src_stowage"]), ("candidate", candidate["src_stowage_after"])):
    summary = json.loads((HERE / arm / "summary.json").read_text())
    actual = {p: h for p, h in summary["source_epoch"].items() if p.startswith("src/stowage/")}
    epochs[arm] = summary["source_epoch"]
    declared = {p: row["sha256"] for p, row in expected.items()}
    checks[arm] = {"stowage_inventory_matches_assembly": actual == declared,
                   "stable_during_worker": summary["source_stable"],
                   "tree_matches_assembly": summary["tree"] == baseline[arm],
                   "actual_source_files": len(actual)}
actual_delta = sorted(p for p in epochs["baseline"].keys() | epochs["candidate"].keys()
                      if epochs["baseline"].get(p) != epochs["candidate"].get(p))
delta_matches = actual_delta == sorted(candidate["changed_src"])
root = HERE.parents[3]
final_path = root / "experiments/mvp/tomlcache/patches/GUARD-EVIDENCE.json"
final = json.loads(final_path.read_text())
canonical = final["candidate"]["canonical_patches"]
final_patches = {name: item["artifact"]["sha256"] for name, item in canonical.items()}
assert final_patches == {
    "0001-toml-content-cache.patch": "b48964666ebac09ab9136d887e96486471ac700371cbbd93b92b4362b1d31df9",
    "0002-toml-cache-tests.patch": "71ceb41fa503e7148a0e924ad25a1c495568bcf15e25f81c1833df3c10b686a3",
}
for item in canonical.values():
    assert sha256((root / item["artifact"]["path"]).read_bytes()).hexdigest() == item["artifact"]["sha256"]
compat_meta = final["combined_scratch_compatibility"]["artifact"]
compat_path = root / compat_meta["path"]
assert sha256(compat_path.read_bytes()).hexdigest() == compat_meta["sha256"]
compat = json.loads(compat_path.read_text())
final_matches = {}
for item in final["candidate"]["source_changes"]:
    path = item["path"]
    expected = (compat["rebase"]["files"][path]["target_sha256"]
                if path == "src/stowage/vessel/fleet_cache.py" else item["candidate_sha256"])
    final_matches[path] = epochs["candidate"].get(path) == expected
final_complete = bool(final["candidate_guards_complete"] and final["decode_guard"]["status"] == "passed"
    and final["decode_guard"]["traces"] == 214 and final["decode_guard"]["differences"] == 0
    and final["checker_guard"]["qualified"] and final["checker_guard"]["exact_reports"] == 100
    and all(final_matches.values())
    and epochs["candidate"]["src/stowage/vessel/fleet_cache.py"] == candidate["singleflight_rebase"]["combined_sha256"])
value = {"qualified": final_complete and delta_matches and all(all(v for k, v in row.items() if k != "actual_source_files")
                           for row in checks.values()), "checks": checks,
         "full_source_params_asset_delta_matches_exact_nine_assembly_changes": delta_matches,
         "actual_delta": actual_delta,
         "baseline_manifest_sha256": BASELINE_EXPECTED,
         "combined_manifest_sha256": COMBINED_EXPECTED,
         "guard_sha256": sha256((HERE / "guard.py").read_bytes()).hexdigest(),
         "toml_owner_final_pending": not final_complete,
         "toml_final_evidence_sha256": sha256(final_path.read_bytes()).hexdigest(),
         "toml_final_canonical_patch_hashes": final_patches,
         "toml_final_source_pins_match": final_matches,
         "toml_final_compatibility_sha256": compat_meta["sha256"],
         "caveat": "Canonical owner's 100-report guard is separate from this composite legal-set guard; no composite full-checker claim follows from it."}
(HERE / "source-qualification.json").write_text(json.dumps(value, indent=2) + "\n")
print(json.dumps(value))
raise SystemExit(0 if value["qualified"] else 1)

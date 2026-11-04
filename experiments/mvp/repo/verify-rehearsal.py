"""Independent stdlib-only hash/receipt audit of the regenerated rehearsal."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
manifest = json.loads((HERE / "generated/closure.json").read_text())
evidence = HERE / "rehearsal/final-20261030"
summary = json.loads((evidence / "summary.json").read_text())
tree = Path(summary["tree"])
checks = []


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(name, result, detail=None):
    checks.append({"name": name, "passed": bool(result), "detail": detail})


verify("closure tool/policy/baseline hashes unchanged", all(sha(HERE / path) == manifest[key] for path, key in [("closure.py", "tool_sha256"), ("closure-policy.json", "policy_sha256"), ("commit-list.txt", "baseline_sha256")]))
verify("closure has no errors or unresolved local imports", not manifest["errors"] and not manifest["unresolved_local_imports"])
verify("candidate counts and exact copied tree", len(manifest["rows"]) == manifest["selected"] == 1997 and manifest["copy"]["tree"] == str(tree))
changed = []
for row in manifest["rows"]:
    expected = manifest["copy"]["gitignore_combined_sha256"] if row["path"] == ".gitignore" else row["sha256"]
    target = tree / row["path"]
    if not target.is_file() or sha(target) != expected:
        changed.append(row["path"])
verify("all 1997 baseline files unchanged by install/tests/probes", not changed, changed)
listed = {row["path"] for row in manifest["rows"]}
verify("required additions selected", {"app/stage_fast.py", "app/stage_extras.py", "app/instruct.py", "experiments/mvp/rules.json", "src/llm/securing/assets.py", "src/llm/lookahead/mask_filter.py", "src/stowage/data_setup.py", "experiments/mvp/exit/wait_for_smoke.py"} <= listed)
verify("no raw/model/docs/secret/cache candidates", all(not p.startswith(("data/raw/", "models/", "docs/")) and ".env" != Path(p).name and not set(Path(p).parts) & {"private", ".git", ".venv", ".uv-cache", "__pycache__"} for p in listed))
verify("copy has no git metadata and own fresh env", not (tree / ".git").exists() and (tree / ".venv").is_dir() and summary["reused_environment"] is False)
expected_steps = {"sync", "origins", "data-setup", "fast-tests", "securing", "app-help", "eval-help", "checker", "app-http"}
verify("all nine requested steps passed and reaped normally", summary["completed"] and not summary["partial_rehearsal"] and {j["label"] for j in summary["jobs"]} == expected_steps and len(summary["jobs"]) == 9 and all(j["reaped"] and not j["timed_out"] and j["exit_code"] == 0 for j in summary["jobs"]))
for name, expected in [("tooling-tests.xml", (29, 0, 0, 0)), ("rehearsal/final-20261030/tests.xml", (175, 0, 0, 1))]:
    cases = list(ET.parse(HERE / name).getroot().iter("testcase"))
    counts = (len(cases), sum(c.find("failure") is not None for c in cases), sum(c.find("error") is not None for c in cases), sum(c.find("skipped") is not None for c in cases))
    verify(name + " counts", counts == expected, counts)
origins_checked = 0
for path in evidence.glob("*-origins.json"):
    origins = json.loads(path.read_text())
    if origins:
        origins_checked += 1
    verify(path.name + " stays under scratch", all(Path(value).resolve().is_relative_to(tree) for value in origins.values()))
verify("module origins asserted for every Python worker", origins_checked == 8, origins_checked)
setup = json.loads((tree / "data/raw/kcs/SETUP-MANIFEST.json").read_text())
member_hash = "aafc7e4f4d773bc50452d397985554c6a20f054150b8865ef70d61f836809190"
archive_hash = "62d737346e0a4bf7a065d3e675522d97dc67444c472aea1d3b45179a49fdbf58"
verify("real ZIP/member pins and nonredistribution provenance", summary["archive_sha256"] == setup["archive_sha256"] == archive_hash and sha(tree / "data/raw/kcs/kcs.fix") == setup["member_sha256"] == member_hash and setup["redistributed"] is False and setup["licence"] == "unstated; not redistributed")
verify("setup installed only declared member and provenance", sorted(p.name for p in (tree / "data/raw/kcs").iterdir()) == ["SETUP-MANIFEST.json", "kcs.fix"])
secured = json.loads((evidence / "securing.json").read_text())
verify("missing/tampered real installed asset fail closed and restore", secured["constructed"] and secured["declared_asset_validation_passed"] and secured["asset_restored"] and {p["condition"] for p in secured["real_asset_fail_closed_probes"]} == {"missing", "tampered"} and all(p["raised"] == "SecuringConfigurationError" for p in secured["real_asset_fail_closed_probes"]))
verify("synthetic securing not claimed active", secured["bounds_available"] is False and secured["metrics"]["securing_active"] is False and secured["metrics"]["unavailable"] == 1)
checker = json.loads((evidence / "checker.json").read_text())
verify("checker verdict remains rejected with bound report", checker["binding"] is True and checker["passed"] is False)
http = json.loads((evidence / "app-http.json").read_text())
verify("all 15 production HTTP handler requests pass", len(http["responses"]) == 15 and all(r["status"] == 200 for r in http["responses"]))
demo = next(r for r in http["responses"] if r["path"] == "/api/demo")
verify("UI rejection and keyless fallback preserved", demo["checker_passed"] is False and demo["shippable"] is False and demo["explanation_source"] == "template" and http["keyless_instruction_refusal"]["refusal"]["code"] == "no_recorded_response" and all(r["source"] == "template" and r["live_used"] is False for r in http["responses"] if r["path"].startswith("/api/stage/explain")))
verify("no network/GPU/model/test-corpus certification", summary["network_blocked_attempt_count"] == 0 and not summary["test_v21_inputs_used"] and not summary["model_weights_or_tokenizer_tested"] and not summary["approved_demo_bundle_tested"])
result = {"passed": all(c["passed"] for c in checks), "method": "Independent stdlib receipt/byte audit; no project imports, numerical jobs or network", "checks": checks, "tree": str(tree), "limits": "Operational tooling receipt only; final showcase/checker/mask/browser acceptance still pending."}
(HERE / "rehearsal-verification.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({"passed": result["passed"], "checks": len(checks), "failures": [c for c in checks if not c["passed"]]}))
raise SystemExit(0 if result["passed"] else 1)

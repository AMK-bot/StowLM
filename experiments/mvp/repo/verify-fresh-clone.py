"""Audit rehearsal receipts and frozen copy; no project imports or workloads."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
checks: list[dict] = []


def read(name: str):
    return json.loads((ROOT / name).read_text())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(name: str, passed: bool, detail=None):
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


manifest = read("fresh-clone-copy.json")
tree = Path(manifest["tree"])
scratch = tree.parent
verify("proposal list hash remains frozen", sha(ROOT / "commit-list.txt") == manifest["candidate_sha256"])
verify("all 1864 selected paths copied", manifest["selected"] == manifest["copied"] == 1864 and not manifest["missing"])
changed = []
for row in manifest["rows"]:
    expected = manifest["gitignore"]["combined_sha256"] if row["path"] == ".gitignore" else row["sha256"]
    path = tree / row["path"]
    if not path.is_file() or sha(path) != expected:
        changed.append(row["path"])
verify("baseline snapshot bytes unchanged", not changed, changed)
verify("baseline has no git metadata", not (tree / ".git").exists())
missing = ["app/stage_fast.py", "src/llm/securing/__init__.py", "data/raw/kcs/kcs.fix", "models/glimmer-30b-4bit/tokenizer.json"]
verify("reported omitted baseline assets remain absent", all(not (tree / p).exists() for p in missing), missing)

for name, expected in [("fresh-clone-sync.json", 1), ("fresh-clone-sync-full.json", 0)]:
    record = read(name)
    verify(name + " completed/reaped with recorded exit", record["completed"] and all(j["reaped"] for j in record["jobs"]) and record["jobs"][-1]["exit_code"] == expected)

for name, counts in [("fresh-clone-tests.xml", (127, 2, 0, 1)), ("fresh-clone-tokenizer.xml", (1, 0, 1, 0)), ("fresh-clone-tests-green.xml", (119, 0, 0, 0))]:
    cases = list(ET.parse(ROOT / name).getroot().iter("testcase"))
    actual = (len(cases), sum(c.find("failure") is not None for c in cases), sum(c.find("error") is not None for c in cases), sum(c.find("skipped") is not None for c in cases))
    verify(name + " counts", actual == counts, actual)
for name in ["fresh-clone-tests.json", "fresh-clone-tests-green.json"]:
    record = read(name)
    verify(name + " completed and workers reaped", record["completed"] and all(j["reaped"] and not j["aborted"] for j in record["jobs"]))
for name in ["fresh-clone-test-pid-closure.json", "fresh-clone-test-green-pid-closure.json"]:
    verify(name + " owned PIDs absent", all(row["state"] == "absent" for row in read(name)))

base = ROOT / "fresh-clone-evidence/app-baseline"
diag = ROOT / "fresh-clone-evidence/app-diagnostic"
verify("app fails at omitted module", "No module named 'app.stage_fast'" in (base / "app-start.stderr").read_text())
verify("baseline securing module absent", "No module named 'llm.securing'" in (base / "securing-baseline.stderr").read_text())
checker = json.loads(next(line for line in (base / "full-checker.stdout").read_text().splitlines() if line.startswith("{")))
verify("full checker executed, bound and rejected synthetic fixture", checker["binding"] is True and checker["passed"] is False)
for folder, expected in [(base, 5), (diag, 6)]:
    rows = [json.loads(line) for line in (folder / "processes.jsonl").read_text().splitlines()]
    started = {r["pid"] for r in rows if r.get("state") == "started"}
    ended = {r["pid"] for r in rows if r.get("state") == "reaped" and not r.get("timed_out")}
    verify(folder.name + " workers reaped normally", len(started) == expected and started == ended, sorted(started))

summary = read("fresh-clone-evidence/app-diagnostic/SUMMARY.json")
variant = scratch / "diagnostic-code-only"
patch = ROOT.parent / "determinism/patches/securing-fail-closed.patch"
verify("diagnostic used pinned D115 patch", sha(patch) == summary["diagnostic_patch_sha256"])
verify("diagnostic final patch targets match receipt", all(sha(variant / row["path"]) == row["sha256"] for row in summary["diagnostic_patched_paths"]))
verify("real fail-closed asset exception observed", "known_fleet_missing_asset SecuringConfigurationError" in (diag / "securing-config.stdout").read_text())
verify("repaired copy still excludes geometry and model weights", not (variant / "data/raw/kcs/kcs.fix").exists() and not list((variant / "models").glob("**/*.safetensors")))
verify("six core HTTP handler requests passed", len(summary["http_responses"]) == 6 and all(r["status"] == 200 for r in summary["http_responses"]))
before = summary["stage_before_static_repair_http"]
after = summary["stage_after_static_repair_http"]
verify("static failures and repairs retained", len(before) == len(after) == 9 and sum(r["status"] == 404 for r in before) == 7 and all(r["status"] == 200 for r in after))
verify("keyless fallback and no unsafe approval", summary["template_explanations_without_key_passed"] and summary["stage_payload_gates"]["checker_passed"] is False and summary["stage_payload_gates"]["departure_approved"] is False)
verify("TCP and visual passes not claimed", not summary["real_listening_server_passed"] and not summary["rendered_browser_or_visual_pass_claim"])
network_log = scratch / "logs/network-guard.jsonl"
verify("external network guard recorded no attempts", not network_log.exists() or not network_log.read_text().strip())
static = read("fresh-clone-static.json")
verify("AST inventory green and no test corpus input bodies", static["python_files_parsed"] == 1762 and not static["syntax_errors"] and static["no_test_input_bodies_in_candidate"])

output = {"created_utc": datetime.now(timezone.utc).isoformat(), "method": "stdlib receipt/hash audit only; no project imports, case evaluation, subprocess or network", "checks": checks, "all_passed": all(c["passed"] for c in checks), "scope": "Verifies evidence consistency, not judge-readiness; measured baseline failures remain failures."}
(ROOT / "fresh-clone-verification.json").write_text(json.dumps(output, indent=2) + "\n")
print(json.dumps({"all_passed": output["all_passed"], "checks": len(checks), "failures": [c for c in checks if not c["passed"]]}))
raise SystemExit(0 if output["all_passed"] else 1)

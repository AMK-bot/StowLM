"""Write the source-pinned checker handoff only after completed D126 receipts."""
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "experiments/mvp/fixwindow/d126-20261030"


def pin(path):
    return dict(path=str(path.relative_to(ROOT)), sha256=sha256(path.read_bytes()).hexdigest())


def main():
    target = OUT / "CHECKER-FREE.json"
    if target.exists():
        raise FileExistsError("Preserve historical source handoff")
    suites = {}
    for name, expected in (("manual-lashing-heat.xml", 40), ("checker-post-watchdog.xml", 407),
                           ("watchdog-live-integration.xml", 21)):
        path = OUT / name
        entries = list(ET.parse(path).getroot().iter("testsuite"))
        counts = {key: sum(int(entry.attrib.get(key, 0)) for entry in entries)
                  for key in ("tests", "errors", "failures", "skipped")}
        assert counts == dict(tests=expected, errors=0, failures=0, skipped=0), counts
        suites[name] = dict(**pin(path), counts=counts)
    builds = [json.loads((OUT / f"build-{ship}.json").read_text())
              for ship in ("feeder_500", "feeder_800", "feeder_1700")]
    gm_path = OUT / "design-gm-verified.json"
    gm = json.loads(gm_path.read_text())
    assert len(gm) == 3 and all(row["exactly_unchanged"] for row in gm)
    processes = []
    for directory in ("build-feeder500-process", "build-feeder800-process", "build-feeder1700-process",
                      "manual-suite-process", "design-gm-process", "design-gm-verified-process",
                      "showcase-process", "showcase-twostep-process", "checker-post-watchdog",
                      "watchdog-live-integration"):
        path = OUT / directory / "process.json"
        record = json.loads(path.read_text())
        assert record["reaped"] and not record["source_drift"] and not record.get("harness_timeout"), directory
        # Historical GM helper's failed assertion is retained, not a release pass.
        assert record["exit_code"] == (1 if directory == "design-gm-process" else 0), directory
        processes.append(dict(**pin(path), owned_pid=record["owned_pid"], reaped=True,
                              exit_code=record["exit_code"]))
    ledger = (ROOT / "experiments/fleet/ledger.md").read_text()
    assert not any(line.startswith("| mvp-construct/D126-window |") for line in ledger.splitlines())
    spec = importlib.util.spec_from_file_location("d126_source_handoff", OUT.parent / "run_checked.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    sources = helper.sources()
    for relative in ("experiments/mvp/gold_tools/driver.py", "tests/checker/test_external_watchdog.py"):
        sources[relative] = sha256((ROOT / relative).read_bytes()).hexdigest()
    showcases = []
    for directory in ("showcase-feeder800-seed115", "showcase-twostep-feeder800-seed115"):
        path = OUT / directory / "result.json"
        value = json.loads(path.read_text())
        assert value["completed"] and not value["passed"] and value["blocked_at"] == "constructor"
        showcases.append(dict(**pin(path), passed=False, blocked_at="constructor", wall_s=value["wall_s"],
                              physical_infeasibility_proven=False))
    record = dict(decision="D126", status="checker free", released_utc=datetime.now(timezone.utc).isoformat(),
                  source_hashes=sources, tests=suites, build_caps={r["ship"]:r["max_gm_lashing_m"] for r in builds},
                  design_gm=pin(gm_path), processes=processes, showcase_attempts=showcases,
                  no_passing_feeder800_showcase=True, owned_heavy_reservations=0,
                  boundary=pin(OUT.parent / "d126/BOUNDARY.json"),
                  qualification="Manual/GM and watchdog/checker tests pass; showcase remains an explicit construction failure.")
    target.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(dict(status=record["status"], released_utc=record["released_utc"],
                         receipt=str(target.relative_to(ROOT)), no_passing_feeder800_showcase=True)))


if __name__ == "__main__":
    main()

"""Demo v6 package chapter: the handover package as data, and a real verify run in its own process.

    view()                  -> everything /api/v6/package shows, read from the package folder
    start_verify({tamper})  -> starts one job; a second request of the same kind joins the running job
    job(id)                 -> real elapsed seconds and the state of each check, never a percentage

The package is ``package_dir`` from app/v6_hero.json, else the historical fixture.

``verify_package`` reports all its checks at once at the end, and the package folder isn't ours to edit. So the child runs
it twice in its own process: first with ``instance.json`` refused, which returns the check names and its real files, chain
and signature results in milliseconds, then in full, which reruns the checker. Check names come from that result, never a
list here. Nothing here re-implements a check.

A tamper run copies the package to a temp dir, changes one digit in plan.json and verifies the copy. The original is
never written to. The temp dir goes when the job ends. A job that outlives the server is stopped by ``atexit`` and,
if the server was killed outright, by the child watching its parent.
"""
from __future__ import annotations

import argparse
import atexit
import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

from app import v6_build
from app.v6_fixtures import Withheld, dicts, dig, optional, read_optional

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "experiments/mvp/pipeline/package/fixtures/complete"
SUMMARY = ROOT / "experiments/mvp/pipeline/package/fixtures/verification-summary.json"
HERO_FILE = Path(__file__).with_name("v6_hero.json")
RECORDED = ROOT / "experiments/mvp/ui/fixtures/verify-recorded.json"  # a real run of this module, for video and no-CPU
QUICK = ("files", "chain", "signature")
SIGNATURE_NOTE = "It says what ran and that nothing has changed since. Our own signature."
TIMEOUT_S = 600
KEEP_JOBS = 8
log = logging.getLogger(__name__)


def package_dir() -> Path:
    config = json.loads(HERO_FILE.read_text()).get("package_dir")
    return (ROOT / config).resolve() if config else FIXTURE


class PackageFile:
    """An allow-listed name in app/v6.py ASSETS whose folder is read per request, so a hero package needs no restart."""

    def __init__(self, name: str, display: bool = False):
        self.name, self.display = name, display

    def read_bytes(self) -> bytes:
        directory = package_dir()
        require_verified(directory)
        require_hero_plan(directory)
        body = (directory / self.name).read_bytes()
        return note_as_shown(as_shown(body.decode())).encode() if self.display else body


PACKAGED = "checked when packaged"
RECHECKED = "rechecked now"
_DATED = re.compile(r"computed on (\d{4}-\d{2}-\d{2})[^,]*, rechecked now")


def as_shown(text: str) -> str:
    """The stored files say "rechecked now", true when they were built. Served later they're a stored document, so the word goes.
    The signed files on disk keep it: changing a byte would fail verify. receipt.json is served raw for that reason."""
    return _DATED.sub(lambda found: f"computed on {found[1]}, checked on {found[1]}", text).replace(RECHECKED, PACKAGED)


def _json(path: Path):
    return json.loads(Path(path).read_text())


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- what the chapter shows
_STABILITY = ("gm_m", "trim_m", "heel_deg", "draught_aft_m", "draught_fwd_m", "draught_mean_m", "max_draught_m",
              "max_sf_ratio", "max_bm_ratio", "checker_passed", "numbers_label", "scope", "securing_note")


_CRANE = ("lift_order", "box", "to", "kind", "mass_t", "pod")  # what fits a sheet; the file has them all


def _crane(directory: Path) -> dict:
    rows = _csv_rows(directory / "crane-sequence.csv")
    header = [name for name in _CRANE if name in rows[0]]
    shown = [[f"{float(row[name]):.1f}" if name == "mass_t" and row[name] else row[name] for name in header] for row in rows[:18]]
    return {"header": header, "rows": shown, "total": len(rows), "kinds": Counter(row["kind"] for row in rows)}


def _steps(receipt: dict) -> list[dict]:
    from experiments.mvp.pipeline.contract.schema import sha256_json
    rows = receipt["steps"]
    return [{"stage": row.get("stage"), "mode": row.get("mode"), "model": row.get("model"), "wall_s": row.get("wall_s"),
             "prev": row.get("prev_sha256"), "hash": sha256_json(row)} for row in rows]


def _note_by(evidence: dict) -> str:
    spark = evidence.get("mode") in {"recorded", "live"} and str(evidence.get("model")).startswith("muse-spark")
    return "Muse Spark, from checker output" if spark and not evidence.get("rejection") else "Template, from checker output"


def _computed_date(index: dict) -> str | None:
    """The date the package states for its own run. provenance.recorded_utc dates the old fixture, not its plan."""
    found = re.search(r"computed on (\d{4}-\d{2}-\d{2})", str(index.get("banner")))
    return found[1] if found else None


def _stored_label(receipt: dict, index: dict, checked: str | None) -> dict:
    """"rechecked now" is only for the page to show after the checker really ran: it picks ``fresh`` then. Until then ``label``."""
    if receipt.get("label") != "stored":
        return {"label": str(receipt.get("label")), "fresh": None}
    date = _computed_date(index)
    stored = f"Stored plan, computed on {date}" if date else "Stored plan, computation date unrecorded"
    checked = checked or date
    return {"label": f"{stored} · Checked on {checked}" if checked else f"{stored}, {PACKAGED}", "fresh": f"{stored}, {RECHECKED}"}


def _engine_0_13(version: str, hero: dict | None) -> bool:
    """Only the package's own provenance can drop the tag: an eligible hero whose required engine is the one its receipt names."""
    return isinstance(hero, dict) and hero.get("eligible") is True and hero.get("engine_version_required") == version


def _other_sentence(directory: Path) -> str | None:
    """A package built under another sentence than the one on stage says so, until it's rebuilt for that one."""
    from app.v6 import hero_reading
    said = [row.get("text") for row in dicts(dig(read_optional(directory / "packet.json"), "voyage_sentences")) if row.get("text")]
    now = optional(hero_reading)
    return f"Built for another sentence: “{said[0]}”" if said and now and now["sentence"] not in said else None


def _first_sentence(text) -> str | None:
    found = re.match(r"[^.]+\.", str(text or ""))
    return found[0] if found else None


def _not_included(directory: Path) -> list[dict]:
    """The page's own headings and paragraphs, nothing rewritten."""
    text = as_shown((directory / "not-included.html").read_text())
    pairs = re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", text, re.S)
    return [{"name": unescape(name), "text": unescape(body)} for name, body in pairs]


def _csv_rows(path: Path) -> list[dict]:
    lines = [line for line in path.read_text().splitlines() if not line.startswith("# ")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def _manifest(directory: Path) -> dict:
    """The planned boxes a planner reads first: dangerous goods and reefers, then the count."""
    rows = [row for row in _csv_rows(directory / "manifest.csv") if row.get("record_kind") == "planned section cargo"]
    special = [row for row in rows if row.get("dg_un") or row.get("reefer") == "True"]
    return {"total": len(rows), "rows": [{"box": row["box"], "slot": row["slot"], "pod": row["pod"], "t": f"{float(row['mass_t']):.1f}",
                                          "what": f"UN {row['dg_un']}, class {row['dg_class']}" if row.get("dg_un") else "Reefer"}
                                         for row in special]}


def _cargo_work(directory: Path) -> dict:
    """The hold that stays on the paper, and the ballast that didn't move."""
    work = _json(directory / "cargo-work.json")
    tanks = [row for row in _csv_rows(directory / "ballast-plan.csv") if row.get("record_type") == "tank" and row.get("kind") == "ballast"]
    return {"status": work["operational_status"], "flagged": len(work.get("flagged_source_move_indices") or []), "lifts": work.get("quay_lifts"),
            "threshold_deg": work.get("list_threshold_deg"), "max_list_deg": work.get("max_abs_list_deg_est"), "ballast": work.get("ballast_line"),
            "tanks": [{"tank": row["tank"], "t": f"{float(row['departure_t']):.0f}", "capacity": f"{float(row['capacity_t']):.0f}"} for row in tanks if float(row["departure_t"]) > 0]}


def package_binding(directory: Path) -> dict | None:
    """The index and every file it lists, plan and receipt included, or None if any file differs from it or is missing."""
    index_path = Path(directory) / "package.json"
    try:
        files = _json(index_path)["files"]
        if not isinstance(files, dict) or not files:
            return None
        for name, expected in files.items():
            path = Path(directory) / name
            if Path(name).name != name or path.is_symlink() or _sha256(path) != expected:
                return None
        return {"index_sha256": _sha256(index_path), "files": files}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _digest(binding: dict) -> str:
    return hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def package_digest(directory: Path) -> str | None:
    binding = package_binding(directory)
    return _digest(binding) if binding else None


def _recording(binding: dict | None) -> dict:
    """The recorded run, only if it verified exactly these files."""
    data = read_optional(RECORDED, {})
    return data if binding and isinstance(data, dict) and data.get("package") == binding else {}


def _bound_summary(binding: dict | None, directory: Path) -> dict | None:
    data = read_optional(SUMMARY) if directory == FIXTURE and binding else None
    return optional(_summary_replay) if isinstance(data, dict) and data.get("package") == binding else None


def _last_job(digest: str) -> "_Job | None":
    """The newest finished verify of these exact files, in this process. A tamper run is about its copy."""
    rows = [job for job in list(_JOBS.values()) if not job.tamper and not job.running and job.package_sha256 == digest]
    return rows[-1] if rows else None


def _job_failure(job: "_Job") -> str | None:
    failed = [row["name"] for row in job.view()["checks"] if row["state"] == "failed"]
    if job.status != "done":
        return f"Package withdrawn: its verification did not finish ({job.reason or job.status})."
    if job.passed is not True or failed:
        return f"Package withdrawn: it failed verification ({', '.join(failed) or 'not passed'})."
    return None


def verification_state(directory: Path) -> dict:
    """{status: passed | failed | unverified, reason, package_sha256, checked_on}. A run here beats a recording; nothing else clears papers."""
    binding = package_binding(directory)
    if binding is None:
        return {"status": "failed", "reason": "Package withdrawn: its files differ from its own index.", "package_sha256": None, "checked_on": None}
    digest, recorded = _digest(binding), _recording(binding)
    job = _last_job(digest)
    if job is not None:
        reason = _job_failure(job)
        day = datetime.now(timezone.utc).date().isoformat()
        return {"status": "failed" if reason else "passed", "reason": reason, "package_sha256": digest, "checked_on": None if reason else day}
    run = recorded.get("verify") or _bound_summary(binding, directory)
    if run is None:
        return {"status": "unverified", "reason": "Package withheld: it hasn't been verified yet.", "package_sha256": digest, "checked_on": None}
    if run.get("passed") is not True:
        return {"status": "failed", "reason": "Package withdrawn: it failed verification.", "package_sha256": digest, "checked_on": None}
    measured = recorded.get("measured_utc")
    return {"status": "passed", "reason": None, "package_sha256": digest, "checked_on": str(measured)[:10] if measured else None}


def require_hero_plan(directory: Path) -> None:
    """The package is the handover of the plan on stage, or it isn't shown: built for another plan, it waits for its rebuild.
    Only a hero file that names a hero has a plan on stage to match."""
    config = json.loads(HERO_FILE.read_text())
    if not config.get("hero_source"):
        return
    from app import v6
    slot = v6._hero_slot(config)
    shown = slot.get("after_sha256") or slot.get("before_sha256")
    receipt = read_optional(directory / "receipt.json")
    if dig(receipt, "plan_sha256") != shown:
        raise Withheld("Package withheld: it was built for another plan than the one on stage, and waits for its rebuild.")
    rule = dig(slot, "instruction", "rule")
    if rule is not None and _package_rules(receipt) != [_rule_key(rule)]:
        raise Withheld("Package withheld: it was built under another rule than the one on stage, and waits for its rebuild.")


def _rule_key(rule) -> str | None:
    from app.brief import canonical_key
    return optional(lambda: canonical_key(rule))


def _package_rules(receipt) -> list:
    """The rule each confirmed sentence of the package was read to, as the receipt states it."""
    return [_rule_key(row.get("reading")) for row in dicts(dig(receipt, "rules", default=[]))]


# The package's own words for a batch confirmation: pipeline/package/confirmation.py BATCH_CONFIRMATION_SOURCE (v4), which a v4 receipt carries
# as its confirmation source. A v3 receipt's raw "user_authorized_batch:package_regeneration" maps to the same sentence. Tested equal to the file.
BATCH_LABEL = "user-authorised batch regeneration, no planner present (demo fixture)"
_CONFIRMED_BY = ((re.compile(r"(^|[_:\s-])batch([_:\s-]|$)", re.I), BATCH_LABEL), (re.compile(r"^planner_", re.I), "the planner, on screen"))


def confirmation_label(source) -> str | None:
    """Who confirmed, in words, by the source's pattern: a batch regeneration is a demo fixture, never a planner's press. Unknown: None."""
    text = str(source or "")
    return next((label for pattern, label in _CONFIRMED_BY if pattern.search(text)), None)


def receipt_confirmation(receipt: dict) -> str | None:
    for row in dicts(receipt.get("steps")):
        if row.get("stage") == "confirmation":
            return dig(optional(lambda: json.loads(row.get("notes") or "{}"), {}), "confirmation_source")
    return None


def package_reader(receipt: dict) -> dict | None:
    """Who read the package's sentence, and the pipeline it was built on: receipt.json's own fields, first 12 of each hash."""
    rows = [row for row in dicts(receipt.get("rules")) if isinstance(row.get("parser"), dict)]
    parser = rows[0]["parser"] if rows else {}
    adapter, pipeline = parser.get("adapter_sha256") or (rows[0].get("adapter_sha256") if rows else None), receipt.get("pipeline_sha256")
    if not parser.get("name") and not adapter and not pipeline:
        return None
    source = receipt_confirmation(receipt)
    short = lambda h: h[:12] if isinstance(h, str) else None
    return {"name": parser.get("name"), "model": parser.get("model"), "adapter": short(adapter), "pipeline": short(pipeline),
            "confirmed_by": confirmation_label(source)}  # the raw source never reaches the page


_CONFIRMED_BY_LINE = re.compile(r"[ \t]*Confirmed by: ([^\s]+?)\.(?=\s|$)")


def note_as_shown(text: str) -> str:
    """The note's "Confirmed by: <source>." in words: a batch source shows as the demo fixture it is, an unknown one goes. The file keeps its bytes."""
    def say(found):
        label = confirmation_label(found[1])
        return f" Confirmed by: {label}." if label else ""
    return _CONFIRMED_BY_LINE.sub(say, text)


def require_verified(directory: Path) -> dict:
    gate = verification_state(directory)
    if gate["status"] != "passed":
        raise Withheld(gate["reason"])
    return gate


def ensure_verified() -> str:
    """Launch: a package with no verification of its own gets a real one now, in its own process."""
    gate = verification_state(package_dir())
    if gate["status"] == "unverified":
        start_verify({"tamper": False})
        return "package verifying"
    return f"package {gate['status']}" + (f": {gate['reason']}" if gate["reason"] else "")


def _summary_replay() -> dict | None:
    """The package task's measured run. It has one figure for the three integrity checks together, and predates coverage."""
    if not SUMMARY.is_file():
        return None
    data = _json(SUMMARY)
    # The summary's newer run carries only a regression's elapsed time; the first run has the measured split.
    run = next(data[key] for key in ("verify_190_boxes", "initial_verify_190_boxes") if "wall_s" in dig(data, key, default={}))
    names = (*QUICK, "checker", "rules")  # all this old run measured
    wall, checker = run["wall_s"], run["checker_wall_s"]
    quick = wall - checker
    return {"passed": run["passed"], "wall_s": wall, "checker_s": checker, "scope": data["label"],
            "checks": [{"name": name, "state": "passed", "at_s": quick if name in QUICK else wall, "errors": []} for name in names]}


def _replay(receipt: dict, directory: Path) -> dict:
    """Measured results for video and no-CPU. A recording or the summary only counts for the exact package it verified."""
    binding = package_binding(directory)
    recorded = _recording(binding)
    verify = recorded.get("verify") or _bound_summary(binding, directory)
    return {"verify": verify, "tamper": recorded.get("tamper"), "measured_utc": recorded.get("measured_utc"), "machine": recorded.get("machine"),
            "checked_on": str(recorded.get("measured_utc"))[:10] if recorded.get("measured_utc") else None}


def view() -> dict:
    """Only the index and the receipt make it a package; every paper and panel past those can go missing on its own."""
    directory = package_dir()
    gate = require_verified(directory)
    require_hero_plan(directory)
    index, receipt = _json(directory / "package.json"), _json(directory / "receipt.json")
    stability = read_optional(directory / "stability.json", {})
    stability = stability if isinstance(stability, dict) else {}
    evidence = read_optional(directory / "note-evidence.json")
    relaxed = index.get("status") != "checked_plan"
    version = str(receipt.get("engine_version"))
    scope = read_optional(directory / "handover-scope.json", {})
    scope = scope if isinstance(scope, dict) else {}
    steps, none = optional(lambda: _steps(receipt), []), {"verify": None, "tamper": None, "measured_utc": None, "machine": None, "checked_on": None}
    verification = optional(lambda: _replay(receipt, directory), none)
    build = v6_build.current()  # this view is a receipt the app emits: it names the pipeline it ran on, or says it isn't frozen
    for name in ("verify", "tamper"):  # the page walks a run's checks
        if not isinstance(dig(verification, name, "checks"), list):
            verification[name] = None
    return {
        "status": index.get("status"), "relaxed": stability.get("banner") if relaxed else None,
        **_stored_label({"label": "stored", **receipt}, index, verification.get("checked_on") or gate["checked_on"]), "package_sha256": gate["package_sha256"], "synthetic_scope": scope.get("headline"),
        "historical": None if _engine_0_13(version, read_optional(directory / "hero-provenance.json")) else "Historical development plan, not Engine 0.13.0",
        "other_sentence": _other_sentence(directory), "scope_line": _first_sentence(scope.get("banner")), "boxes": scope.get("planned_boxes") or dig(read_optional(directory / "provenance.json"), "load_list_boxes"),
        "background": {"boxes": scope.get("background_boxes"), "t": scope.get("background_mass_t")},
        "engine_label": f"Engine {version}" if _engine_0_13(version, read_optional(directory / "hero-provenance.json")) else None,
        "files": sorted(index.get("files") or []), "note": note_as_shown(as_shown(optional(lambda: (directory / "note-to-master.md").read_text(), ""))),
        "note_by": _note_by(evidence) if isinstance(evidence, dict) else "From checker output", "note_recorded": dig(evidence, "mode") == "recorded", "bay_plan": "/api/v6/asset/bay-plan.html",
        "bay_pages": optional(lambda: (directory / "bay-plan.html").read_text().count('class="bay-page"'), 0),
        "crane": optional(lambda: _crane(directory), {"header": [], "rows": [], "total": 0, "kinds": {}}),
        "not_included": optional(lambda: _not_included(directory), []), "manifest": optional(lambda: _manifest(directory)), "cargo_work": optional(lambda: _cargo_work(directory)),
        "stability": {**{key: stability.get(key) for key in _STABILITY}, "checker_passed": stability.get("checker_passed", dig(receipt, "checker", "passed"))},
        "receipt": {"steps": steps, "checker": {"passed": dig(receipt, "checker", "passed"), "level": dig(receipt, "checker", "level")},
                    "engine_version": version, "plan_note": optional(lambda: receipt["steps"][0].get("notes")),
                    "signature": str(receipt.get("signature", ""))[:16], "plan_sha256": receipt.get("plan_sha256"),
                    "pipeline_sha256": build["pipeline_sha256"], "film_manifest_sha256": build["film_sha256"], "build_line": build["receipt_line"], "reader": optional(lambda: package_reader(receipt))},
        "verification": verification, "signature_note": SIGNATURE_NOTE,
        "links": {"crane": "/api/v6/asset/package-crane-sequence.csv", "stability": "/api/v6/asset/package-stability.json",
                  "note": "/api/v6/asset/package-note-to-master.md", "receipt": "/api/v6/asset/package-receipt.json",
                  "not_included": "/api/v6/asset/package-not-included.html", "manifest": "/api/v6/asset/package-manifest.csv", "cargo_work": "/api/v6/asset/package-cargo-work.json"},
    }


# ---------------------------------------------------------------- the child process
def _emit(**event) -> None:
    print(json.dumps(event), flush=True)


def _errors(check: dict) -> list[str]:
    out = []
    for row in check.get("errors") or []:
        text = str(row.get("error", row)) if isinstance(row, dict) else str(row)
        where = row.get("file") if isinstance(row, dict) else None
        rules = row.get("rules") if isinstance(row, dict) else None
        out.append(f"{where}: {text}" if where else f"{text}: {', '.join(rules)}" if rules else text)
    return out


def check_names(result: dict) -> list[str]:
    return [name for name, value in result.items() if isinstance(value, dict) and "passed" in value]


def stopped_run(path) -> dict:
    """verify_package's own result, stopped before the checker rerun: every check named, the first three real."""
    import experiments.mvp.pipeline.package as pkg

    class Stop(Exception):
        pass

    real = pkg._load

    def load(target):
        if Path(target).name == "instance.json":
            raise Stop  # caught by verify_package's checker block, which is the only part this skips
        return real(target)

    pkg._load = load
    try:
        return pkg.verify_package(path)
    finally:
        pkg._load = real


def quick_checks(path) -> dict:
    result = stopped_run(path)
    return {name: result[name] for name in QUICK}


def _watch_parent(parent: int, cleanup: str | None) -> None:
    while os.getppid() == parent:
        time.sleep(1)
    if cleanup:
        shutil.rmtree(cleanup, ignore_errors=True)
    os.killpg(0, signal.SIGKILL)


def child(path: str, parent: int, cleanup: str | None) -> int:
    threading.Thread(target=_watch_parent, args=(parent, cleanup), daemon=True).start()
    import experiments.mvp.pipeline.package as pkg
    stopped = stopped_run(path)
    _emit(names=check_names(stopped))
    for name in QUICK:
        _emit(check=name, passed=stopped[name]["passed"], errors=_errors(stopped[name]))
    result = pkg.verify_package(path)
    for name in check_names(result):
        _emit(check=name, passed=result[name]["passed"], errors=_errors(result[name]), broken_rules=result[name].get("broken_rules"))
    _emit(done=True, passed=result["passed"], wall_s=result["wall_s"])
    return 0


# ---------------------------------------------------------------- jobs
class _Job:
    def __init__(self, tamper: bool, directory: Path, temp: str | None, tampered: dict | None):
        self.id, self.tamper, self.directory, self.temp, self.tampered = uuid.uuid4().hex[:12], tamper, directory, temp, tampered
        self.started, self.ended = time.monotonic(), None
        self.status, self.reason, self.passed, self.wall_s, self.tail = "running", None, None, None, ""
        self.package_sha256 = None  # the exact files a clean run verifies; a tamper run's copy has none
        self.checks = {name: self._row(name) for name in QUICK}
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    @staticmethod
    def _row(name: str) -> dict:
        return {"name": name, "state": "pending", "at_s": None, "errors": []}

    @property
    def running(self) -> bool:
        return self.status == "running"

    def elapsed(self) -> float:
        return (self.ended if self.ended is not None else time.monotonic()) - self.started

    def apply(self, event: dict) -> None:
        with self.lock:
            if event.get("done"):
                self.passed, self.wall_s = event.get("passed"), event.get("wall_s")
                return
            for name in event.get("names") or []:
                self.checks.setdefault(name, self._row(name))
            if not event.get("check"):
                return
            row = self.checks.setdefault(event["check"], self._row(event["check"]))
            if row["at_s"] is None:
                row["at_s"] = round(self.elapsed(), 2)
            row.update(state="passed" if event["passed"] else "failed", errors=event.get("errors") or [])
            if event.get("broken_rules"):
                row["broken_rules"] = event["broken_rules"]

    def view(self) -> dict:
        with self.lock:
            rows = [dict(row) for row in self.checks.values()]
        if self.running:
            current = next((row for row in rows if row["state"] == "pending"), None)
            if current:
                current["state"] = "running"
        return {"id": self.id, "tamper": self.tamper, "status": self.status, "reason": self.reason, "live": True, "pid": self.proc.pid if self.proc else None, "package_sha256": self.package_sha256,
                "elapsed_s": round(self.elapsed(), 2), "wall_s": self.wall_s, "passed": self.passed, "checks": rows, "tampered": self.tampered}


_JOBS: dict[str, _Job] = {}
_LOCK = threading.Lock()


def _command(directory: Path, cleanup: str | None) -> list[str]:
    command = [sys.executable, "-m", "app.v6_package", "--verify", str(directory), "--parent", str(os.getpid())]
    return command + ["--cleanup", cleanup] if cleanup else command


def _env() -> dict:
    paths = [ROOT, ROOT / "src", ROOT / "experiments/mvp/frontier", ROOT / "experiments/mvp/frontier/lab"]
    extra = [os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else []
    threads = dict.fromkeys(("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"), "1")
    return {**os.environ, **threads, "PYTHONPATH": os.pathsep.join([*map(str, paths), *extra])}


def change_one_digit(directory: Path) -> dict:
    """Move the first loaded box two tiers up: one character, still valid JSON, a different plan."""
    path = directory / "plan.json"
    text = path.read_text()
    found = re.search(r'"dst": "(\d{7})"', text)
    if found is None:
        raise ValueError("plan.json has no slot to tamper with")
    at, digit = found.end(1) - 1, int(text[found.end(1) - 1])
    after = str((digit + 2) % 10)
    path.write_text(text[:at] + after + text[at + 1:])
    return {"file": "plan.json", "line": text.count("\n", 0, at) + 1, "before": found.group(1), "after": found.group(1)[:-1] + after}


def _reap(job: _Job) -> None:
    timer = threading.Timer(TIMEOUT_S, _stop, args=(job, f"stopped after {TIMEOUT_S} s"))
    timer.daemon = True
    timer.start()
    try:
        for line in job.proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                job.tail = line.strip() or job.tail
                continue
            job.apply(event)
        code = job.proc.wait()
    finally:
        timer.cancel()
        _clean(job)
    job.ended = time.monotonic()
    if job.running:
        complete = code == 0 and job.passed is not None
        job.status, job.reason = ("done", None) if complete else ("failed", job.tail or f"exit code {code}")


def _clean(job: _Job) -> None:
    if job.temp:
        shutil.rmtree(job.temp, ignore_errors=True)


def _stop(job: _Job, reason: str) -> None:
    if not job.running or job.proc is None or job.proc.poll() is not None:
        return
    job.status, job.reason = "stopped", reason
    try:
        os.killpg(job.proc.pid, signal.SIGKILL)  # the child leads its own session, so this reaches only what it started
    except ProcessLookupError:
        pass


def start_verify(fields: dict) -> dict:
    tamper = fields.get("tamper", False)
    if not isinstance(tamper, bool):
        raise ValueError("tamper must be true or false")
    with _LOCK:
        for job in _JOBS.values():
            if job.running and job.tamper == tamper:
                return job.view()
        directory, temp, tampered = package_dir(), None, None
        if tamper:
            temp = tempfile.mkdtemp(prefix="stowlm-verify-")
            copy = Path(temp) / "package"
            try:
                shutil.copytree(directory, copy)
                tampered = change_one_digit(copy)
            except BaseException:
                shutil.rmtree(temp, ignore_errors=True)
                raise
            directory = copy
        job = _Job(tamper, directory, temp, tampered)
        job.package_sha256 = None if tamper else package_digest(directory)
        try:
            job.proc = subprocess.Popen(_command(directory, temp), cwd=ROOT, env=_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, start_new_session=True)
        except OSError:
            _clean(job)
            raise
        log.info("v6 verify job %s: child pid %s", job.id, job.proc.pid)
        _JOBS[job.id] = job
        for old in [j for j in _JOBS.values() if not j.running][:-KEEP_JOBS]:
            del _JOBS[old.id]
        threading.Thread(target=_reap, args=(job,), daemon=True).start()
        return job.view()


def job(job_id: str) -> dict:
    found = _JOBS.get(job_id)
    return found.view() if found else {"id": job_id, "status": "unknown", "checks": [], "live": False}


def _stop_all() -> None:
    for row in list(_JOBS.values()):
        _stop(row, "the server stopped")
        _clean(row)


atexit.register(_stop_all)


def record() -> None:
    """One real verify and one real tamper, saved for video mode. Rerun when the package changes. CPU only: verify_package's
    hashes, chain, signature and a full checker rerun, in child processes; nothing renders. The recording binds to the package
    index and every file hash, and is refused if the package isn't the hero's plan and rule or changed during the runs."""
    directory = package_dir()
    require_hero_plan(directory)
    before = package_binding(directory)
    if before is None:
        raise SystemExit("not recorded: the package files differ from its own index")
    runs = {}
    for name, tamper in (("verify", False), ("tamper", True)):
        current = start_verify({"tamper": tamper})
        while job(current["id"])["status"] == "running":
            time.sleep(0.5)
        if not tamper and job(current["id"])["package_sha256"] != _digest(before):
            raise SystemExit("not recorded: the verify ran on other files than the package index names")
        runs[name] = {k: v for k, v in job(current["id"]).items() if k not in {"id", "pid", "live", "package_sha256"}}
        print(name, runs[name]["status"], runs[name]["passed"], runs[name]["elapsed_s"], flush=True)
    if runs["verify"]["passed"] is not True or runs["tamper"]["passed"] is not False:
        raise SystemExit("not recorded: the clean package must pass and the tampered copy must fail")
    if package_binding(directory) != before:
        raise SystemExit("not recorded: the package changed while it was being verified")
    load = os.getloadavg()[0]
    RECORDED.parent.mkdir(parents=True, exist_ok=True)
    RECORDED.write_text(json.dumps(v6_build.stamp({"receipt_sha256": _sha256(directory / "receipt.json"), "package": before,
                                    "package_pipeline_sha256": dig(read_optional(directory / "receipt.json"), "pipeline_sha256"), "measured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "machine": f"shared development machine, load {load:.0f}, one thread per process", **runs}), indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--parent", type=int)
    parser.add_argument("--cleanup")
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    if args.record:
        record()
    else:
        raise SystemExit(child(str(args.verify), args.parent, args.cleanup))

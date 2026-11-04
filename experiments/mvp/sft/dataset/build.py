"""7.1 (v4) MVP SFT dataset builder: checker-passed plans -> train/val JSONL for llm.stowlm.train (dataset_kind mvp_gold).

Per plan (``sft_data.build_mvp_example``):
- the plan's full-checker report passed and is bound to (instance, plan) (``report_matches_inputs``);
- the plan is serialised in format stowlm@1.0 / prompt 2.2 (rule freeze OFF);
- every completion token replays through the 5.1 mask on the D79 engine (C2 off by config), with no masked token.

Rows over ``--max-len`` (the 5.9 cap, 10,240) are dropped and counted, never truncated. Test-corpus inputs are
refused unless ``--test-fixture`` (tooling check only: rows get a stub and must go under a ``sft-pilot/`` folder).

Inputs: record JSON files, or dirs searched for ``record.json`` / ``*.record.json`` and mvp-gold case folders
(``result.json`` + ``plan.json`` + ``checker.json`` + ``case.lock``; failed cases count as ``not_gold``). A record has the instance
(``instance_path`` or ``instance``), the plan (``result.plan`` or ``plan``) and the checker report (``report``).
The harness/refs record layout (experiments/mvp/runs/test-*/instances/*/record.json) is one such layout.

Usage: uv run python experiments/mvp/sft/dataset/build.py --out <dir> --train <records...> --val <records...>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
TEST_SUMS = ROOT / "experiments/mvp/corpus/test/SHA256SUMS"
SHOWCASE_SUMS = ROOT / "experiments/mvp/corpus/showcase/SHA256SUMS"
MAX_LEN = json.loads((ROOT / "experiments/mvp/sft/config.json").read_text())["train_args"]["max_seq_length"]
FIXTURE_STUB = {"mvp.sft.test_fixture": "test-split reference plan used to exercise the 7.1 tooling; never train on it"}


class _Refused(Exception):
    """A refusal reason already formatted by the worker (kept verbatim)."""


def record_files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in (Path(x).resolve() for x in paths):
        if p.is_dir():
            out += sorted(x for x in p.rglob("*.json") if x.name == "record.json" or x.name.endswith(".record.json")
                          or (x.name == "result.json" and (x.parent / "case.lock").exists()))  # mvp-gold case
        else:
            out.append(p)
    return out


def test_input_hashes() -> set[str]:
    if not TEST_SUMS.exists():
        return set()
    return {line.split()[0] for line in TEST_SUMS.read_text().splitlines() if line.strip()}


def held_out_hashes() -> set[str]:
    """Current test + showcase membership (hash lists plus every instance file under those folders), re-read on every
    build: admission is never cached (rev-v4-h P1 #1)."""
    out = set(test_input_hashes())
    if SHOWCASE_SUMS.exists():
        out |= {line.split()[0] for line in SHOWCASE_SUMS.read_text().splitlines() if line.strip()}
    for folder in ("test", "showcase"):
        for f in (ROOT / "experiments/mvp/corpus" / folder).rglob("*.json"):
            if not f.name.endswith(".audit.json"):
                out.add(hashlib.sha256(f.read_bytes()).hexdigest())
    return out


def is_held_out(input_sha: str | None, ipath, held: set[str]) -> bool:
    """By content (current hash lists and files) or by location (a corpus/test or corpus/showcase path)."""
    if input_sha in held:
        return True
    parts = Path(ipath).parts if ipath is not None else ()
    return any(parts[i] == "corpus" and parts[i + 1] in ("test", "showcase") for i in range(len(parts) - 1))


def load_triple(path: Path):
    from stowage.io import load_instance
    from stowage.model import Instance, MovePlan

    rec = json.loads(path.read_text())
    if path.name == "result.json" and (path.parent / "case.lock").exists():  # mvp-gold case folder (6.1)
        if rec.get("passed") is not True:
            raise ValueError(f"NOT_GOLD: gold case did not pass ({rec.get('drop_reason') or rec.get('error')})")
        rec = {"instance_path": rec["input_path"], "plan": json.loads((path.parent / "plan.json").read_text()),
               "report": json.loads((path.parent / "checker.json").read_text())}
    if rec.get("instance_path"):
        ipath = (ROOT / rec["instance_path"]) if not Path(rec["instance_path"]).is_absolute() else Path(rec["instance_path"])
        instance, raw = load_instance(ipath), ipath.read_bytes()
    else:
        instance = Instance.model_validate(rec["instance"])
        raw, ipath = instance.model_dump_json().encode(), None
    plan = (rec.get("result") or {}).get("plan") or rec.get("plan")
    if plan is None:
        raise ValueError("record has no plan")
    report = rec.get("report")
    if report is None:
        raise ValueError("record has no checker report")
    return instance, MovePlan.model_validate(plan), report, hashlib.sha256(raw).hexdigest(), ipath


CACHE_NAMESPACE = "v2"  # rev-v4-h: v1 entries (incomplete key, cached admission) are all misses
CACHE = Path(os.environ.get("STOWLM_SFT_ROW_CACHE", ROOT / "experiments/mvp/sft/dataset/cache")) / CACHE_NAMESPACE
_FINGERPRINT_ROOTS = ("src", "params", "data")  # every producer/replay/checker module and the physical data they read
_TOK = None


def code_key() -> str:
    """Row-cache code identity (rev-v4-h P1 #2): a complete fingerprint of the dependency graph that can change a row,
    not an allowlist. All files under src/ (serialiser, mask, securing + assets, feasibility engine, checker rules,
    solver/ballast), params/ and data/ (ship parameters, IMDG and other physical tables), the tokenizer files and the
    declared component versions. APPROXIMATION: derived numerical caches under experiments/cache are excluded; they
    are deterministic functions of these inputs (their own loaders validate them)."""
    from llm.stowlm.format import DEFAULT_TOKENIZER_PATH
    from stowage.versions import component_versions

    h = hashlib.sha256(CACHE_NAMESPACE.encode())
    for root in _FINGERPRINT_ROOTS:
        for f in sorted((ROOT / root).rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and not f.name.startswith("."):
                h.update(str(f.relative_to(ROOT)).encode() + b"\0" + f.read_bytes())
    tok = Path(DEFAULT_TOKENIZER_PATH)
    for f in sorted(tok.glob("*.json")) if tok.is_dir() else [tok]:
        h.update(f.name.encode() + b"\0" + f.read_bytes())
    h.update(json.dumps(component_versions(), sort_keys=True).encode())
    return h.hexdigest()


def _referenced_input(path: Path, rec: dict) -> Path | None:
    raw = rec.get("input_path") if path.name == "result.json" and (path.parent / "case.lock").exists() \
        else rec.get("instance_path")
    if not raw:
        return None
    return Path(raw) if Path(raw).is_absolute() else ROOT / raw


def record_key(path: Path) -> str:
    """Record content identity (rev-v4-h P1 #3): the record file, for a gold case folder its plan and checker report,
    and the bytes of the instance the record references (or its absence), all read before any cache lookup."""
    raw = path.read_bytes()
    h = hashlib.sha256(raw)
    if path.name == "result.json" and (path.parent / "case.lock").exists():
        for name in ("plan.json", "checker.json"):
            f = path.parent / name
            h.update(name.encode() + b"\0" + (f.read_bytes() if f.exists() else b"<missing>"))
    try:
        ref = _referenced_input(path, json.loads(raw))
    except ValueError:
        ref = None
    h.update(b"instance\0" + (ref.read_bytes() if ref is not None and ref.is_file() else b"<missing>"))
    return h.hexdigest()


def _work(job):
    """Expensive per-record part (load + serialise + D79/securing mask replay), run in a pool worker. Pure function of
    the record/instance bytes and the code fingerprint, so its result can be cached; it makes NO admission decision
    (test/showcase membership, fixture mode, leak, length): the parent applies those on every row, cached or not."""
    path, split, rel, held, skip_held, ckey, use_cache = job
    from dataclasses import asdict

    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.sft_data import build_mvp_example

    global _TOK
    cache_file = None
    if use_cache:
        cache_file = CACHE / f"{hashlib.sha256((record_key(path) + ckey + split + rel).encode()).hexdigest()}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text())
    out = {"rung": "?", "input_sha": None, "ipath": None, "example": None, "error": None}
    try:
        instance, plan, report, input_sha, ipath = load_triple(path)
        out.update(rung=instance.size_class, input_sha=input_sha, ipath=None if ipath is None else str(ipath))
        if skip_held and is_held_out(input_sha, ipath, held):
            out["skipped_held_out"] = True  # the parent refuses it; not worth replaying, and never cached
            return out
        _TOK = _TOK or load_tokenizer()
        out["example"] = asdict(build_mvp_example(instance, plan, report, split=split, source=rel, tokenizer=_TOK))
    except Exception as error:
        out["error"] = f"{type(error).__name__}: {error}"[:300]
    if cache_file is not None:
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(out))
        tmp.replace(cache_file)
    return out


def main() -> None:
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.sft_data import build_mvp_example, length_stats, write_dataset
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--train", nargs="*", default=[])
    ap.add_argument("--val", nargs="*", default=[])
    ap.add_argument("--max-len", type=int, default=MAX_LEN)
    ap.add_argument("--test-fixture", action="store_true", help="admit test inputs as a stubbed tooling fixture")
    ap.add_argument("--workers", type=int, default=1, help="process pool for the per-record replay (native threads 1)")
    ap.add_argument("--no-cache", action="store_true", help="don't read/write the per-record row cache")
    ap.add_argument("--holdout-from", default=None,
                    help="restrict --holdout-val candidates to train records under this directory (e.g. small gold)")
    ap.add_argument("--holdout-val", type=int, default=0,
                    help="no val gold yet: move K passing train cases (deterministic by path hash) to val")
    a = ap.parse_args()
    assert not rule_freeze_v1_enabled(), "MVP datasets are built with the rule-freeze switch OFF"
    out = Path(a.out).resolve()
    if a.test_fixture and "sft-pilot" not in out.parts:
        raise SystemExit("--test-fixture output must live under a sft-pilot/ folder")
    held = held_out_hashes()
    examples, refusals, seen = [], [], {}
    counts: dict[str, Counter] = defaultdict(Counter)
    t0 = time.perf_counter()
    train_files, val_files = record_files(a.train), record_files(a.val)
    if a.holdout_val:
        # APPROXIMATION: val drawn from train-corpus instances (seed-disjoint from test; distinct instances from
        # train). Only gold cases that passed are eligible, so K val rows really exist.
        def passed(p):
            try:
                return json.loads(p.read_text()).get("passed", True) is True
            except ValueError:
                return False
        scope = Path(a.holdout_from).resolve() if a.holdout_from else None
        ranked = sorted((p for p in train_files if passed(p) and (scope is None or p.is_relative_to(scope))),
                        key=lambda p: hashlib.sha256(str(p.relative_to(ROOT)).encode()).hexdigest())
        held = set(ranked[:a.holdout_val])
        train_files = [p for p in train_files if p not in held]
        val_files = val_files + sorted(held)
    from llm.stowlm.sft_data import SFTExample

    ckey = code_key()
    jobs = [(path, split, str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path), held,
             not a.test_fixture, ckey, not a.no_cache)
            for split, files in (("train", train_files), ("val", val_files)) for path in files]
    if a.workers > 1:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor

        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
            os.environ[var] = "1"  # inherited by spawned workers: one native thread each
        with ProcessPoolExecutor(a.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            outcomes = list(pool.map(_work, jobs, chunksize=4))  # map keeps the serial file order
    else:
        outcomes = [_work(job) for job in jobs]
    for (path, split, rel, *_), w in zip(jobs, outcomes):
            rung = w["rung"]
            try:
                input_sha = w["input_sha"]
                if input_sha is None:
                    raise _Refused(w["error"] or "record did not load")
                # admission on EVERY row, cache hit or not, against today's test/showcase membership (P1 #1)
                if is_held_out(input_sha, w.get("ipath"), held) and not a.test_fixture:
                    raise ValueError("input is in the frozen MVP test/showcase split")
                if input_sha in seen and seen[input_sha] != split:
                    raise ValueError(f"input already used in split {seen[input_sha]} (leak)")
                if w["error"] is not None:
                    raise _Refused(w["error"])
                ex = SFTExample(**w["example"])
                if len(ex.input_ids) > a.max_len:
                    raise ValueError(f"over length cap: {len(ex.input_ids)} > {a.max_len}")
                if a.test_fixture:
                    ex.stubs.update(FIXTURE_STUB)
                seen[input_sha] = split
                ex.meta["gold"]["input_sha256"] = input_sha
                ex.meta["rung"] = rung
                examples.append(ex)
                counts[split][f"{rung}:admitted"] += 1
            except Exception as error:
                reason = str(error) if isinstance(error, _Refused) else f"{type(error).__name__}: {error}"[:300]
                refusals.append({"split": split, "record": rel, "rung": rung, "reason": reason})
                kind = ("over_length" if "over length cap" in reason else "not_gold" if "NOT_GOLD" in reason
                        else "refused")
                counts[split][f"{rung}:{kind}"] += 1
    if not examples:
        raise SystemExit(f"no admitted examples; refusals: {refusals[:5]}")
    stats = write_dataset(examples, out, mvp=not a.test_fixture)
    by_rung = {f"{split}/{rung}": length_stats([e for e in examples if e.split == split and e.meta["rung"] == rung])
               for split, rung in sorted({(e.split, e.meta["rung"]) for e in examples})}
    report = {"out": str(out), "dataset_kind": stats["dataset_kind"], "max_len": a.max_len,
              "counts": {k: dict(v) for k, v in counts.items()}, "by_rung": by_rung,
              "processed_tokens": {s: sum(len(e.input_ids) for e in examples if e.split == s) for s in ("train", "val")},
              "replay": stats["replay"], "refusals": refusals, "wall_s": round(time.perf_counter() - t0, 1),
              "workers": a.workers, "row_cache": None if a.no_cache else {"dir": str(CACHE.relative_to(ROOT) if CACHE.is_relative_to(ROOT) else CACHE),
                                                                           "code_key": ckey}}
    (out / "build_report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "refusals"}, indent=1))
    print(f"refusals: {len(refusals)}", *(r["reason"] for r in refusals[:5]), sep="\n  ")


if __name__ == "__main__":
    main()

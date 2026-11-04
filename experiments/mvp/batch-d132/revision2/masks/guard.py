"""D132 fixed-prefix, separate-process legal-set guard; never reads TEST inputs.

Prepare scripts from the qualified 214 TRAIN/VAL/fixture transcripts. Run each
arm in a fresh interpreter with its scratch src first. Compare actual legal
lists and state content, allowing only heat-related additions. No GPU/model.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
ORIGIN = ROOT / "experiments/mvp/maskspeed/out/guard2-d115-final.json"
TRANSCRIPTS = ORIGIN.with_suffix(".transcripts.jsonl")
TOKENIZER = ROOT / "models/glimmer-30b-4bit/tokenizer.json"
PREPARED = ROOT / "experiments/mvp/batch-d132/masks/prepared"
V1 = ROOT / "experiments/mvp/batch-d132/masks"
V2_MANIFEST = ROOT / "experiments/mvp/batch-d132/assembly/revision2-combined-manifest.json"
V2_MANIFEST_SHA256 = "779bc9901801c4a02c3ae0c2853db689d784e7dabd84ea4dee84e6c446047780"


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(json.dumps(value, sort_keys=True) + "\n")
    tmp.replace(path)


def approved_map(relative):
    path = (ROOT / relative).resolve()
    if not any(path.is_relative_to((ROOT / "experiments/mvp/corpus" / split).resolve())
               for split in ("train", "val")):
        raise ValueError(f"D110 excluded input; refusing even to hash/read it: {relative}")
    return path


def prepare():
    origin = json.loads(ORIGIN.read_text())
    assert origin["exact"] and len(origin["traces"]) == 214
    assert len(origin["excluded"]) == 0
    for trace in origin["traces"]:
        assert trace["split"] in {"fixture", "train", "val"}
        if trace.get("map"):
            approved_map(trace["map"])
    # Only after every path is approved may actual TRAIN/VAL bodies be hashed.
    for relative, expected in origin["input_manifest"].items():
        assert digest(approved_map(relative)) == expected
    ids = {trace["id"] for trace in origin["traces"]}
    groups = {key: [] for key in ids}
    with TRANSCRIPTS.open() as stream:
        for raw in stream:
            row = json.loads(raw)
            if row["trace_id"] not in ids:
                raise ValueError("Unexpected trace outside qualified input policy")
            if row["arm"] == "before" and row["event"]["type"] != "done":
                groups[row["trace_id"]].append(row["event"])
    records = []
    for index, trace in enumerate(origin["traces"]):
        events = groups[trace["id"]]
        assert events and events[-1]["type"] == "final"
        path = PREPARED / f"trace-{index:03d}.json"
        write(path, {"trace": trace, "script": events})
        records.append({"index": index, "id": trace["id"], "file": path.name,
                        "sha256": digest(path), "prefixes": sum(e["type"] == "prefix" for e in events),
                        "split": trace["split"], "map": trace.get("map")})
    value = {"origin": str(ORIGIN), "origin_sha256": digest(ORIGIN),
        "transcripts_sha256": digest(TRANSCRIPTS), "tokenizer": str(TOKENIZER),
        "tokenizer_sha256": digest(TOKENIZER), "inputs": origin["input_manifest"],
        "traces": records, "count": len(records), "test_bodies_read": False,
        "prefixes": sum(r["prefixes"] for r in records),
        "script_policy": "Archived operations only; both new arms freshly query every actual legal set"}
    write(PREPARED / "manifest.json", value)
    print(json.dumps({"prepared": len(records), "prefixes": value["prefixes"]}))


def source_epoch(tree):
    paths = []
    for folder in (tree / "src", tree / "params"):
        paths.extend(p for p in folder.rglob("*") if p.is_file()
                     and p.suffix in {".py", ".toml", ".json"} and "__pycache__" not in p.parts)
    paths.append(tree / "data/raw/kcs/kcs.fix")
    return {str(p.relative_to(tree)): digest(p) for p in sorted(paths)}


def classify_legal_delta(before, after, heat_context):
    key = lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"))
    left, right = {key(x) for x in before}, {key(x) for x in after}
    added, removed = sorted(right - left), sorted(left - right)
    relevant = bool(heat_context and heat_context["remaining"]
        and heat_context["old_unrepresented_tanks"] and not heat_context["correct_unrepresented_tanks"])
    return added, removed, bool(added) and not removed and relevant


def run(args):
    assert os.environ.get("PYTHONHASHSEED") == "0", "Pin hash seed before interpreter startup"
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        assert os.environ.get(key) == "1", key
    tree = args.tree.resolve()
    assert digest(V2_MANIFEST) == V2_MANIFEST_SHA256
    v2_manifest = json.loads(V2_MANIFEST.read_text())
    assert tree == Path(v2_manifest["candidate"]).resolve()
    print(json.dumps({"worker_pid": os.getpid(), "arm": args.arm, "tree": str(tree)}), flush=True)
    sys.path.insert(0, str(tree / "src"))
    import stowage
    import llm.stowlm.mask as mask
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.securing.mask_filter import SecuringEngine
    from llm.lookahead.mask_filter import LookaheadEngine
    from stowage.actions import get_action_space
    from stowage.actions.samples import tiny_instance
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.feasibility import _engine as native_module
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    for module in (stowage, mask, native_module,
                   sys.modules["llm.securing.mask_filter"], sys.modules["llm.lookahead.mask_filter"]):
        assert Path(module.__file__).resolve().is_relative_to(tree / "src"), module.__file__
    assert rule_freeze_v1_enabled() is False
    prepared = json.loads((PREPARED / "manifest.json").read_text())
    assert digest(TOKENIZER) == prepared["tokenizer_sha256"]
    epoch = source_epoch(tree)
    for group in ("src_stowage_after", "src_exit_after"):
        assert all(epoch.get(path) == row["sha256"] for path, row in v2_manifest[group].items())
    v1_epoch = json.loads((V1 / "candidate/summary.json").read_text())["source_epoch"]
    delta = sorted(path for path in v1_epoch.keys() | epoch.keys() if v1_epoch.get(path) != epoch.get(path))
    assert delta == sorted(v2_manifest["changed_exit"]), "V2 changed dependencies outside declared ExIt targets"
    assert epoch["data/raw/kcs/kcs.fix"] == "aafc7e4f4d773bc50452d397985554c6a20f054150b8865ef70d61f836809190"
    space = get_action_space()
    tokenizer = load_tokenizer(TOKENIZER)
    folder = HERE / args.arm
    started, cpu_start = time.perf_counter(), time.process_time()
    results = []
    for record in prepared["traces"][:args.limit]:
        if os.environ.get("D132_MASK_STOP") and Path(os.environ["D132_MASK_STOP"]).exists():
            raise SystemExit(75)
        path = PREPARED / record["file"]
        assert digest(path) == record["sha256"]
        saved = json.loads(path.read_text())
        trace, script = saved["trace"], saved["script"]
        value = {"id": trace["id"], "index": record["index"], "events": []}
        trace_started = time.perf_counter()
        try:
            if trace["split"] == "fixture":
                instance = tiny_instance(trace["seed"], n_load=8, layover=bool(trace["seed"] % 2))
            else:
                instance_path = approved_map(trace["map"])
                assert digest(instance_path) == prepared["inputs"][trace["map"]]
                instance = load_instance(instance_path)
            native = make_feasibility_engine(instance, space,
                config=FeasibilityConfig(extra={"global_screen": 0}))
            engine = SecuringEngine(LookaheadEngine(native))
            if trace["split"] != "fixture":
                assert engine.securing_metrics()["securing_active"], "Securing inactive on real TRAIN/VAL map"
            codec = ActionCodec(tokenizer, [b.id for b in instance.containers])
            decoder = mask.MaskedDecoder(engine, codec, mask.MaskConfig(lookahead=trace.get("lookahead", 0)))
            clone = None

            def heat_context(current):
                occupied = set(current.expander.occ.values())
                sources = instance.vessel.heat_sources
                return {
                    "remaining": [b.id for b in instance.containers if b.id in instance.load_list
                        and b.id not in occupied and "away_from_heat" in b.special_stowage],
                    "old_unrepresented_tanks": [t.id for t in instance.vessel.tanks if t.heated
                        and t.id not in {h.id for h in sources}],
                    "correct_unrepresented_tanks": [t.id for t in instance.vessel.tanks if t.heated
                        and t.id not in {h.tank_id if h.tank_id is not None else h.id
                                        for h in sources if h.kind == "heated_tank"}],
                }

            for operation in script:
                kind = operation["type"]
                if kind == "prefix":
                    if operation["label"] == "prefetched":
                        decoder.prefetch()
                    legal = list(decoder.allowed_token_ids())
                    assert [space.encode(a) for a in decoder.actions] == operation["actions"]
                    assert list(decoder.tokens) == operation["tokens"]
                    value["events"].append({**operation, "legal": legal, "status": decoder.status,
                                            "heat_context": heat_context(decoder.engine)})
                elif kind == "advance":
                    if operation["token"] not in decoder.allowed_token_ids():
                        raise ValueError("Archived token is no longer legal in this source epoch")
                    committed = decoder.advance(operation["token"])
                    actual = space.encode(committed) if committed is not None else None
                    assert actual == operation["committed"]
                    value["events"].append(dict(operation))
                elif kind in {"pop_partial", "pop_committed"}:
                    action = space.encode(decoder.pop_action())
                    assert action == operation["action"]
                    value["events"].append(dict(operation))
                elif kind == "engine_probe":
                    if operation["label"] == "before-push":
                        clone = decoder.engine.copy()
                    elif operation["label"] == "after-push":
                        clone.push(space.decode(operation["actions"][-1]))
                    else:
                        assert operation["label"] == "after-pop"
                        clone.pop()
                    legal = sorted((space.encode(a) for a in clone.legal_actions()),
                                   key=lambda a: json.dumps(a, sort_keys=True, separators=(",", ":")))
                    assert [space.encode(a) for a in clone.actions()] == operation["actions"]
                    value["events"].append({**operation, "legal": legal, "heat_context": heat_context(clone)})
                elif kind == "final":
                    value["events"].append({**operation, "status": decoder.status,
                        "plan": decoder.engine.to_plan().model_dump(mode="json")
                                if decoder.status == "complete" else None})
                else:
                    raise ValueError(f"Unrecognized archived operation: {kind}")
            value.update(replayed=True, securing=decoder.engine.securing_metrics(),
                         status=decoder.status, prefixes=sum(e["type"] == "prefix" for e in value["events"]))
        except Exception as error:
            value.update(replayed=False, error=f"{type(error).__name__}: {error}", traceback=traceback.format_exc())
        value["wall_s"] = time.perf_counter() - trace_started
        target = folder / record["file"]
        write(target, value)
        results.append({k: value[k] for k in ("id", "index", "replayed", "wall_s")})
        summary = {"arm": args.arm, "tree": str(tree), "source_epoch": epoch,
            "manifest_sha256": digest(PREPARED / "manifest.json"), "results": results,
            "wall_s": time.perf_counter() - started, "cpu_s": time.process_time() - cpu_start,
            "complete": len(results) == 214 and all(r["replayed"] for r in results),
            "c2": 0, "rule_freeze": False, "hashseed": "0", "tokenizer_sha256": prepared["tokenizer_sha256"],
            "worker_pid": os.getpid(), "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "revision2_manifest_sha256": V2_MANIFEST_SHA256, "v1_candidate_source_delta": delta,
            "test_bodies_read": False, "actual_imports": {m.__name__: m.__file__ for m in (stowage, mask)},
            "cache_policy": "Fresh process per arm; default caches confined to scratch runtime; no shared Python caches"}
        write(folder / "summary.json", summary)
        print(json.dumps({"arm": args.arm, "index": record["index"], "id": trace["id"],
                          "replayed": value["replayed"], "wall_s": value["wall_s"]}), flush=True)
    summary["source_stable"] = epoch == source_epoch(tree)
    summary["complete"] &= summary["source_stable"]
    write(folder / "summary.json", summary)


def compare():
    manifest = json.loads((PREPARED / "manifest.json").read_text())
    differences, errors, counts = [], [], Counter()
    summaries = [json.loads((V1 / "baseline/summary.json").read_text()), json.loads((HERE / "candidate/summary.json").read_text())]
    for record in manifest["traces"]:
        paths = [V1 / "baseline" / record["file"], HERE / "candidate" / record["file"]]
        if not all(p.is_file() for p in paths):
            errors.append({"id": record["id"], "reason": "missing arm"}); continue
        before, after = (json.loads(p.read_text()) for p in paths)
        if not before["replayed"] or not after["replayed"]:
            errors.append({"id": record["id"], "reason": "unreplayable", "errors": [before.get("error"), after.get("error")]}); continue
        assert len(before["events"]) == len(after["events"])
        heat_context = None
        for index, (a, b) in enumerate(zip(before["events"], after["events"])):
            kind = a["type"]
            if kind not in {"prefix", "engine_probe"}:
                if a != b:
                    # A formerly dead terminal prefix may now have immediate escape.
                    if kind == "final" and a["status"] == "dead_end" and b["status"] == "running":
                        counts["terminal_immediate_escape"] += 1
                    else:
                        errors.append({"id": record["id"], "event": index, "reason": "state/final content differs"})
                continue
            counts[kind] += 1
            heat_context = a["heat_context"]
            added, removed, accepted = classify_legal_delta(a["legal"], b["legal"], heat_context)
            comparable_a = {k: v for k, v in a.items() if k not in {"legal", "status"}}
            comparable_b = {k: v for k, v in b.items() if k not in {"legal", "status"}}
            if comparable_a != comparable_b:
                errors.append({"id": record["id"], "event": index, "reason": "prefix content differs"})
            if added or removed:
                differences.append({"id": record["id"], "event": index, "kind": kind,
                    "added": [json.loads(x) for x in added], "removed": [json.loads(x) for x in removed],
                    "before_legal": a["legal"], "after_legal": b["legal"],
                    "heat_context": heat_context, "heat_relevant_additions_only": accepted})
                if not accepted:
                    errors.append({"id": record["id"], "event": index, "reason": "deletion or non-heat addition"})
            elif a["legal"] != b["legal"]:
                errors.append({"id": record["id"], "event": index, "reason": "order-only difference"})
            if a.get("status") != b.get("status") and not (added and not removed
                    and a.get("status") == "dead_end" and b.get("status") == "running"):
                errors.append({"id": record["id"], "event": index, "reason": "unexplained status difference"})
        counts["traces"] += 1
    complete = all(s["complete"] and s["source_stable"] for s in summaries) and counts["traces"] == 214
    result = {"qualified": complete and not errors, "complete214": complete, "counts": dict(counts),
        "changed_queries": len(differences), "differences": differences, "errors": errors,
        "future_completion_claimed": False, "full_checker_run": False,
        "synthetic_fail_closed_tightening_cases_are_separate": True}
    write(HERE / "comparison.json", result)
    print(json.dumps({k: result[k] for k in ("qualified", "complete214", "counts", "changed_queries")}))
    return 0 if result["qualified"] else 1


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--arm", choices=("candidate",), required=True)
    run_parser.add_argument("--tree", type=Path, required=True)
    run_parser.add_argument("--limit", type=int, default=214)
    sub.add_parser("compare")
    args = parser.parse_args()
    if args.command == "run": run(args)
    else: raise SystemExit(compare())


if __name__ == "__main__":
    main()

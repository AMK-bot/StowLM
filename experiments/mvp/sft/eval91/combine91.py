"""9.1 combine (D128 recipe, D128.5 equal finisher budgets).

Rows: "StowLM own plan" (pass@1, greedy) and, if sampled/ exists, best-of-(k+1) (fewest weighted lifts among StowLM's
own passing plans; greedy wins ties). Two-step from twostep/ (same pipeline, same finisher budget). Every row reports
outcome categories: pass, repaired pass (5.7 fallback, never counted as StowLM), finisher time limit, dead end,
checker fail, error. Paired comparisons use maps where both sides pass.
Usage: uv run python experiments/mvp/sft/eval91/combine91.py <out_dir>
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
from math import comb, isfinite
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OWN = "own pass"
STOWLM_HYBRID = "StowLM + 15 s solver polish (hybrid)"
TWOSTEP_HYBRID = "two-step + 15 s solver polish (hybrid)"
GUARD_PROTOCOL = "twostep-polish91-v1"


def load_run(run: Path) -> dict[str, dict]:
    rows = {}
    for rec_path in run.glob("instances/*.json"):  # loose-mode records: one per input
        rec = json.loads(rec_path.read_text())
        folder = run / "instances" / rec["instance_id"]
        outcome = json.loads((folder / "outcome.json").read_text())["outcome"]
        report = json.loads((folder / "report.json").read_text()) or {}
        rows[rec["instance_id"]] = {"rung": rec["rung"], "outcome": outcome, "input": rec["input"],
                                    "objective": (report.get("count") or {}).get("weighted_nonproductive")
                                    if outcome in (OWN, "repaired pass") else None,
                                    "pipeline_s": rec["pipeline_s"], "plan_from": str(folder / "result.json")}
    return rows


def load_twostep(folder: Path) -> dict[str, dict]:
    out = {}
    for p in folder.glob("*.json"):
        r = json.loads(p.read_text())
        out[r["instance_id"]] = {"outcome": OWN if r.get("passed") else r.get("outcome", "fail"),
                                 "objective": r.get("weighted_nonproductive"), "pipeline_s": r.get("s"),
                                 "_source_sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    return out


def summarise(rows: dict[str, dict], two: dict, rung: str | None) -> dict:
    sel = {i: r for i, r in rows.items() if rung is None or r["rung"] == rung}
    own = {i: r["objective"] for i, r in sel.items() if r["outcome"] == OWN}
    pairs = [(o, two[i]["objective"]) for i, o in own.items() if two.get(i, {}).get("outcome") == OWN]
    d = [a - b for a, b in pairs]
    f = lambda xs: st.mean(xs) if xs else None
    return {"n": len(sel), "own_pass": len(own),
            "outcomes": {k: sum(r["outcome"] == k for r in sel.values()) for k in sorted({r["outcome"] for r in sel.values()})},
            "own_mean_lifts": f(list(own.values())),
            "paired_vs_twostep": {"n": len(pairs), "stowlm_mean": f([a for a, _ in pairs]),
                                  "twostep_mean": f([b for _, b in pairs]), "mean_diff": f(d),
                                  "better": sum(x < 0 for x in d), "equal": sum(x == 0 for x in d),
                                  "worse": sum(x > 0 for x in d)},
            "s_mean": f([r["pipeline_s"] for r in sel.values() if r["pipeline_s"] is not None])}


def _hybrid_row(original, record, before, after):
    """Keep failed original outcomes and add polish only to checked passing plans."""
    return dict(original, objective=after, before_polish=before, polish_s=record["polish_s"],
                pipeline_s=(original.get("pipeline_s") or 0) + record["polish_s"])


def _finite_number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and isfinite(value)


def load_stowlm_polish(out, own):
    target = out / "polish"
    if not target.exists():
        return None
    metadata = out / "polish_summary.json"
    if not metadata.exists() or json.loads(metadata.read_text()).get("label") != STOWLM_HYBRID:
        raise ValueError("StowLM hybrid requires completed exactly-15-second polish metadata")
    expected = {i for i, row in own.items() if row["outcome"] == OWN}
    records = {}
    for path in target.glob("*.json"):
        row = json.loads(path.read_text())
        iid = row.get("instance_id")
        if iid in records or iid not in expected:
            raise ValueError("StowLM hybrid has unexpected or duplicate map records")
        records[iid] = row
    if set(records) != expected:
        raise ValueError("StowLM hybrid polish records are incomplete")
    result = {i: dict(row) for i, row in own.items()}
    for iid, row in records.items():
        before, after = row.get("stowlm_lifts"), row.get("polished_lifts")
        if (row.get("own_report_passed") is not True or before != own[iid]["objective"]
                or not _finite_number(before) or not _finite_number(after) or after > before
                or not _finite_number(row.get("polish_s")) or row["polish_s"] < 0
                or row.get("requested_polish_s") != 15):
            raise ValueError("StowLM hybrid record failed baseline or budget checks")
        source = Path(own[iid]["plan_from"])
        if (not row.get("source_result_sha256")
                or row["source_result_sha256"] != hashlib.sha256(source.read_bytes()).hexdigest()):
            raise ValueError("StowLM hybrid record is not bound to its selected own plan")
        result[iid] = _hybrid_row(own[iid], row, before, after)
    return result


def load_twostep_polish(out, two):
    target = out / "twostep-polish"
    if not target.exists():
        return None
    marker = target / "GUARD.json"
    if not marker.exists():
        raise ValueError("two-step hybrid has no complete equality guard")
    guard = json.loads(marker.read_text())
    passes = {i for i, row in two.items() if row["outcome"] == OWN}
    if (guard.get("protocol") != GUARD_PROTOCOL or guard.get("stage") != "complete"
            or guard.get("valid") is not True or guard.get("all_matches") is not True
            or len(two) != 30 or guard.get("n_expected") != 30 or guard.get("n_matches") != 30
            or guard.get("n_mismatches") != 0 or guard.get("n_passing") != len(passes)
            or guard.get("n_polished") != len(passes) or guard.get("requested_polish_s") != 15
            or not guard.get("fingerprint")):
        raise ValueError("two-step hybrid equality or completion guard failed")
    originals = guard.get("reference_sha256") or {}
    if set(originals) != set(two) or any(originals[i] != row["_source_sha256"] for i, row in two.items()):
        raise ValueError("two-step hybrid guard is not bound to all original outcome records")
    paths = guard.get("rows") or {}
    hashes = guard.get("polish_sha256") or {}
    if set(paths) != passes or set(hashes) != passes:
        raise ValueError("two-step hybrid guard has incomplete passing-map coverage")
    # A guarded run may never borrow arbitrary files outside its own polish directory.
    actual_files = {p.stem for p in target.glob("*.json") if p.name != "GUARD.json"}
    if actual_files != set(two):
        raise ValueError("two-step hybrid has unexpected or missing polished records")
    result = {i: dict(row) for i, row in two.items()}
    for iid in sorted(set(two) - passes):
        row = json.loads((target / f"{iid}.json").read_text())
        if (row.get("instance_id") != iid or row.get("status") != "skipped_not_passing"
                or row.get("run_fingerprint") != guard["fingerprint"]):
            raise ValueError("two-step hybrid nonpassing-map marker is incomplete")
    for iid in sorted(passes):
        path = target / f"{iid}.json"
        if Path(paths[iid]).resolve() != path.resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != hashes[iid]:
            raise ValueError("two-step hybrid polished file differs from completion guard")
        row = json.loads(path.read_text())
        before, after = row.get("before_moves"), row.get("after_moves")
        if (row.get("instance_id") != iid or row.get("status") != "completed"
                or row.get("run_fingerprint") != guard["fingerprint"]
                or row.get("own_report_passed") is not True or row.get("final_report_passed") is not True
                or row.get("assignments_preserved") is not True or row.get("ballast_preserved") is not True
                or row.get("requested_polish_s") != 15 or before != two[iid]["objective"]
                or not _finite_number(before) or not _finite_number(after) or after > before
                or not _finite_number(row.get("polish_s")) or row["polish_s"] < 0):
            raise ValueError("two-step hybrid polished record failed independent admission checks")
        result[iid] = _hybrid_row(two[iid], row, before, after)
    return result


def hybrid_pair(left, right, rung):
    maps = [i for i, row in left.items() if (rung is None or row["rung"] == rung)
            and row["outcome"] == OWN and right.get(i, {}).get("outcome") == OWN]
    pairs = [(left[i]["objective"], right[i]["objective"]) for i in maps]
    fewer, more = sum(a < b for a, b in pairs), sum(a > b for a, b in pairs)
    n = fewer + more
    p = min(Fraction(1), Fraction(2 * sum(comb(n, k) for k in range(min(fewer, more) + 1)), 2**n)) if n else Fraction(1)
    return {"n": len(pairs), "stowlm_mean": st.mean(a for a, _ in pairs) if pairs else None,
            "twostep_mean": st.mean(b for _, b in pairs) if pairs else None,
            "fewer": fewer, "equal": len(pairs) - fewer - more, "more": more,
            "sign_test_p": float(p), "sign_test_exact": str(p), "sign_test_discordant": n}


def combine(out: Path):
    stowlm, two = load_run(out / "stowlm"), load_twostep(out / "twostep")
    for i, r in two.items():  # rungs for the two-step rows
        r["rung"] = stowlm.get(i, {}).get("rung", "?")
    rows = {"StowLM own plan (pass@1, greedy)": stowlm}
    if (out / "sampled" / "instances").exists():
        sampled, best = load_run(out / "sampled"), {}
        for i, g in stowlm.items():
            cands = [r for r in (g, sampled.get(i)) if r and r["outcome"] == OWN]
            best[i] = dict(min(cands, key=lambda r: r["objective"]) if cands else g,
                           pipeline_s=g["pipeline_s"] + (sampled.get(i) or {}).get("pipeline_s", 0))
        rows["StowLM best-of-(k+1) (greedy + samples, checker-selected)"] = best
    # Freeze own-plan selection before adding any solver-edited rows.
    chosen_own = list(rows.values())[-1]
    chosen = {i: r["plan_from"] for i, r in chosen_own.items() if r["outcome"] == OWN}
    rows["two-step (same pipeline, same finisher budget)"] = two
    hybrid_status = {}
    def optional_hybrid(name, loader, original, directory):
        try:
            result = loader(out, original)
        except (ValueError, KeyError, TypeError, OSError, ArithmeticError) as error:
            # A first combine may run while optional polish is unfinished. Keep
            # baseline rows usable, but admit no incomplete or mismatched hybrid.
            reason = str(error) if type(error) is ValueError else type(error).__name__
            hybrid_status[name] = {"status": "unavailable", "reason": reason}
            return None
        if directory.exists():
            hybrid_status[name] = {"status": "complete"}
        return result
    stowlm_polish = optional_hybrid(STOWLM_HYBRID, load_stowlm_polish, chosen_own, out / "polish")
    twostep_polish = optional_hybrid(TWOSTEP_HYBRID, load_twostep_polish, two, out / "twostep-polish")
    if stowlm_polish is not None:
        rows[STOWLM_HYBRID] = stowlm_polish
    if twostep_polish is not None:
        rows[TWOSTEP_HYBRID] = twostep_polish
    summary = {"rows": {name: {r: summarise(v, two, r) for r in ("tiny", "small", None)} for name, v in rows.items()},
               "chosen": chosen,
               "labels": {"own": "cargo sequence decoded by the model under the mask; ballast by the deterministic V4 "
                                 "finisher; full checker passed; no repair, no solver edits to moves"}}
    if stowlm_polish is not None or twostep_polish is not None:
        summary["labels"]["hybrid"] = "15 s requested CPU solver polish; fixed stow assignments; separately labelled; never used in chosen own-plan selection"
    if stowlm_polish is not None and twostep_polish is not None:
        summary["hybrid_paired"] = {r: hybrid_pair(stowlm_polish, twostep_polish, r) for r in ("tiny", "small", None)}
    if hybrid_status:
        summary["hybrid_status"] = hybrid_status
    fmt = lambda x: "—" if x is None else f"{x:.2f}"
    lines = ["| row | rung | pass | outcomes | mean lifts (pass) | paired vs two-step (n, Δ, better/equal/worse) | s/map |",
             "|---|---|---|---|---|---|---|"]
    for name, by in summary["rows"].items():
        for rung, s in by.items():
            p = s["paired_vs_twostep"]
            lines.append(f"| {name} | {rung or 'all'} | {s['own_pass']}/{s['n']} | {s['outcomes']} | {fmt(s['own_mean_lifts'])} | "
                         f"{p['n']}, {fmt(p['mean_diff'])}, {p['better']}/{p['equal']}/{p['worse']} | {fmt(s['s_mean'])} |")
    if "hybrid_paired" in summary:
        lines += ["", "Hybrid comparison: StowLM + 15 s polish vs two-step + 15 s polish, on mutual passing maps only.",
                  "Exact two-sided sign tests exclude ties; no multiple-comparison adjustment. Hybrid rows never enter own-plan selection.", "",
                  "| rung | mutual | StowLM mean | two-step mean | fewer / equal / more (StowLM) | exact sign p |",
                  "|---|---:|---:|---:|---:|---:|"]
        for rung, pair in summary["hybrid_paired"].items():
            lines.append(f"| {rung or 'all'} | {pair['n']} | {fmt(pair['stowlm_mean'])} | {fmt(pair['twostep_mean'])} | {pair['fewer']}/{pair['equal']}/{pair['more']} | {pair['sign_test_p']:.8g} |")
    for name, status in hybrid_status.items():
        if status["status"] != "complete":
            lines += ["", f"{name}: unavailable; {status['reason']}. No hybrid result admitted."]
    return summary, "# 9.1 StowLM evaluation (D128 recipe, equal finisher budgets)\n\n" + "\n".join(lines) + "\n", "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--sealed", action="store_true", help="write result artifacts without printing any result values")
    args = parser.parse_args()
    try:
        summary, markdown, display = combine(args.out_dir)
    except (ValueError, KeyError, TypeError, OSError, ArithmeticError) as error:
        if args.sealed:
            parser.exit(2, f"combine91 refused: {type(error).__name__}; errors=1; no result values printed\n")
        raise
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    (args.out_dir / "SUMMARY.md").write_text(markdown)
    print("combine91 wrote summary.json and SUMMARY.md; errors=0" if args.sealed else display)


if __name__ == "__main__":
    main()

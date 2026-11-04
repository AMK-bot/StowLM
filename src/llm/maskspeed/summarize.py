"""Combine disjoint guards and weight host timings by actual row/batch steps."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


def summarize(benchmark, guards=()):
    records = benchmark["benchmarks"]
    by_key = {(r["runtime"], r["B"], r["map"]):r for r in records}
    if len(by_key) != len(records):
        raise AssertionError("duplicate benchmark arm/map/batch")
    for record in records:
        if record["runtime"] != "before":
            continue
        other = by_key[("after", record["B"], record["map"])]
        for key in ("row_steps", "batch_steps", "completed_actions", "final_statuses", "final_tokens_sha256"):
            if key in record or key in other:
                assert record.get(key) == other.get(key), (record["map"], record["B"], key)
    result = dict(cpu_only=True, source_stable=benchmark.get("source_stable"),
                  pythonhashseed=benchmark.get("pythonhashseed"), batches={})
    for batch in sorted({r["B"] for r in records}):
        result["batches"][str(batch)] = {}
        for arm in ("before", "after"):
            rows = [r for r in records if r["runtime"] == arm and r["B"] == batch]
            row_steps = sum(r["row_steps"] for r in rows)
            batch_steps = sum(r["batch_steps"] for r in rows)
            def weighted(key):
                return sum(r[key]*r["row_steps"] for r in rows)/row_steps
            full_ms = sum(r["full_ms_per_row_step"]*r["row_steps"] for r in rows)
            layers = sorted({key for row in rows for key in row["layer_exclusive_ms_per_row_step"]})
            layer_ms = {key:sum(r["layer_exclusive_ms_per_row_step"].get(key,0)*r["row_steps"]
                              for r in rows) for key in layers}
            result["batches"][str(batch)][arm] = dict(maps=len(rows), row_steps=row_steps,
                batch_steps=batch_steps, full_ms_per_row_step=full_ms/row_steps,
                full_ms_per_batch_step=full_ms/batch_steps,
                query_ms_per_row_step=weighted("query_ms_per_row_step"),
                advance_ms_per_row_step=weighted("advance_ms_per_row_step"),
                layer_exclusive_ms_per_row_step={k:v/row_steps for k,v in layer_ms.items()},
                layer_exclusive_ms_per_batch_step={k:v/batch_steps for k,v in layer_ms.items()},
                instrumentation_wall_overhead_ratio=sum(r["instrumented_wall_s"] for r in rows)
                    /sum(r["wall_s"] for r in rows))
            item = result["batches"][str(batch)][arm]
            clocks = {r.get("layer_clock", "perf_counter") for r in rows}
            assert len(clocks) == 1, "mixed layer clocks"
            item["layer_clock"] = next(iter(clocks))
            if all("full_cpu_ms_per_row_step" in r for r in rows):
                cpu_ms = sum(r["full_cpu_ms_per_row_step"]*r["row_steps"] for r in rows)
                item.update(full_cpu_ms_per_row_step=cpu_ms/row_steps,
                    full_cpu_ms_per_batch_step=cpu_ms/batch_steps,
                    query_cpu_ms_per_row_step=weighted("query_cpu_ms_per_row_step"),
                    advance_cpu_ms_per_row_step=weighted("advance_cpu_ms_per_row_step"),
                    instrumentation_cpu_overhead_ratio=sum(r["instrumented_cpu_s"] for r in rows)
                        /sum(r["cpu_s"] for r in rows))
    traces = [trace for guard in guards for trace in guard["traces"]]
    if traces:
        assert len({t["id"] for t in traces}) == len(traces), "guard shards overlap"
        assert all(t["exact"] for t in traces)
        result["guard"] = dict(traces=len(traces), exact=True,
            source_stable=all(g.get("source_stable") for g in guards),
            pythonhashseed=sorted({g.get("pythonhashseed", "unpinned") for g in guards}),
            kinds=dict(Counter(t["kind"] for t in traces)),
            statuses=dict(Counter(t["status"] for t in traces)),
            baseline_unreplayable=sum(len(g.get("unreplayable", [])) for g in guards),
            steps=sum(t["steps"] for t in traces), backtracks=sum(t["backtracks"] for t in traces),
            prefetch_probes=sum(t["prefetch_probes"] for t in traces))
        result["guard"]["qualified"] = (result["guard"]["source_stable"]
            and result["guard"]["pythonhashseed"] == ["0"] and len(traces) >= 200)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", type=Path)
    parser.add_argument("--guard", type=Path, nargs="*", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(json.loads(args.benchmark.read_text()),
                       [json.loads(path.read_text()) for path in args.guard])
    content = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(content)
    print(content)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Size a gold campaign from measured, single-core attempt costs and admitted yield.

Input JSON: {"label": "epoch/shards measured", "measurements": [
  {"rung": "tiny", "attempted": 10, "published": 2,
   "total_attempt_seconds": 50, "prompt_tokens": 591, "output_tokens": 43,
   "available_instances": 1500}]}

Attempt time MUST include unsuccessful attempts, timeouts, checker, mask replay,
and both solvers if enabled. Published means unique admitted instance plans, not
solver candidates. Token lengths are mean prompt/output lengths of admitted
examples. Never supply a success-only time mean. Available instances are the
total allocation one is prepared to attempt (including calibration shards).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from eval.snapshot_io import read_snapshot
import math
from pathlib import Path
from typing import Any


RUNGS = ("tiny", "small", "medium", "full")


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be numeric")
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    return value


def _integer(value: Any, name: str) -> int:
    result = _number(value, name)
    if not result.is_integer():
        raise ValueError(f"{name} must be an integer")
    return int(result)


@dataclass(frozen=True)
class Measurement:
    rung: str
    attempted: int
    published: int
    total_attempt_seconds: float
    prompt_tokens: float
    output_tokens: float
    available_instances: int

    @classmethod
    def parse(cls, row: dict[str, Any]) -> "Measurement":
        rung = row["rung"]
        if rung not in RUNGS:
            raise ValueError(f"unknown rung: {rung}")
        attempted = _integer(row["attempted"], "attempted")
        published = _integer(row["published"], "published")
        if attempted == 0 or published > attempted:
            raise ValueError("require 0 <= published <= attempted and attempted > 0")
        total = _number(row["total_attempt_seconds"], "total_attempt_seconds", positive=True)
        prompt = _number(row["prompt_tokens"], "prompt_tokens")
        output = _number(row["output_tokens"], "output_tokens")
        if published and prompt + output <= 0:
            raise ValueError("published examples require positive total token length")
        available = _integer(row["available_instances"], "available_instances")
        if available < attempted:
            raise ValueError("available_instances includes calibration and must be >= attempted")
        return cls(rung, attempted, published, total, prompt, output, available)

    @property
    def rate(self) -> float:
        return self.published / self.attempted

    @property
    def attempt_seconds(self) -> float:
        return self.total_attempt_seconds / self.attempted

    @property
    def tokens(self) -> float:
        return self.prompt_tokens + self.output_tokens


def plan_budget(
    payload: dict[str, Any], *, target_tokens: float = 12_000_000,
    cores: int = 4, min_counts: dict[str, int] | None = None,
    max_counts: dict[str, int] | None = None, full_cap: int | None = None,
    token_shares: dict[str, float] | None = None, pipeline_test: bool = False,
    epochs: int = 1, training_tokens_per_second: float = 200,
) -> dict[str, Any]:
    """Size the D31 token shares using measured same-rung yields and costs.

    The explicit pipeline-test mode retains efficiency-only sizing for diagnostics.
    Neither mode supplies an exact integer optimisation guarantee.
    """
    if not isinstance(payload.get("label"), str) or not payload["label"].strip():
        raise ValueError("measurements require a nonempty provenance label")
    target_tokens = _number(target_tokens, "target_tokens", positive=True)
    cores = _integer(cores, "cores")
    epochs = _integer(epochs, "epochs")
    full_cap = _integer(full_cap, "full_cap") if full_cap is not None else (300 if pipeline_test else None)
    shares = dict(token_shares or dict(tiny=.10, small=.30, medium=.35, full=.25))
    if set(shares) != set(RUNGS) or any(not math.isfinite(v) or v < 0 for v in shares.values()) or not math.isclose(sum(shares.values()), 1.):
        raise ValueError("token shares must contain all rungs and sum to one")
    if not pipeline_test and (shares["tiny"] > .10 or not math.isclose(shares["small"], .30) or not math.isclose(shares["medium"], .35)):
        raise ValueError("production shares require tiny <= .10, small .30, medium .35, full remainder")
    training_tokens_per_second = _number(
        training_tokens_per_second, "training_tokens_per_second", positive=True)
    if cores == 0 or epochs == 0:
        raise ValueError("cores and epochs must be positive")
    measurements = [Measurement.parse(row) for row in payload["measurements"]]
    if not measurements or len({m.rung for m in measurements}) != len(measurements):
        raise ValueError("require one or more distinct measured rungs")
    by_rung = {m.rung: m for m in measurements}
    minima = dict(min_counts or {})
    maxima = dict(max_counts or {})
    for bounds in (minima, maxima):
        for rung, count in bounds.items():
            if rung not in by_rung:
                raise ValueError(f"count bound for unmeasured rung: {rung}")
            bounds[rung] = _integer(count, f"{rung} count bound")
    # APPROXIMATION: extrapolate calibration yield/time/token means to this same
    # epoch's remaining instances; this supplies no confidence interval. No
    # physical or checker rule is approximated, and acceptance gates stay intact.
    capacity = {
        m.rung: min(
            m.available_instances * m.published // m.attempted,
            maxima.get(m.rung, m.available_instances),
            full_cap if m.rung == "full" and full_cap is not None else m.available_instances,
        ) for m in measurements
    }
    counts = {m.rung: minima.get(m.rung, 0) for m in measurements}
    for rung, count in counts.items():
        if count > capacity[rung]:
            raise ValueError(f"{rung} minimum {count} exceeds estimated gold capacity {capacity[rung]}")

    def processed_tokens() -> float:
        return sum(counts[m.rung] * m.tokens * epochs for m in measurements)

    ranked = sorted(
        (m for m in measurements if m.rate > 0 and m.tokens > 0),
        key=lambda m: (-m.tokens * m.rate / m.attempt_seconds, RUNGS.index(m.rung)),
    )
    unmet_shares = []
    unmet_floors = []
    unmeasured_rungs = [rung for rung in RUNGS if rung not in by_rung]
    if pipeline_test:
        for measurement in ranked:
            remaining = target_tokens - processed_tokens()
            if remaining <= 0:
                break
            needed = math.ceil(remaining / (measurement.tokens * epochs))
            counts[measurement.rung] += min(
                needed, capacity[measurement.rung] - counts[measurement.rung])
    else:
        # D31 is a training-design constraint, never a solver-efficiency choice.
        # Tiny is a ceiling; an unused tiny allocation becomes full's remainder.
        goals = {rung: target_tokens * shares[rung] for rung in RUNGS}
        for rung in ('tiny', 'small', 'medium'):
            if rung not in by_rung:
                if rung != 'tiny':unmet_shares.append(rung)
                continue
            m = by_rung[rung]
            unit = m.tokens * epochs
            wanted = (math.floor(goals[rung] / unit) if rung == 'tiny' else math.ceil(goals[rung] / unit)) if unit else 0
            if rung == 'tiny' and counts[rung] > wanted:
                raise ValueError('tiny minimum exceeds production token-share ceiling')
            counts[rung] = min(capacity[rung], max(counts[rung], wanted))
            if rung != 'tiny' and counts[rung] * unit < goals[rung]:unmet_shares.append(rung)
        goals['full'] = max(0., target_tokens - sum(counts.get(rung, 0) * by_rung[rung].tokens * epochs for rung in ('tiny', 'small', 'medium') if rung in by_rung))
        if 'full' in by_rung:
            m = by_rung['full'];unit = m.tokens * epochs
            wanted = max(300, minima.get('full', 0), math.ceil(goals['full'] / unit) if unit else 300)
            counts['full'] = min(capacity['full'], wanted)
            if counts['full'] < 300:unmet_floors.append('full>=300')
            if counts['full'] * unit < goals['full']:unmet_shares.append('full')
        else:
            unmet_floors.append('full>=300');unmet_shares.append('full')

    rows = []
    for rung in RUNGS:
        if rung not in by_rung:
            continue
        m = by_rung[rung]
        count = counts[rung]
        # Count the already-spent calibration once; forecast only the shortfall.
        # Integer arithmetic avoids a rounded rate inventing an extra attempt.
        missing = max(0, count - m.published)
        additional_attempts = ((missing * m.attempted + m.published - 1) // m.published) if missing else 0
        attempts = m.attempted + additional_attempts
        rows.append({
            "rung": rung, "measured_attempted": m.attempted,
            "measured_published": m.published, "solve_rate": m.rate,
            "seconds_per_attempt": m.attempt_seconds,
            "prompt_tokens": m.prompt_tokens, "output_tokens": m.output_tokens,
            "available_instances": m.available_instances, "gold_capacity": capacity[rung],
            "gold_count": count, "expected_attempts": attempts,
            "additional_attempts": additional_attempts,
            "processed_tokens": count * m.tokens * epochs,
            "serial_core_seconds": attempts * m.attempt_seconds,
            "calibration_core_seconds": m.total_attempt_seconds,
            "additional_core_seconds": additional_attempts * m.attempt_seconds,
        })
    serial = sum(row["serial_core_seconds"] for row in rows)
    additional = sum(row["additional_core_seconds"] for row in rows)
    longest = max((row["seconds_per_attempt"] for row in rows if row["expected_attempts"]), default=0)
    additional_longest = max((row["seconds_per_attempt"] for row in rows if row["additional_attempts"]), default=0)
    tokens = processed_tokens()
    return {
        "label": payload["label"], "status": "ready" if tokens >= target_tokens and not unmet_shares and not unmet_floors else "insufficient_capacity",
        "data_mode": "pipeline_test" if pipeline_test else "production",
        "token_shares": shares, "unmeasured_rungs": unmeasured_rungs, "unmet_shares": unmet_shares, "unmet_floors": unmet_floors,
        "policy": "pipeline-test efficiency sizing" if pipeline_test else "D31 token shares: tiny<=10%, small30%, medium35%, full remainder with >=300 full plans; no cross-rung efficiency substitution",
        "target_tokens": target_tokens, "processed_tokens": tokens,
        "shortfall_tokens": max(0, target_tokens - tokens),
        "cores": cores, "epochs": epochs, "full_cap": full_cap, "rows": rows,
        "serial_core_seconds": serial,
        "calibration_core_seconds": serial - additional,
        "additional_core_seconds": additional,
        "ideal_parallel_wall_seconds": max(serial / cores, longest),
        "ideal_additional_wall_seconds": max(additional / cores, additional_longest),
        "training_hours": tokens / training_tokens_per_second / 3600,
        "training_tokens_per_second": training_tokens_per_second,
        "caveat": "Calibration extrapolation, not a confidence bound. Ideal CPU wall excludes overhead and assumes single-core attempts. Total time includes calibration; additional time starts after calibration. Epochs multiply training tokens only.",
    }


def example_inputs() -> dict[str, Any]:
    """Entirely synthetic planning demonstration, never current solve evidence."""
    return {
        "label": "SYNTHETIC EXAMPLE, NOT EVIDENCE; all rates, times and lengths illustrative",
        "measurements": [
            dict(rung=rung, attempted=10, published=published,
                 total_attempt_seconds=seconds, prompt_tokens=prompt,
                 output_tokens=output, available_instances=available)
            for rung, published, seconds, prompt, output, available in [
                ("tiny", 9, 20, 600, 50, 1500),
                ("small", 8, 100, 1100, 250, 1500),
                ("medium", 6, 600, 2600, 1000, 2500),
                ("full", 4, 1800, 4000, 2000, 3500),
            ]
        ],
    }


def _bounds(values: list[str]) -> dict[str, int]:
    result = {}
    for value in values:
        rung, separator, count = value.partition("=")
        if not separator or rung not in RUNGS or rung in result:
            raise ValueError("bounds must be unique RUNG=COUNT values")
        result[rung] = int(count)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--measurements", type=Path)
    source.add_argument("--example", action="store_true", help="use explicitly synthetic inputs; not evidence")
    parser.add_argument("--target-tokens", type=float, default=12_000_000)
    parser.add_argument("--cores", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=1, help="processed token target includes all epochs")
    parser.add_argument("--full-cap", type=int, default=None)
    parser.add_argument("--pipeline-test", action="store_true", help="non-production efficiency demonstration; never a production mixture")
    parser.add_argument("--token-shares", default="tiny=.10,small=.30,medium=.35,full=.25")
    parser.add_argument("--min-count", action="append", default=[], metavar="RUNG=COUNT")
    parser.add_argument("--max-count", action="append", default=[], metavar="RUNG=COUNT")
    parser.add_argument("--training-tokens-per-second", type=float, default=200,
                        help="D31 planning assumption, not a new measurement")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = example_inputs() if args.example else read_snapshot(args.measurements).json()
        result = plan_budget(payload, target_tokens=args.target_tokens, cores=args.cores,
                             min_counts=_bounds(args.min_count), max_counts=_bounds(args.max_count),
                             full_cap=args.full_cap, epochs=args.epochs, pipeline_test=args.pipeline_test,
                             token_shares={r:float(v) for r,v in (item.split("=") for item in args.token_shares.split(","))},
                             training_tokens_per_second=args.training_tokens_per_second)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.error(str(error))
    print(result["label"])
    print("rung     samples gold  yield  sec/attempt  tokens/example  capacity  selected  attempts  tokens(M)  core-h")
    for row in result["rows"]:
        print(f"{row['rung']:<8} {row['measured_attempted']:>7} {row['measured_published']:>4} "
              f"{row['solve_rate']:>6.1%} {row['seconds_per_attempt']:>11.2f} "
              f"{row['prompt_tokens'] + row['output_tokens']:>15.1f} {row['gold_capacity']:>9} "
              f"{row['gold_count']:>9} {row['expected_attempts']:>9} "
              f"{row['processed_tokens'] / 1e6:>10.3f} {row['serial_core_seconds'] / 3600:>7.2f}")
    print(f"{result['status']}: {result['processed_tokens']:,.0f}/{result['target_tokens']:,.0f} processed tokens; "
          f"shortfall {result['shortfall_tokens']:,.0f}")
    print(f"CPU total {result['serial_core_seconds'] / 3600:.2f} core-h; ideal {args.cores}-core wall "
          f"{result['ideal_parallel_wall_seconds'] / 3600:.2f} h; serial wall "
          f"{result['serial_core_seconds'] / 3600:.2f} h")
    print(f"Calibration already spent {result['calibration_core_seconds'] / 3600:.2f} core-h; "
          f"additional {result['additional_core_seconds'] / 3600:.2f} core-h; ideal additional wall "
          f"{result['ideal_additional_wall_seconds'] / 3600:.2f} h")
    print(f"SFT {result['training_hours']:.2f} GPU-h at assumed {args.training_tokens_per_second:g} tok/s; "
          f"{args.epochs} epoch(s)")
    print(result["policy"])
    print(result["caveat"])
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())

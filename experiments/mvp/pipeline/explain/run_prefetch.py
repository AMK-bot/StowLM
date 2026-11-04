"""Prepare and optionally record the explicitly named, gated lattice hero only."""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
import math
from pathlib import Path

import time

from experiments.mvp.pipeline.contract.schema import canonical_json
from . import hero as hero_source
from .cache import _boxes, estimate_cost, prefetch

HERE = Path(__file__).resolve().parent


def select_demo_boxes(hero, *, limit=10):
    """Pick cargo variety deterministically, without looking at screen outcomes."""
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('demo box limit must be an integer from 1 to 20')
    aboard = set(_boxes(hero['instance'], hero['plan'], hero['report']))
    boxes = {box['id']: box for box in hero['instance']['containers'] if box['id'] in aboard}
    chosen = []

    def add(box_id):
        if box_id in boxes and box_id not in chosen and len(chosen) < limit:
            chosen.append(box_id)

    ordered = sorted(boxes)
    for flag in ('dg', 'reefer', 'empty'):
        matching = [box for box in ordered if boxes[box].get(flag)]
        if matching:
            add(matching[0])
    for story in hero.get('story_candidates', [])[:3]:
        for key in ('dg_container_ids', 'anchor_reefer_ids', 'anchor_container_ids'):
            for box in story.get(key, [])[:2]:
                add(box)
    for pod in sorted({b['pod'] for b in boxes.values()}):
        add(next(box for box in ordered if boxes[box]['pod'] == pod))
    mass_order = sorted(ordered, key=lambda box: (boxes[box]['gross_mass_kg'], box))
    if mass_order:
        add(mass_order[0])
        add(mass_order[-1])
    for box in ordered:
        add(box)
    return chosen


def remaining_budget_usd():
    """Include retired evidence and every conservative outstanding hold."""
    from .budget import DIRECTORY
    path = DIRECTORY / '.spend.json'
    if not path.exists():
        path = HERE / 'fixtures/development/retired-cache/.spend.json'
    used = Decimal(json.loads(path.read_text())['used_usd']) if path.exists() else Decimal(0)
    if not used.is_finite() or used < 0 or used > 5:
        raise ValueError('invalid global task allocation state')
    return Decimal(5) - used


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, help='Explicit lattice hero manifest; defaults to lattice/hero.json')
    parser.add_argument('--out-dir', type=Path, default=HERE / 'fixtures/hero/cache')
    parser.add_argument('--spark', action='store_true', help='Record genuine summaries under the shared $5 total cap')
    parser.add_argument('--estimate', action='store_true', help='Project selected-box cost without screens or model calls')
    parser.add_argument('--wait-seconds', type=float, default=0, help='Wait only for publication of the named hero')
    parser.add_argument('--demo-boxes', type=int, default=10, help='Model/demo box count, 1 to 20; default 10')
    args = parser.parse_args(argv)
    if not math.isfinite(args.wait_seconds) or args.wait_seconds < 0:
        parser.error('--wait-seconds must be finite and nonnegative')
    if not 1 <= args.demo_boxes <= 20:
        parser.error('--demo-boxes must be from 1 to 20')
    deadline = time.monotonic() + args.wait_seconds
    while True:
        readiness = hero_source.prepare_hero(args.manifest, args.out_dir.parent)
        if args.out_dir == HERE / 'fixtures/hero/cache':
            (HERE / 'fixtures').mkdir(parents=True, exist_ok=True)
            (HERE / 'fixtures/readiness.json').write_text(canonical_json(readiness) + '\n')
        if readiness['status'] != 'waiting_for_named_lattice_hero' or time.monotonic() >= deadline:
            break
        time.sleep(min(5, max(0, deadline-time.monotonic())))
    if readiness['status'] != 'ready':
        print(canonical_json(readiness), flush=True)
        return 2 if readiness['status'] == 'waiting_for_named_lattice_hero' else 3
    try:
        hero = hero_source.load_hero(args.manifest)
        if hero is None or hero['binding'] != readiness['binding']:
            raise ValueError('named hero changed during preparation; run preparation again')
        selected = select_demo_boxes(hero, limit=args.demo_boxes)
        estimate = estimate_cost(hero['instance'], hero['plan'], hero['report'], model_box_ids=selected)
        remaining = remaining_budget_usd()
        projection = dict(estimate, model_box_ids=selected, demo_box_ids=selected,
                          fast_screens_per_demo_box_upper_bound=6,
                          remaining_global_budget_usd=str(remaining), task_cap_usd=5,
                          binding=hero['binding'], paid_calls_enabled=args.spark and not args.estimate)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / 'prefetch-projection.json').write_text(canonical_json(projection) + '\n')
        print(canonical_json(projection), flush=True)
        if args.estimate:
            return 0
        client = None
        if args.spark:
            if Decimal(str(estimate['projected_cost_usd'])) > remaining:
                raise ValueError('selected-box projection exceeds the remaining shared $5 allocation; no request sent')
            from llm.spark.client import SparkClient
            from .budget import task_client
            client = task_client(SparkClient(max_retries=0, timeout=45))
        index = prefetch(hero['instance'], hero['plan'], hero['report'], args.out_dir,
                         client=client, spend_cap_usd=5., demo_box_ids=selected, model_box_ids=selected)
        print(canonical_json(dict(status='prefetched', boxes=len(index['entries']),
                                  selected_demo_boxes=selected, records=index['records'],
                                  binding=hero['binding'], native_thai_reader_reviewed=False)), flush=True)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(canonical_json(dict(status='hero_prefetch_withheld', reason=str(error))), flush=True)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())

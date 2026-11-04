"""Write a UI placeholder plan (``app.backend.make_demo_plan``, deterministic first-fit) for render-scale testing.

    PYTHONPATH=.:src nice uv run python experiments/mvp/ui/make_placeholder.py \
        experiments/mvp/corpus/showcase/feeder_1700-full.json

APPROXIMATION / DEV ONLY: the placeholder is not a planner output. Its plan id ends in ``.ui-placeholder`` and the
stage bundle marks it ``meta.planner = "ui-placeholder"`` and never approved. Output goes to
``experiments/mvp/ui/placeholder-plans/<instance file stem>.plan.json`` (outside every discovery root, so the app never
picks it up as a saved plan by itself). make_demo_plan calls the move engine for every trial placement (~3 ms each),
so the full ship takes minutes.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.backend import make_demo_plan  # noqa: E402
from stowage.io import load_instance  # noqa: E402


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    source = Path(argv[0])
    out = ROOT / "experiments/mvp/ui/placeholder-plans" / (source.name.removesuffix(".json") + ".plan.json")
    started = time.monotonic()
    instance = load_instance(source)
    plan = make_demo_plan(instance)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(plan.model_dump_json())
    loads = sum(m.kind == "load" for m in plan.moves)
    print(f"{out}: {len(plan.moves)} lifts, {loads}/{len(instance.load_list)} loads, "
          f"{time.monotonic() - started:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the UI timeline precomputer with cache writes confined to showcase outputs.

Pass --run-folder even when selecting --instance/--plan explicitly. Both
--cache-dir stays inside that run; --numerical-cache-root is its named sibling.
Keeping numerical tables outside UI discovery prevents parsing large table JSON
as potential plan records.
The official UI implementation executes in-process, with exactly one worker,
so the numerical-cache redirects remain active for every computed keyframe.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
for directory in (ROOT, ROOT / "src", Path(__file__).resolve().parent):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                 "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "1"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--instance")
    parser.add_argument("--plan")
    parser.add_argument("--run-folder", required=True,
                        help="Owned output root, also required for explicit --instance/--plan")
    parser.add_argument("--entry")
    parser.add_argument("--workers", type=int, choices=(1,), default=1)
    parser.add_argument("--cache-dir", required=True, help="Physics timeline cache inside --run-folder")
    parser.add_argument("--numerical-cache-root", required=True,
                        help="Sibling RUN_NAME-numerical-cache; tables go under ROOT/cache/")
    parser.add_argument("--max-frames", type=int)
    args = parser.parse_args(argv)
    run = Path(args.run_folder).resolve()
    if not run.is_dir():
        parser.error("--run-folder must be an existing run directory")
    if not Path(args.cache_dir).resolve().is_relative_to(run):
        parser.error("--cache-dir must resolve inside --run-folder")
    expected_numerical_root = run.parent / (run.name + "-numerical-cache")
    if Path(args.numerical_cache_root).resolve() != expected_numerical_root:
        parser.error("--numerical-cache-root must be sibling RUN_NAME-numerical-cache")
    if args.max_frames is not None and args.max_frames < 0:
        parser.error("--max-frames must be nonnegative")

    from run_hero import isolate_caches
    isolate_caches(Path(args.numerical_cache_root).resolve())
    source = ROOT / "experiments/mvp/ui/precompute_timeline.py"
    specification = importlib.util.spec_from_file_location("showcase_official_timeline", source)
    if specification is None or specification.loader is None:
        raise ImportError(f"Could not load official timeline tool: {source}")
    official = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(official)
    delegated = ["--run-folder", str(run), "--workers", "1",
                 "--cache-dir", str(Path(args.cache_dir).resolve())]
    for flag, value in (("--instance", args.instance), ("--plan", args.plan),
                        ("--entry", args.entry), ("--max-frames", args.max_frames)):
        if value is not None:
            delegated += [flag, str(value)]
    return official.main(delegated)


if __name__ == "__main__":
    raise SystemExit(main())

"""Execute synthetic heat regressions against a selected source copy only."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    # Changes only this short-lived process's module binding, never live files.
    import stowage.checker
    import pytest

    name = "stowage.checker.rules_special"
    spec = importlib.util.spec_from_file_location(name, args.source.resolve())
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    stowage.checker.rules_special = module
    began = time.perf_counter()
    code = pytest.main(["-q", "-p", "no:cacheprovider", str(HERE / "test_rules_heat_ids.py"),
                        f"--junitxml={HERE / (args.name + '.xml')}"])
    result = {"source": str(args.source), "exit_code": int(code),
              "wall_s": time.perf_counter() - began,
              "kind": "synthetic static E8 checks only; no full checker/physics/solver"}
    (HERE / (args.name + ".json")).write_text(json.dumps(result, indent=2) + "\n")
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())

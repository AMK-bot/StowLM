"""Regenerate canonical fixtures with the current component versions.

From the repository root:
    uv run python data/fixtures/regenerate.py

Use --output-dir /tmp/stowage-fixtures to preview without changing stored files.
Existing fixtures retain their recorded provenance until explicitly regenerated.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from stowage.io import save_instance
from stowage.model.fixtures import build_instance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    for name, layover in (("empty_ship", False), ("layover", True)):
        path = save_instance(build_instance(layover=layover), args.output_dir / f"{name}.json")
        print(path)


if __name__ == "__main__":
    main()

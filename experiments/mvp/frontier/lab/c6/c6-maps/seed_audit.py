"""Seed-only audit: never reads map contents or enters the sealed split trees.

Run with the repository's .venv/bin/python. Reports cover every path outside
the explicitly excluded trees and selected request/cohort/split metadata.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from functools import lru_cache

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
FORBIDDEN = ("experiments/mvp/testv3", "experiments/mvp/valsplit")
EXCLUDED_COMPONENTS = {".git", ".venv", "node_modules", "__pycache__"}
KEYWORDS = re.compile(r"request|cohort|split", re.I)
METADATA_NAME = re.compile(
    r"manifest|prereg|freeze|holdout|(?:^|[._-])(?:train|test|val|validation)(?:[._-]|$)",
    re.I,
)
TEXT_SUFFIXES = {".json", ".jsonl", ".ndjson", ".txt", ".text", ".list", ".csv", ".tsv", ".yaml", ".yml", ".toml", ".md", ".py", ".lock", ".msg", ""}


def requested_seeds(rep_count: int = 2) -> list[int]:
    return sorted({243_100_000 + 100 * ship + rep + offset
                   for ship in range(3) for rep in range(rep_count)
                   for offset in (0, 1000)})


@lru_cache(maxsize=4)
def seed_pattern(seeds: tuple[int, ...]) -> re.Pattern:
    return re.compile(r"(?<![0-9])(?:" + "|".join("_?".join(str(s)) for s in seeds) + r")(?![0-9])")


def find_seeds(text: str, seeds: set[int]) -> list[int]:
    # The fixed seed prefix cheaply rejects nearly every metadata file/path.
    if not any(prefix in text for prefix in ("243", "2_4", "24_3")):
        return []
    # A C-level substring search avoids regex work across large numeric
    # metadata snapshots when none of the exact target seeds is present.
    normalized = text.replace("_", "")
    if not any(str(seed) in normalized for seed in seeds):
        return []
    return sorted({int(m.group(0).replace("_", ""))
                   for m in seed_pattern(tuple(sorted(seeds))).finditer(text)})


def metadata_candidate(relative: str) -> bool:
    parts = relative.split("/")
    name = parts[-1]
    suffix = os.path.splitext(name)[1].lower()
    if suffix not in TEXT_SUFFIXES:
        return False
    # Basename matches include requests, split locks, cohort definitions, etc.
    # Generic manifests and freezes capture split metadata with arbitrary names.
    # Text lists inside cohort/split directories can also have arbitrary names.
    return bool(KEYWORDS.search(name) or METADATA_NAME.search(name)
                or (suffix in {".txt", ".text", ".list", ".csv", ".tsv", ".yaml", ".yml", ".toml"}
                    and any(KEYWORDS.search(part) for part in parts[:-1]))
                or ("splits" in parts[:-1] and suffix in {".json", ".jsonl", ".ndjson"}))


def audit(root: Path = ROOT, rep_count: int = 2) -> dict:
    root = root.resolve()
    own = HERE.relative_to(root).as_posix()
    excluded = [*FORBIDDEN, own]
    seeds = set(requested_seeds(rep_count))
    files = []
    collisions = []
    errors = []
    path_count = 0
    referenced_forbidden = []
    counts = Counter()
    for directory, dirs, names in os.walk(root, followlinks=False):
        relative_dir = os.path.relpath(directory, root)
        dirs[:] = sorted(d for d in dirs
                         if d not in EXCLUDED_COMPONENTS
                         and not os.path.islink(os.path.join(directory, d))
                         and os.path.normpath(os.path.join(relative_dir, d)) not in excluded)
        for name in sorted(names):
            path = os.path.join(directory, name)
            relative = os.path.normpath(os.path.join(relative_dir, name))
            path_count += 1
            if path_count % 10000 == 0:
                print(f"seed audit: {path_count} paths, {len(files)} metadata files", file=sys.stderr, flush=True)
            hits = find_seeds(relative, seeds)
            if hits:
                collisions.append({"path": relative, "source": "path", "seeds": hits})
            if not metadata_candidate(relative):
                continue
            if os.path.islink(path):
                continue
            try:
                with open(path, "rb") as handle:
                    content = handle.read()
                # Metadata is text; binary data with a matching filename is
                # hashed and reported but never interpreted as text.
                decoded = content.decode("utf-8")
            except (OSError, UnicodeError) as exc:
                errors.append({"path": relative, "error": type(exc).__name__})
                continue
            hits = find_seeds(decoded, seeds)
            if hits:
                collisions.append({"path": relative, "source": "metadata", "seeds": hits})
            references = [prefix for prefix in FORBIDDEN if prefix in decoded]
            if references:
                referenced_forbidden.append({"path": relative, "references": references})
            files.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
            counts["request" if "request" in name.lower() else
                   "cohort" if "cohort" in name.lower() else
                   "split" if "split" in name.lower() else "other_metadata"] += 1
    return {
        "schema": "c6-seed-audit-v1", "seeds": sorted(seeds),
        "audit_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "rep_count": rep_count, "disjoint": not collisions and not errors,
        "scope": "All repository file paths and request/cohort/split metadata selected by metadata_candidate; map contents are not inspected.",
        "forbidden_paths": list(FORBIDDEN),
        "limitation": "Forbidden testv3/ and valsplit/ trees were not entered or opened. Their contents are not directly certified; only external metadata references are covered.",
        "excluded_components": sorted(EXCLUDED_COMPONENTS), "own_folder_excluded": own,
        "paths_checked": path_count, "metadata_files_checked": len(files),
        "metadata_categories": dict(sorted(counts.items())),
        "external_forbidden_references": referenced_forbidden,
        "collisions": collisions, "errors": errors,
        "files": sorted(files, key=lambda entry: entry["path"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rep-count", type=int, default=2)
    parser.add_argument("--output", type=Path, default=HERE / "SEED-AUDIT.json")
    args = parser.parse_args()
    if args.rep_count < 2 or args.rep_count >= 100:
        parser.error("rep-count must be in [2, 99]")
    if args.output.resolve().parent != HERE:
        parser.error("output must be directly inside c6-maps")
    result = audit(rep_count=args.rep_count)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("disjoint", "seeds", "paths_checked", "metadata_files_checked", "collisions", "errors")}))
    raise SystemExit(0 if result["disjoint"] else 1)


if __name__ == "__main__":
    main()

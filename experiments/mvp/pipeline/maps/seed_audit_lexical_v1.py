"""Audit proposed seeds against prior paths and metadata, without reading maps.

Adapted from frontier/lab/c6/c6-maps/seed_audit.py.  Ignored and untracked
experiment files are included: this deliberately uses os.walk, not git's index.
Forbidden raw split trees are pruned before traversal; references to them in
external cohort, request, manifest, and set-list metadata remain auditable.
"""
from __future__ import annotations

import argparse
from collections import Counter
from functools import lru_cache
import hashlib
from itertools import product
import json
import os
from pathlib import Path
import re
import time
from typing import Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FORBIDDEN = (
    "experiments/mvp/testv3",
    "experiments/mvp/valsplit",
    "data/raw/rcsppsuite",
    "refs_tools/small-v21-instances.txt",
)
FORBIDDEN_RUN_PREFIXES = ("test-0.9", "unseen15-0.9")
# Tool environments contain package fixtures rather than experiment cohorts.
EXCLUDED_COMPONENTS = {
    ".git", ".venv", ".uv-cache", "node_modules", "__pycache__", ".pytest_cache",
}
TEXT_SUFFIXES = {
    ".json", ".jsonl", ".ndjson", ".txt", ".text", ".list", ".csv",
    ".tsv", ".yaml", ".yml", ".toml", ".md", ".py", ".lock", ".msg", "",
}
KEYWORDS = re.compile(r"request|cohort|split", re.I)
METADATA_NAME = re.compile(
    r"manifest|prereg|freeze|holdout|(?:^|[._-])(?:train|test|val|validation)(?:[._-]|$)",
    re.I,
)
RAW_INSTANCE_NAME = re.compile(r"(?:seed[-_.]?|(?:^|[._-])s)\d|(?:random|simulated)\.(?:tiny|small|medium)", re.I)
SEALED_COMPONENT = re.compile(r"^(?:sealed|holdout|testv3|valsplit|test[-_]v2(?:[._-]1)?)$", re.I)


def requested_seeds() -> list[int]:
    """Fixed 32 development and 24 sealed seeds, in the preregistered grid."""
    return sorted(306_100_000 + offset + 100 * cell + rep
                  for offset, repetitions in ((0, 4), (10_000, 3))
                  for cell in range(8) for rep in range(repetitions))


@lru_cache(maxsize=8)
def seed_pattern(seeds: tuple[int, ...]) -> re.Pattern:
    return re.compile(r"(?<![0-9])(?:" + "|".join(
        "_?".join(str(seed)) for seed in seeds) + r")(?![0-9])")


def find_seeds(text: str, seeds: set[int]) -> list[int]:
    if not seeds:
        return []
    return _detector(seeds)(text)


def _detector(seeds: set[int]):
    """Precompute the fast prefix rejection once for large path inventories."""
    numbers = tuple(str(seed) for seed in sorted(seeds))
    prefix = os.path.commonprefix(numbers)
    pattern = seed_pattern(tuple(sorted(seeds)))
    prefix_forms = [''.join(digit + ('_' if gap else '')
                           for digit, gap in zip(prefix[:-1], gaps)) + prefix[-1]
                    for gaps in product((False, True), repeat=len(prefix)-1)] if 0 < len(prefix) <= 6 else []

    def detect(text: str) -> list[int]:
        if prefix_forms:
            # Find the shared prefix once per occurrence, rather than scanning
            # each multi-hundred-MB metadata file separately for all 56 roots.
            found = set()
            for form in prefix_forms:
                position = text.find(form)
                while position >= 0:
                    match = pattern.match(text, position)
                    if match:
                        found.add(int(match.group().replace('_', '')))
                    position = text.find(form, position + 1)
            return sorted(found)
        normalized = text.replace("_", "")
        if prefix and prefix not in normalized:
            return []
        if not any(number in normalized for number in numbers):
            return []
        return sorted({int(match.group().replace("_", ""))
                       for match in pattern.finditer(text)})

    return detect


def metadata_candidate(relative: str) -> bool:
    """Select external metadata, never a seed-named or sealed raw map."""
    parts = relative.split("/")
    name = parts[-1]
    suffix = Path(name).suffix.lower()
    if _forbidden(relative) or suffix not in TEXT_SUFFIXES or RAW_INSTANCE_NAME.search(name):
        return False
    explicit = bool(KEYWORDS.search(name) or METADATA_NAME.search(name))
    # A raw map filename can contain 'test' or 'holdout'. In map/raw/instance
    # trees only unambiguous metadata basenames may be opened.
    raw_tree = any(part.lower() in {"maps", "parents", "instances", "raw"}
                   or SEALED_COMPONENT.match(part) for part in parts[:-1])
    if raw_tree:
        return bool(re.search(r"request|cohort|split|manifest|prereg|freeze", name, re.I))
    return bool(explicit
                or (suffix in {".txt", ".text", ".list", ".csv", ".tsv", ".yaml", ".yml", ".toml"}
                    and any(KEYWORDS.search(part) or part.lower() == "sets" for part in parts[:-1]))
                or (any(part.lower() in {"split", "splits", "sets"} for part in parts[:-1])
                    and suffix in {".json", ".jsonl", ".ndjson"}))


def _forbidden(relative: str) -> bool:
    parts = relative.split("/")
    return (any(relative == prefix or relative.startswith(prefix + "/") for prefix in FORBIDDEN)
            or any(parts[index] == "runs" and parts[index + 1].startswith(FORBIDDEN_RUN_PREFIXES)
                   for index in range(len(parts) - 1)))


def audit(seeds: Iterable[int] | None = None, *, root: Path = ROOT) -> dict:
    """Return disjointness and hashed evidence; never load any map content.

    A clean result certifies accessible path/metadata evidence only. Forbidden
    split contents cannot be directly certified and that limitation is explicit.
    """
    root = Path(root).resolve()
    proposed = list(requested_seeds() if seeds is None else seeds)
    if not proposed or any(type(seed) is not int or seed < 0 for seed in proposed):
        raise ValueError("seeds must be a nonempty sequence of nonnegative integers")
    seed_set = set(proposed)
    detect = _detector(seed_set)
    collisions, errors, files, external_refs = [], [], [], []
    if len(proposed) != len(seed_set):
        errors.append({"path": "<requested seeds>", "error": "duplicate proposed seeds"})
    own = HERE.relative_to(root).as_posix() if HERE.is_relative_to(root) else None
    path_count, path_hash, categories = 0, hashlib.sha256(), Counter()
    skipped = Counter()
    last_progress = time.monotonic()

    def walk_error(error: OSError) -> None:
        errors.append({"path": str(error.filename), "error": type(error).__name__})

    for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        if time.monotonic() - last_progress >= 15:
            print(f'Seed audit: {path_count} paths, {len(files)} metadata files', flush=True)
            last_progress = time.monotonic()
        relative_dir = Path(directory).relative_to(root)
        retained = []
        for name in sorted(dirs):
            relative = (relative_dir / name).as_posix()
            if name in EXCLUDED_COMPONENTS:
                skipped["tool_environment_directories"] += 1
            elif _forbidden(relative):
                skipped["forbidden_directories"] += 1
            elif own and relative == own:
                skipped["new_cohort_directory"] += 1
            elif Path(directory, name).is_symlink():
                skipped["symlink_directories"] += 1
            else:
                retained.append(name)
        dirs[:] = retained
        for name in sorted(names):
            relative = (relative_dir / name).as_posix()
            path_count += 1
            path_hash.update(relative.encode("utf-8", "surrogateescape") + b"\n")
            hits = detect(relative)
            if hits:
                collisions.append({"path": relative, "source": "path", "seeds": hits})
            if not metadata_candidate(relative):
                continue
            path = Path(directory, name)
            if path.is_symlink():
                skipped["symlink_metadata_files"] += 1
                continue
            try:
                content = path.read_bytes()
                decoded = content.decode("utf-8")
            except (OSError, UnicodeError) as error:
                errors.append({"path": relative, "error": type(error).__name__})
                continue
            hits = detect(decoded)
            if hits:
                collisions.append({"path": relative, "source": "metadata", "seeds": hits})
            references = [prefix for prefix in FORBIDDEN if prefix in decoded]
            if references:
                external_refs.append({"path": relative, "references": references})
            files.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
            categories["request" if "request" in name.lower() else "cohort" if "cohort" in name.lower()
                       else "split" if "split" in name.lower() else "other_metadata"] += 1
    files.sort(key=lambda item: item["path"])
    metadata_hash = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema": "pipeline-envelope-seed-audit-v1", "seeds": sorted(seed_set),
        "disjoint": not collisions and not errors,
        "audit_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "All accessible repository file paths and selected request/cohort/split/manifest/set metadata, including ignored and untracked files; no map contents.",
        "limitation": "Forbidden testv3, valsplit and raw rcsppsuite trees are not entered; their contents are not directly certified. External metadata references are covered. Tool environments, symlinks and this new cohort folder are excluded.",
        "forbidden_paths": list(FORBIDDEN), "own_folder_excluded": own,
        "forbidden_run_patterns": ["**/runs/" + prefix + "*" for prefix in FORBIDDEN_RUN_PREFIXES],
        "excluded_components": sorted(EXCLUDED_COMPONENTS), "skipped": dict(sorted(skipped.items())),
        "paths_checked": path_count, "metadata_files_checked": len(files),
        "path_inventory_sha256": path_hash.hexdigest(), "metadata_inventory_sha256": metadata_hash,
        "metadata_categories": dict(sorted(categories.items())),
        "external_forbidden_references": external_refs,
        "collisions": collisions, "errors": errors, "evidence": collisions, "files": files,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", nargs="+", type=int, default=None)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and not args.output.resolve().is_relative_to(HERE):
        parser.error("output must stay under experiments/mvp/pipeline/maps/")
    result = audit(args.seeds)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in (
        "disjoint", "seeds", "paths_checked", "metadata_files_checked",
        "path_inventory_sha256", "metadata_inventory_sha256", "collisions", "errors")}))
    raise SystemExit(0 if result["disjoint"] else 1)


if __name__ == "__main__":
    main()

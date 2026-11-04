"""Fit-only membership gate, following corpus_tools.freeze seed reservations.

Requested inventories bind complete configs to canonical paths. Test and every
showcase draw reserve their root seeds, including unselected draws. Feasibility
does not establish disjointness: IDs and exact instance bytes are checked too.
This module has no solver imports and is reusable for existing-gold audits.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections import OrderedDict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
from types import ModuleType


def _read(path):
    return json.loads(Path(path).read_text())


def _require(test, message):
    if not test:
        raise ValueError(f"Split admission: {message}")


def _canonical(root, config):
    _require(config.get("split") in {"train", "val", "test"}, "unknown requested split")
    _require(type(config.get("seed")) is int and config["seed"] >= 0, "invalid seed")
    for field in ("rung", "vessel", "generator"):
        _require(isinstance(config.get(field), str) and config[field] not in {"", ".", ".."}
                 and "/" not in config[field], f"invalid {field}")
    return root / config["split"] / config["rung"] / config["vessel"] / config["generator"] / f"seed-{config['seed']:09d}.json"


def _reservations(value, seeds, ids, hashes):
    if isinstance(value, list):
        for child in value:
            _reservations(child, seeds, ids, hashes)
    elif isinstance(value, dict):
        for key, child in value.items():
            if key == "seed" and type(child) is int:
                seeds.add(child)
            elif key == "instance_id" and isinstance(child, str):
                ids.add(child)
            elif key in {"instance_sha256", "sha256"} and isinstance(child, str):
                hashes.add(child)
            elif key == "hashes" and isinstance(child, dict):
                # Metadata digests cannot equal instance bytes accidentally;
                # keeping all declared hashes is conservative.
                hashes.update(v for v in child.values() if isinstance(v, str))
            else:
                _reservations(child, seeds, ids, hashes)


@contextmanager
def _identity_cache(output):
    if output is None:
        yield {}
        return
    directory = Path(output) / ".split-admission"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "index.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        path = directory / "index.json"
        cache = _read(path) if path.exists() else {}
        yield cache
        temporary = directory / f".index.{os.getpid()}.partial"
        temporary.write_text(json.dumps(cache, sort_keys=True))
        temporary.replace(path)


def _identity(path, cache):
    path = Path(path)
    stat = path.stat()
    key = str(path.resolve())
    pin = [stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino]
    prior = cache.get(key)
    if prior and prior.get("stat") == pin:
        return prior
    raw = path.read_bytes()
    instance = json.loads(raw)
    value = dict(stat=pin, instance_id=instance.get("id"), seed=instance.get("seed"),
                 sha256=hashlib.sha256(raw).hexdigest())
    cache[key] = value
    return value


def _output_identity(path):
    """Reuse only tiny, stat-bound output identities across helper reimports.

    Candidate inputs/audits are deliberately outside this cache. Retain no
    source pins or other large result metadata. Re-stat every output on every
    admission, including failed records, so in-place edits and atomic replaces
    invalidate identities immediately.
    """
    name = "_stowlm_gold_output_identity_cache_v1"
    state = sys.modules.get(name)
    if state is None:
        fresh = ModuleType(name)
        fresh.entries = OrderedDict()
        fresh.lock = threading.RLock()
        state = sys.modules.setdefault(name, fresh)
    path = Path(path)
    key = str(path.resolve())

    def fingerprint():
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)

    with state.lock:
        before = fingerprint()
        prior = state.entries.get(key)
        if prior is not None and prior[0] == before:
            state.entries.move_to_end(key)
            return prior[1]
        for _ in range(3):
            row = _read(path)
            after = fingerprint()
            if before == after:
                summary = {"instance_id": row.get("instance_id"), "input_sha256": row.get("input_sha256")}
                state.entries[key] = (after, summary)
                state.entries.move_to_end(key)
                while len(state.entries) > 4096:
                    state.entries.popitem(last=False)
                return summary
            before = after
        raise ValueError(f"Split admission: output changed while reading: {path}")


def verify_candidate(input_path, corpus_root, output=None):
    """Raise ValueError for leakage, duplicates, or relabelled/unrequested input.

    Cache writes (when enabled) contain only measured file identities and are
    serialized across gold workers. Every candidate itself is freshly hashed.
    """
    root = Path(corpus_root).resolve()
    path = Path(os.path.abspath(input_path))
    _require(path == path.resolve() and path.is_relative_to(root), "noncanonical or escaping input path")
    relative = path.relative_to(root)
    _require(len(relative.parts) == 5 and relative.parts[0] in {"train", "val"}, "candidate is not train/val")
    split = relative.parts[0]
    audit = _read(path.with_suffix(".audit.json"))
    config = audit.get("config", {})
    _require(_canonical(root, config) == path and config["split"] == split, "path/config identity mismatch")
    inventory_path = root / f"{split}.requested.json"
    _require(inventory_path.is_file(), "missing requested inventory")
    specs = _read(inventory_path).get("specs", [])
    matches = [spec for spec in specs if _canonical(root, spec) == path]
    _require(len(matches) == 1 and matches[0] == config, "complete audit config is not in requested inventory")
    seeds, ids, hashes = set(), set(), set()
    test_request = root / "test.requested.json"
    if test_request.exists():
        _reservations(_read(test_request), seeds, ids, hashes)
    for document in [root / "releases.json", root / "showcase/showcase-manifest.json",
                     *sorted((root / "showcase").rglob("*.audit.json")),
                     *sorted((root / "showcase").rglob("*.selection.json"))]:
        if document.exists():
            _reservations(_read(document), seeds, ids, hashes)
    manifest = root / "manifest.json"
    if manifest.exists():
        payload = _read(manifest)
        for row in payload.get("files", []):
            if row.get("split") in {"test", "showcase"}:
                _reservations(row, seeds, ids, hashes)
        for heldout in ("test", "showcase"):
            seeds.update(payload.get("seeds", {}).get(heldout, []))
    _require(config["seed"] not in seeds, "root seed reserved for test/showcase draw")
    # Inventory reservation collisions are invalid even before generation.
    for other_split in ("train", "val"):
        request = root / f"{other_split}.requested.json"
        if request.exists():
            for spec in _read(request).get("specs", []):
                if spec.get("seed") == config["seed"]:
                    _require(_canonical(root, spec) == path and spec == config,
                             "root seed reused by another requested fit instance")
    with _identity_cache(output) as cache:
        # Do not trust the cache for the candidate, even if stat metadata agrees.
        cache.pop(str(path), None)
        candidate = _identity(path, cache)
        _require(candidate["instance_id"] is not None, "instance ID missing")
        _require(candidate["seed"] == config["seed"], "instance/audit seed mismatch")
        _require(audit.get("accepted") is True and audit.get("rule_freeze_v1_enabled") is False
                 and audit.get("instance_sha256") == candidate["sha256"], "accepted OFF audit checksum missing/mismatched")
        for cohort in ("test", "showcase", "train", "val"):
            for sidecar in sorted((root / cohort).rglob("*.audit.json")):
                other = sidecar.with_name(sidecar.name.removesuffix(".audit.json") + ".json")
                if not other.exists() or other == path or any(part.startswith(".") for part in other.relative_to(root).parts):
                    continue
                identity = _identity(other, cache)
                if cohort in {"test", "showcase"}:
                    ids.add(identity["instance_id"])
                    hashes.add(identity["sha256"])
                    seeds.add(identity["seed"])
                else:
                    _require(identity["instance_id"] != candidate["instance_id"], f"duplicate fit instance ID: {other}")
                    _require(identity["sha256"] != candidate["sha256"], f"duplicate fit instance bytes: {other}")
        _require(candidate["instance_id"] not in ids, "instance ID reserved for test/showcase")
        _require(candidate["sha256"] not in hashes, "instance bytes reserved for test/showcase")
        _require(candidate["seed"] not in seeds, "root seed reserved by actual heldout instance")
    if output is not None:
        expected_result = Path(output) / relative.with_suffix("") / "result.json"
        # Only active fitting cohorts own gold identities. Runtime snapshots
        # and quarantined historical failures are not active output records.
        for cohort in ("train", "val"):
            for record_path in (Path(output) / cohort).rglob("result.json"):
                if record_path == expected_result:
                    continue
                row = _output_identity(record_path)
                _require(row.get("instance_id") != candidate["instance_id"], f"duplicate existing-gold ID: {record_path}")
                _require(row.get("input_sha256") != candidate["sha256"], f"duplicate existing-gold bytes: {record_path}")
    return dict(split=split, seed=config["seed"], config=config,
                instance_id=candidate["instance_id"], input_sha256=candidate["sha256"])

"""Strict measurement compatibility. Historical evidence is never upgraded."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import sys
import types

VERSION = 2


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _code_value(value):
    """Canonical code constants, independent of hash-seed/frozenset order."""
    if isinstance(value, types.CodeType):
        return {"code": value.co_code.hex(), "constants": [_code_value(item) for item in value.co_consts],
                "names": list(value.co_names), "variables": list(value.co_varnames),
                "free": list(value.co_freevars), "cells": list(value.co_cellvars),
                "flags": value.co_flags, "args": value.co_argcount,
                "posonly": value.co_posonlyargcount, "kwonly": value.co_kwonlyargcount,
                "stacksize": value.co_stacksize, "exceptions": value.co_exceptiontable.hex()}
    if isinstance(value, (tuple, frozenset)):
        items = [_code_value(item) for item in value]
        return {type(value).__name__: sorted(items, key=digest) if isinstance(value, frozenset) else items}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, float):
        return {"float": value.hex()}
    if isinstance(value, complex):
        return {"complex": [value.real.hex(), value.imag.hex()]}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return {"constant_type": type(value).__name__, "value": repr(value)}


def implementation_identity(binding_root):
    """Hash every binding Python file/pins plus already-loaded callable code."""
    root = Path(binding_root)
    sources = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(root.glob("*.py"))}
    pins = root / "provenance-pins.json"
    if pins.exists():
        sources[pins.name] = hashlib.sha256(pins.read_bytes()).hexdigest()
    # Stabilize the set of loaded production modules across a script runner and
    # a later summary process. Tests are source-hashed but not imported here.
    for path in sorted(root.glob("*.py")):
        if not path.name.startswith("test_") and path.stem != "__init__":
            importlib.import_module("experiments.mvp.pipeline.binding." + path.stem)
    code = {}
    for name, module in list(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if not filename or Path(filename).resolve().parent != root.resolve():
            continue
        if Path(filename).name.startswith("test_"):
            continue
        for symbol, value in sorted(vars(module).items()):
            candidates = [(symbol, value)]
            if inspect.isclass(value) and getattr(value, "__module__", None) == name:
                candidates += [(symbol + "." + key, item.__func__ if isinstance(item, (staticmethod, classmethod)) else item)
                               for key, item in vars(value).items()]
            for label, item in candidates:
                if getattr(item, "__module__", None) != name:
                    continue
                layers = []
                seen = set()
                while hasattr(item, "__code__") and id(item) not in seen:
                    seen.add(id(item))
                    layers.append(digest(_code_value(item.__code__)))
                    item = getattr(item, "__wrapped__", None)
                if layers:
                    code[Path(filename).name + "." + label] = layers
    return dict(source_files=sources, source_sha256=digest(sources),
                loaded_code_sha256=digest(code))


def resolved_settings(instance, config, budget_s, finalize_s):
    from engine.config import Config
    actual = Config() if config is None else config
    load = len(instance.load_list)
    total = load + len(getattr(getattr(instance, "onboard", None), "placements", ()))
    finish = 600. if finalize_s is None else float(finalize_s)
    return dict(config=asdict(actual) if is_dataclass(actual) else dict(actual),
                budget_s=(120. if total <= 60 else 900.) if budget_s is None else float(budget_s),
                finalize_s=finish, effective_finalize_s=max(300., finish) if load <= 60 else finish,
                budget_semantics="extra work; original <=60-load historical floor retained")


def build_identity(*, input_path, cell, profile, rule, settings, engine_identity,
                   implementation, seed=0):
    path = Path(input_path).resolve()
    raw = path.read_bytes()
    payload = dict(version=VERSION, input_path=str(path), input_sha256=hashlib.sha256(raw).hexdigest(),
                   input_bytes=len(raw), cell=cell, profile=profile, canonical_rule=rule,
                   seed=seed, resolved_settings=settings, engine=engine_identity,
                   implementation=implementation)
    return dict(payload=payload, sha256=digest(payload))


def compatibility(row, expected):
    found = row.get("measurement_identity")
    if not isinstance(found, dict):
        return False, ["legacy row lacks enforced measurement identity"]
    payload = found.get("payload")
    if not isinstance(payload, dict) or found.get("sha256") != digest(payload):
        return False, ["stored measurement identity is malformed"]
    if expected.get("sha256") != digest(expected["payload"]):
        raise ValueError("invalid expected measurement identity")
    if found == expected:
        return True, []
    keys = sorted(set(payload) | set(expected["payload"]))
    return False, [key + " changed" for key in keys if payload.get(key) != expected["payload"].get(key)]


def attempt_key(identity, attempt_id):
    return identity["sha256"][:24] + "-" + attempt_id

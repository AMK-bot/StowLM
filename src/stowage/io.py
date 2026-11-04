"""Deterministic UTF-8 JSON I/O for the frozen data contracts (owner 1.1).

Files include a top-level schema version and reject incompatible major versions.
PlannerResult has no version field in the frozen model, so its file uses a versioned
JSON envelope (the other result fields remain at the top level). ``.json.gz`` files
are transparent, with a fixed gzip timestamp so equal objects have identical bytes.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from stowage.frozen import frozen, require_clean
from stowage.model import Instance, MovePlan, PlannerResult, Provenance
from stowage.versions import SCHEMA_VERSION

_Model = TypeVar("_Model", bound=BaseModel)


class SchemaVersionError(ValueError):
    """The file's schema_version major differs from ``stowage.versions.SCHEMA_VERSION``."""


def _check_version(version: Any) -> None:
    if not isinstance(version, str) or not version.split(".")[0].isdigit():
        raise SchemaVersionError(f"missing or malformed schema_version: {version!r}")
    if int(version.split(".")[0]) != int(SCHEMA_VERSION.split(".")[0]):
        raise SchemaVersionError(
            f"incompatible schema_version {version!r}; expected major {SCHEMA_VERSION.split('.')[0]}"
        )


def _read(path: str | Path, model: type[_Model]) -> _Model:
    path = Path(path)
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    payload = json.loads(data.decode("utf-8"))
    _check_version(payload.get("schema_version") if isinstance(payload, dict) else None)
    if model is PlannerResult:
        payload.pop("schema_version")
        later_plans = payload.get("later_plans", [])
        plans = [payload.get("plan")]
        if isinstance(later_plans, list):
            plans.extend(later_plans)
        for plan in plans:
            if isinstance(plan, dict):
                _check_version(plan.get("schema_version"))
    return model.model_validate(payload)


def _require_clean_tree(value: Any, *, what: str) -> None:
    """Check every provenance, including later plans and nested result artifacts."""
    if isinstance(value, Provenance):
        require_clean(value, what=what)
    elif isinstance(value, BaseModel):
        if isinstance(getattr(value, "provenance", None), Provenance):
            require_clean(value.provenance, what=what)
        for name in type(value).model_fields:
            _require_clean_tree(getattr(value, name), what=what)
    elif isinstance(value, dict):
        for item in value.values():
            _require_clean_tree(item, what=what)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _require_clean_tree(item, what=what)


def _write(value: BaseModel, path: str | Path, *, allow_stubs: bool, what: str) -> Path:
    if not allow_stubs:
        _require_clean_tree(value, what=what)
    # Pydantic handles enums, dates and configured non-finite number encoding first;
    # sorting JSON dictionary keys also canonicalizes arbitrary action/metric maps.
    payload = json.loads(value.model_dump_json())
    payload.setdefault("schema_version", SCHEMA_VERSION)
    _check_version(payload["schema_version"])
    if isinstance(value, PlannerResult):
        for plan in [value.plan, *value.later_plans]:
            if plan is not None:
                _check_version(plan.schema_version)
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    path = Path(path)
    if path.suffix == ".gz":
        data = gzip.compress(data, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@frozen("io.load_instance", owner="1.1", implemented=True)
def load_instance(path: str | Path) -> Instance:
    """Read an Instance; reject incompatible schema majors and invalid model data."""
    return _read(path, Instance)


@frozen("io.save_instance", owner="1.1", implemented=True)
def save_instance(instance: Instance, path: str | Path, *, allow_stubs: bool = False) -> Path:
    """Write an Instance, creating parents; refuse stub taint unless explicitly allowed."""
    return _write(instance, path, allow_stubs=allow_stubs, what="save_instance")


@frozen("io.load_plan", owner="1.1", implemented=True)
def load_plan(path: str | Path) -> MovePlan:
    """Read a MovePlan; reject incompatible schema majors and invalid model data."""
    return _read(path, MovePlan)


@frozen("io.save_plan", owner="1.1", implemented=True)
def save_plan(plan: MovePlan, path: str | Path, *, allow_stubs: bool = False) -> Path:
    """Write a MovePlan, refusing stub-tainted provenance unless explicitly allowed."""
    return _write(plan, path, allow_stubs=allow_stubs, what="save_plan")


@frozen("io.load_result", owner="1.1", implemented=True)
def load_result(path: str | Path) -> PlannerResult:
    """Read a PlannerResult from its top-level versioned JSON envelope."""
    return _read(path, PlannerResult)


@frozen("io.save_result", owner="1.1", implemented=True)
def save_result(result: PlannerResult, path: str | Path, *, allow_stubs: bool = False) -> Path:
    """Write a versioned result; check result, current plan and all later plans for taint."""
    return _write(result, path, allow_stubs=allow_stubs, what="save_result")

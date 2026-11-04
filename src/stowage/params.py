"""Parameter files loader: ``params/<kind>/*.toml`` at the repo root (plan 0.9; leader decision).

Kinds: ``cargo`` (1.4), ``ports`` (1.5), ``fleet`` (1.6). Format (see params/README.md): any numeric value is a
table ``{ value = ..., unit = "...", source = "..." }`` or ``{ value = ..., unit = "...", assumption = "..." }``.
Read with stdlib ``tomllib``. These helpers are real code (not stubs); kind-specific typed views are built by the
consumers (2.2 fleet, 2.5 cargo, 2.6 ports).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from stowage.toml_cache import load as load_toml

ParamKind = Literal["cargo", "ports", "fleet"]
PARAM_KINDS: tuple[str, ...] = ("cargo", "ports", "fleet")


def repo_root() -> Path:
    """The repository root (the folder holding ``pyproject.toml``), found upward from this file."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise FileNotFoundError("repo root (pyproject.toml) not found above " + str(here))


def params_dir(kind: ParamKind | None = None) -> Path:
    base = repo_root() / "params"
    return base / kind if kind else base


def list_param_files(kind: ParamKind) -> list[str]:
    """Names (file stems) of the TOML files of ``kind``, sorted."""
    _check_kind(kind)
    d = params_dir(kind)
    return sorted(p.stem for p in d.glob("*.toml")) if d.is_dir() else []


def load_param_file(kind: ParamKind, name: str) -> dict[str, Any]:
    """Parse ``params/<kind>/<name>.toml`` and return the raw dict. Raises FileNotFoundError / tomllib.TOMLDecodeError."""
    _check_kind(kind)
    return load_toml(params_dir(kind) / f"{name}.toml")


@dataclass(frozen=True)
class Quantity:
    """A parameter value with unit and provenance (exactly one of ``source`` / ``assumption`` is set)."""

    value: Any
    unit: str
    source: str | None
    assumption: str | None


def is_quantity(node: Any) -> bool:
    return isinstance(node, dict) and "value" in node and "unit" in node


def quantity(node: Any) -> Quantity:
    """Interpret a ``{value, unit, source|assumption}`` table. Raises ValueError if unit or provenance is missing."""
    if not is_quantity(node):
        raise ValueError(f"not a quantity table (needs value + unit): {node!r}")
    src, asm = node.get("source"), node.get("assumption")
    if not (src or asm):
        raise ValueError(f"quantity without source or assumption: {node!r}")
    return Quantity(node["value"], str(node["unit"]), src, asm)


def unsourced_numbers(tree: Any, path: str = "") -> list[str]:
    """Dotted paths of bare numbers (not inside a quantity table) or quantity tables lacking provenance — for the
    params tests ("every number carries its source or an assumption label")."""
    bad: list[str] = []
    if is_quantity(tree):
        if not (tree.get("source") or tree.get("assumption")):
            bad.append(path)
        return bad
    if isinstance(tree, dict):
        for k, v in tree.items():
            bad += unsourced_numbers(v, f"{path}.{k}" if path else k)
    elif isinstance(tree, list):
        for i, v in enumerate(tree):
            bad += unsourced_numbers(v, f"{path}[{i}]")
    elif isinstance(tree, (int, float)) and not isinstance(tree, bool):
        bad.append(path)
    return bad


def _check_kind(kind: str) -> None:
    if kind not in PARAM_KINDS:
        raise ValueError(f"unknown params kind {kind!r}; expected one of {PARAM_KINDS}")

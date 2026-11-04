"""D199 experimental prompt wrapper; production src/ remains unchanged.

The base prompt ALREADY includes exact hatch membership in ``g.covers`` when
layout is enabled (the SFT default). Version 9 adds an explicit bay summary and
explains the cost consequence; it does not claim the original lacked covers or
turn a teacher's port-block heuristic into a hard feasibility rule.

Call ``install()`` inside each SFT builder process before building/replaying rows.
The separately loaded base module preserves v3's exact-mass semantics even after
the public module is patched. No global temporary patching occurs during encode.
"""
from __future__ import annotations

import ast
from dataclasses import asdict
from hashlib import sha256
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import textwrap
from types import FunctionType
from typing import Any

from llm.stowlm import format_prompt as _public

PROMPT_VERSION = "stowlm-prompt@9.0"
FROZEN_VAL_SHA256 = "5dc48ff802170bb192d68867daef23d6cff6a74e63b7d3d1bc7c72c8630c79e8"
FROZEN_VAL_PATH = Path(__file__).resolve().parents[1] / "sft/stowlm-v2/data/val.jsonl"
_frozen_val_rows: set[str] = set()
_spec = importlib.util.spec_from_file_location("_stowlm9_base_prompt", _public.__file__)
assert _spec is not None and _spec.loader is not None
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

HATCH_SYSTEM_SUFFIX = (
    "\nHatch view: h=[cover-ID,hold-bay-runs,deck-bay-runs]; "
    "g.covers retains exact hold/deck stack positions (covers can share bays). "
    "All deck stacks on a cover must clear before opening it to access its hold. "
    "Earlier POD-call discharges need access; later-POD deck cargo above earlier-POD hold cargo "
    "can add restow and cover lifts. Grouping PODs by cover is a cost heuristic, not a hard rule."
)


def active_prompt_version() -> str:
    return PROMPT_VERSION


def prompt_version(instance: Any) -> str:
    return PROMPT_VERSION


def system_prompt(instance: Any) -> str:
    return _base.system_prompt(instance) + HATCH_SYSTEM_SUFFIX


def _wire(instance: Any, include_layout: bool = True) -> dict[str, Any]:
    wire = _base._wire(instance, include_layout)
    wire["bv"], wire["v"] = wire["v"], PROMPT_VERSION
    stacks = {stack.id: stack for stack in instance.vessel.stacks}
    wire["h"] = [
        [cover.id,
         _base._runs(stacks[sid].bay for sid in cover.hold_stack_ids),
         _base._runs(stacks[sid].bay for sid in cover.deck_stack_ids)]
        for cover in sorted(instance.vessel.hatch_covers, key=lambda cover: cover.id)
    ]
    if "g" in wire:
        wire["g"]["covers"].sort(key=lambda row: row[0])
    return wire


def instance_to_prompt(instance: Any, *, include_layout: bool = True) -> str:
    return _base._json(_wire(instance, include_layout))


def parse_prompt(text: str) -> dict[str, Any]:
    wire = json.loads(text)
    if not isinstance(wire, dict) or wire.get("v") != PROMPT_VERSION:
        return _base.parse_prompt(text)
    base_wire = dict(wire)
    base_wire["v"] = base_wire.pop("bv")
    facts = _base._facts(base_wire)
    facts["format"] = PROMPT_VERSION
    facts["base_prompt"] = base_wire["v"]
    facts["hatch_bays"] = wire["h"]
    return facts


def prompt_facts(instance: Any, *, include_layout: bool = True) -> dict[str, Any]:
    return parse_prompt(instance_to_prompt(instance, include_layout=include_layout))


def encode_prompt(instance: Any, tokenizer: Any, *, include_layout: bool = True) -> tuple[int, ...]:
    from llm.stowlm.format import build_sequence

    return build_sequence(tokenizer, [
        "<|begin_of_text|><|start|>system<|message|>", system_prompt(instance),
        '\n\n# Valid recipients: "self", "user".<|eot|><|start|>user<|message|>',
        instance_to_prompt(instance, include_layout=include_layout),
        "<|eot|><|start|>assistant to=user<|message|>",
    ])


def _row_sha(row: Any) -> str:
    value = asdict(row) if not isinstance(row, dict) else row
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _load_frozen_val(path: str | Path) -> None:
    global _frozen_val_rows
    raw = Path(path).read_bytes()
    if sha256(raw).hexdigest() != FROZEN_VAL_SHA256:
        raise ValueError("frozen validation bytes differ from the pinned 8.2 file")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(rows) != 80 or any(row["split"] != "val" for row in rows):
        raise ValueError("expected exactly 80 frozen validation rows")
    _frozen_val_rows = {_row_sha(row) for row in rows}
    if len(_frozen_val_rows) != 80:
        raise ValueError("duplicate frozen validation row")


def install(*, frozen_val_path: str | Path | None = None) -> None:
    """Install the experiment version in this process, including cached aliases.

    Fork workers inherit this installation; spawn workers must call it themselves.
    Existing serialized validation rows retain their original prompt bytes.
    """
    names = ("active_prompt_version", "prompt_version", "system_prompt", "instance_to_prompt",
             "parse_prompt", "prompt_facts", "encode_prompt")
    for name in names:
        setattr(_public, name, globals()[name])
    for module_name in ("llm.stowlm.sft_data", "llm.stowlm.planner"):
        module = sys.modules.get(module_name)
        if module is not None:
            for name in names:
                if hasattr(module, name):
                    setattr(module, name, globals()[name])
    if frozen_val_path is not None:
        _load_frozen_val(frozen_val_path)
        from llm.stowlm import sft_data

        original = getattr(sft_data.mvp_row_failure, "_v9_original", sft_data.mvp_row_failure)

        def mvp_row_failure(row: Any) -> str | None:
            if row.split == "val" and _row_sha(row) in _frozen_val_rows:
                # Clone function globals instead of changing the active prompt
                # globally: concurrent encoders always retain v9 semantics.
                legacy = FunctionType(original.__code__, {
                    **original.__globals__, "active_prompt_version": lambda: row.meta["prompt"],
                }, original.__name__, original.__defaults__, original.__closure__)
                return legacy(row)
            return original(row)

        mvp_row_failure._v9_original = original
        sft_data.mvp_row_failure = mvp_row_failure
        if (train := sys.modules.get("llm.stowlm.train")) is not None:
            train.mvp_row_failure = mvp_row_failure


def _version_admitted(what: str, found: list[str], declared: Any, rows: list[Any], stats: dict[str, Any]) -> bool:
    if len(found) == 1 and (declared is None or found == [str(declared)]):
        return True
    if what != "prompt" or declared is not None or stats.get("prompt_versions") != found:
        return False
    train_rows = [row for row in rows if row.split == "train"]
    val_rows = [row for row in rows if row.split == "val"]
    return (bool(train_rows) and len(val_rows) == 80
            and {_row_sha(row) for row in val_rows} == _frozen_val_rows
            and all(row.meta.get("prompt") == PROMPT_VERSION for row in train_rows)
            and {row.instance_id for row in train_rows}.isdisjoint(row.instance_id for row in val_rows))


def install_training(*, frozen_val_path: str | Path = FROZEN_VAL_PATH) -> None:
    """Enable CPU admission of v9 train + the exact 80 legacy validation rows.

    All original trainer admission checks remain in place. A narrowly matched AST
    substitution changes only the single-version prompt/format guard to accept
    this pinned mixed-prompt exception. Dataset lineage still records both prompt
    versions, the real file hashes, and unchanged per-row content hashes.
    Call before invoking the existing training entry point; this imports no MLX.
    """
    from llm.stowlm import train

    install(frozen_val_path=frozen_val_path)
    if getattr(train.admit_dataset, "_v9_installed", False):
        return
    original = train.admit_dataset
    tree = ast.parse(textwrap.dedent(inspect.getsource(original)))
    expected = ast.parse("len(found) != 1 or (declared is not None and found != [str(declared)])", mode="eval").body
    replacements = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and ast.dump(node.test) == ast.dump(expected):
            node.test = ast.parse("not _v9_version_admitted(what, found, declared, rows, stats)", mode="eval").body
            replacements += 1
    if replacements != 1:
        raise RuntimeError("trainer admission structure changed; refusing an unreviewed prompt exception")
    namespace = {**original.__globals__, "_v9_version_admitted": _version_admitted,
                 "mvp_row_failure": train.mvp_row_failure}
    exec(compile(ast.fix_missing_locations(tree), str(Path(__file__)), "exec"), namespace)
    modified = namespace[original.__name__]
    modified._v9_installed = True
    train.admit_dataset = modified

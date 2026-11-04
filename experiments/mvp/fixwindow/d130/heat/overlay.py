"""Private experiment overlay, not a production mask fix.

Call install() before constructing an engine or importing a planner. It replaces
only IncrementalEngine._heat_many in this process, with strict source pins.
No production file, checker function, environment switch, or data is changed.
"""
from __future__ import annotations

import __future__
import ast
from hashlib import sha256
import importlib
import inspect
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
PINS = {
    ROOT / "src/stowage/feasibility/_engine.py": "eaf19b0517ac4ce9f03a294ebcb2b3d3aab189f906d6a7f27d89356cd60ebc0f",
    HERE / "engine-candidate.py": "2d505524484c1576a6b61ce98a39af84a11f0387bee65bb34ad282aec516e81e",
    HERE / "feasibility-heat-tank-links.patch": "864b9c2931d7749b37c5678326a7c30cba0a4689d11a473793bcb888d068fcab",
    ROOT / "src/stowage/checker/rules_special.py": "bad52b0135ed37682e5b8c33d68dd7e9ff0266814e5a39a8f84e2882c307cef3",
}


def install() -> dict:
    """Install only in the current fresh experiment process and return provenance."""
    hashes = {str(path): sha256(path.read_bytes()).hexdigest() for path in PINS}
    for path, expected in PINS.items():
        if hashes[str(path)] != expected:
            raise RuntimeError(f"D130 source epoch differs; do not silently rebase: {path}")
    source = ROOT / "src/stowage/feasibility/_engine.py"
    candidate = HERE / "engine-candidate.py"
    tree = ast.parse(candidate.read_text())
    owners = [node for node in tree.body if isinstance(node, ast.ClassDef)
              and any(isinstance(child, ast.FunctionDef) and child.name == "_heat_many"
                      for child in node.body)]
    if len(owners) != 1 or owners[0].name != "IncrementalEngine":
        raise RuntimeError("D130 engine class discovery is ambiguous or changed")
    method = next(node for node in owners[0].body if isinstance(node, ast.FunctionDef)
                  and node.name == "_heat_many")
    module = importlib.import_module("stowage.feasibility._engine")
    cls = getattr(module, owners[0].name)
    if Path(inspect.getfile(cls)).resolve() != source.resolve():
        raise RuntimeError("D130 loaded feasibility engine comes from a different source tree")
    if getattr(cls._heat_many, "_d130_overlay", False):
        raise RuntimeError("D130 overlay must be installed once in a fresh process")
    if Path(cls._heat_many.__code__.co_filename).resolve() != source.resolve():
        raise RuntimeError("D130 loaded heat method was already replaced")
    namespace = dict(module.__dict__)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(candidate), "exec",
                 flags=__future__.annotations.compiler_flag), namespace)
    replacement = namespace["_heat_many"]
    replacement._d130_overlay = True
    setattr(cls, "_heat_many", replacement)
    # Check again after import/installation; this helper never writes these files.
    for path, expected in PINS.items():
        if sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"D130 input changed during overlay installation: {path}")
    return {
        "mask_epoch": "D130 private in-memory E8 tank/source namespace correction",
        "production_mask_unchanged": True,
        "production_source_files_unchanged": True,
        "full_checker_modified_by_overlay": False,
        "patched_class": f"{module.__name__}.{cls.__name__}",
        "patched_method": "_heat_many",
        "source_sha256": hashes,
        "overlay_helper_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "warning": "Private corrected-mask evidence is not production-mask replay approval",
    }

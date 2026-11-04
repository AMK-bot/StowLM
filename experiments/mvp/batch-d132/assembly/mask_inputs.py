"""Pin the local Python import closure and tokenizer used by mask qualification.

This is a read-only static dependency inventory, not another production edit.
Package initializers and imports nested in functions are included. Unrelated
training-loop modules are excluded unless an entry point actually imports them.
"""
from __future__ import annotations

import ast
from pathlib import Path

ENTRIES = ("llm.stowlm.mask", "llm.stowlm.format", "llm.securing.mask_filter",
           "llm.lookahead.mask_filter", "llm.instructions.mask_filter")


def closure(runtime: Path) -> list[str]:
    src = runtime / "src"
    pending = list(ENTRIES)
    visited = set()
    paths = set()
    while pending:
        name = pending.pop()
        if name in visited or not name.startswith("llm"):
            continue
        visited.add(name)
        module = src.joinpath(*name.split("."))
        path = module.with_suffix(".py")
        package = module / "__init__.py"
        if package.is_file():
            path = package
        if not path.is_file():
            continue  # Imported attributes need not be modules.
        paths.add(str(path.relative_to(runtime)))
        parts = name.split(".")
        pending.extend(".".join(parts[:n]) for n in range(1, len(parts)))
        current_package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parent = current_package.split(".")
                    base = parent[:len(parent) - node.level + 1]
                    imported = ".".join(base + ([node.module] if node.module else []))
                else:
                    imported = node.module or ""
                pending.append(imported)
                pending.extend(imported + "." + alias.name for alias in node.names if alias.name != "*")
    assert {"src/llm/stowlm/mask.py", "src/llm/securing/assets.py",
            "src/llm/lookahead/bounds.py", "src/llm/instructions/apply.py"} <= paths
    assert not any(name.startswith("src/llm/exit/") for name in paths)
    return sorted(paths)

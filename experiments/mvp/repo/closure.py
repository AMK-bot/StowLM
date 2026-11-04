"""Regenerate the reviewed repo proposal, following local imports without execution.

Run from the repository root with ``uv run --offline --no-sync python
experiments/mvp/repo/closure.py``. Optionally --copy-to an empty scratch path.
This prepares a candidate, never stages files or grants publication permission.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict, deque
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parents[2]


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_list(path: Path) -> list[str]:
    paths = []
    lines = path.read_text().splitlines()
    tabular = any(re.match(r"^\d+\t", line) for line in lines)
    for line in lines:
        if not line or line.startswith(("#", "[")):
            continue
        if "\t" in line:
            size, line = line.split("\t", 1)
            if not size.isdigit():
                continue
        elif tabular:
            # Audited proposals contain un-commented subtotal/footer prose.
            continue
        paths.append(line.strip())
    return sorted(set(paths))


class Closure:
    def __init__(self, root: Path, policy: dict):
        self.root = root.resolve()
        self.policy = policy
        self.reasons = defaultdict(set)
        self.excluded = defaultdict(set)
        self.references = []
        self.import_edges = []
        self.third_party = set()
        self.unresolved = set()
        self.errors = []
        self.modules = {}
        self.namespaces = set()
        self.by_path = {}
        self.parsed = set()
        self.queue = deque()
        self.files = {}

    def deny(self, relative: str) -> str | None:
        p = Path(relative)
        if p.is_absolute() or not p.parts or ".." in p.parts:
            return "unsafe/nonrelative path"
        if p.as_posix() != relative:
            return "noncanonical relative path; use a normalized repo-relative path"
        for prefix in self.policy["exclude_prefixes"]:
            if relative == prefix or relative.startswith(prefix + "/"):
                return "audited excluded prefix: " + prefix
        for part in p.parts:
            if part in self.policy["exclude_parts"] or part.startswith((".pytest", ".venv")):
                return "generated/private directory"
        if p.name in self.policy["exclude_names"] or p.name.startswith(".env."):
            return "private/configuration file"
        if p.suffix.lower() in self.policy["exclude_extensions"]:
            return "secret/weight/runtime extension"
        if any(fnmatch.fnmatch(relative, pattern) for pattern in self.policy["exclude_patterns"]):
            return "audited exclusion pattern"
        if (self.root / p).is_symlink() or not (self.root / p).resolve().is_relative_to(self.root):
            return "symlink/outside repository"
        return None

    def walk(self, relative: str):
        base = self.root / relative
        if not base.is_dir():
            return
        for directory, dirs, names in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not self.deny((Path(directory) / d).relative_to(self.root).as_posix()))
            for name in sorted(names):
                path = (Path(directory) / name).relative_to(self.root).as_posix()
                if not self.deny(path):
                    yield path

    def index(self):
        for folder in self.policy["python_roots"]:
            for path in self.walk(folder):
                if not path.endswith(".py"):
                    continue
                parts = Path(path).with_suffix("").parts
                if parts[0] == "src":
                    parts = parts[1:]
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                module = ".".join(parts)
                self.modules[module] = path
                self.by_path[path] = module
        if (self.root / "conftest.py").is_file():
            self.modules["conftest"] = "conftest.py"
            self.by_path["conftest.py"] = "conftest"
        for module in self.modules:
            parts = module.split(".")
            self.namespaces.update(".".join(parts[:i]) for i in range(1, len(parts)))

    def add(self, path: str, reason: str):
        rejected = self.deny(path)
        if rejected:
            self.excluded[path].add(rejected)
            return
        source = self.root / path
        if not source.is_file():
            self.references.append({"path": path, "from": reason, "status": "missing"})
            return
        if path not in self.reasons:
            raw = source.read_bytes()
            if source.read_bytes() != raw:
                raise RuntimeError("Concurrent write; rerun closure: " + path)
            self.files[path] = {"path": path, "bytes": len(raw), "sha256": digest(raw)}
            if path.endswith(".py") and path not in self.by_path:
                parts = Path(path).with_suffix("").parts
                if parts[0] == "src":
                    parts = parts[1:]
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                module = ".".join(parts)
                self.by_path[path] = module
                self.modules[module] = path
            # Retain audited historical scripts as proposed, but do not treat
            # every archived benchmark/snapshot as a judge entry point. Follow
            # them if a current app/planner/test actually references the file.
            if not (path.startswith("experiments/") and reason == "previous audited proposal"):
                self.queue.append(path)
        elif path not in self.parsed and reason != "previous audited proposal":
            self.queue.append(path)
        self.reasons[path].add(reason)

    def resolve_experiment(self, module: str):
        # File-based test helpers live outside the installable packages. Resolve
        # only literal import names, never walk the enormous experiment tree.
        if not module.startswith("experiments."):
            return
        relative = module.replace(".", "/")
        for candidate in [relative + ".py", relative + "/__init__.py"]:
            if not self.deny(candidate) and (self.root / candidate).is_file():
                self.modules[module] = candidate
                self.by_path[candidate] = module
                return
        if not self.deny(relative) and (self.root / relative).is_dir():
            self.namespaces.add(module)

    def module(self, module: str, origin: str):
        self.resolve_experiment(module)
        # Include package __init__ files; their eager imports also matter.
        for count in range(1, len(module.split(".")) + 1):
            prefix = ".".join(module.split(".")[:count])
            if prefix in self.modules:
                self.add(self.modules[prefix], "import from " + origin)
        if module in self.modules:
            self.import_edges.append({"from": origin, "module": module, "path": self.modules[module]})
        elif module in self.namespaces:
            self.import_edges.append({"from": origin, "module": module, "path": None, "namespace": True})
        elif module.split(".")[0] in {"app", "stowage", "llm", "eval", "tests", "experiments"}:
            self.unresolved.add((origin, module))
        else:
            self.third_party.add(module.split(".")[0])

    def data_literal(self, literal: str, origin: str):
        if len(literal) > 400 or "\n" in literal or "://" in literal or "{" in literal:
            return
        if literal.startswith(("/Users/", "/home/")):
            self.references.append({"from": origin, "status": "absolute-home-path", "path": "[redacted absolute literal]"})
            return
        clean = literal.split("#", 1)[0]
        p = Path(clean)
        if p.suffix.lower() not in set(self.policy["asset_extensions"]) | {".py", ".fix", ".zip", ".safetensors"}:
            return
        if p.is_absolute() or ".." in p.parts:
            return
        candidates = [p, Path(origin).parent / p]
        if p.parts and p.parts[0] not in {"data", "params", "src", "app", "tests", "experiments", "models", "demo"}:
            for parent in Path(origin).parents:
                candidates.append(parent / p)
        seen = set()
        found = False
        for candidate in candidates:
            name = candidate.as_posix()
            if name in seen:
                continue
            seen.add(name)
            if not (self.root / candidate).is_file():
                continue
            found = True
            rejection = self.deny(name)
            if rejection:
                self.excluded[name].add("referenced by " + origin + "; " + rejection)
            elif name in self.reasons or name.startswith(("params/", "src/", "app/", "tests/", "data/fixtures/", "demo/")) or (name.startswith("experiments/") and name.endswith(".py")) or name in self.policy["explicit_assets"]:
                if (self.root / candidate).stat().st_size <= self.policy["max_new_asset_bytes"] or name in self.reasons:
                    self.add(name, "data reference from " + origin)
            else:
                self.references.append({"path": name, "from": origin, "status": "requires-explicit-data-review"})
        if not found and len(p.parts) > 1 and p.parts[0] in {"data", "params", "models", "demo", "experiments"}:
            self.references.append({"path": p.as_posix(), "from": origin, "status": "unresolved-runtime-path"})

    def scan(self, path: str):
        if path in self.parsed:
            return
        self.parsed.add(path)
        if not path.endswith(".py"):
            return
        try:
            raw = (self.root / path).read_bytes()
            if digest(raw) != self.files[path]["sha256"]:
                raise RuntimeError("Source changed during closure; rerun: " + path)
            tree = ast.parse(raw, filename=path)
        except (SyntaxError, UnicodeError) as error:
            self.errors.append({"path": path, "error": type(error).__name__, "line": getattr(error, "lineno", None)})
            return
        module = self.by_path.get(path, "")
        package = module if Path(path).name == "__init__.py" else module.rpartition(".")[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.module(alias.name, path)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package.split(".")
                    base = base[:len(base) - node.level + 1]
                    imported = ".".join(base + ([node.module] if node.module else []))
                else:
                    imported = node.module or ""
                if imported:
                    self.module(imported, path)
                for alias in node.names:
                    child = imported + "." + alias.name if imported else alias.name
                    self.resolve_experiment(child)
                    if child in self.modules:
                        self.module(child, path)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.data_literal(node.value, path)
                # eval/planner registries use module:Class, including lazy paths.
                value = node.value.split(":", 1)[0]
                if re.fullmatch(r"(?:stowage|llm|eval|app)(?:\.[A-Za-z_][A-Za-z_0-9]*)+", value):
                    if value in self.modules:
                        self.module(value, path)

    def build(self, original: list[str], extras: list[str]):
        self.index()
        for path in original:
            self.add(path, "previous audited proposal")
        for path in ["pyproject.toml", "uv.lock", ".gitignore", "conftest.py"]:
            self.add(path, "installation/test root")
        for path in extras:
            self.add(path, "explicit reviewed addition")
            if path not in self.files:
                self.errors.append({"path": path, "error": "explicit addition missing or excluded; review policy, do not silently drop"})
        for module in self.policy["entry_modules"]:
            if module not in self.modules:
                self.errors.append({"entry_module": module, "error": "missing entry point"})
            self.module(module, "judge entry point")
        # All shipped suites are roots, not only yesterday's test files.
        for folder in ["tests", "app/tests"]:
            for path in self.walk(folder):
                if path.endswith(".py") or Path(path).suffix.lower() in self.policy["asset_extensions"]:
                    self.add(path, "shipped test suite/fixture")
        for folder in self.policy["asset_roots"]:
            for path in self.walk(folder):
                if Path(path).suffix.lower() in self.policy["asset_extensions"]:
                    if (self.root / path).stat().st_size <= self.policy["max_new_asset_bytes"]:
                        self.add(path, "public package/parameter/fixture asset")
        for path in self.policy["explicit_assets"]:
            self.add(path, "declared app data dependency")
            if path not in self.files:
                self.errors.append({"path": path, "error": "required declared data missing or excluded"})
        while self.queue:
            self.scan(self.queue.popleft())
        for origin, module in sorted(self.unresolved):
            self.errors.append({"path": origin, "module": module, "error": "unresolved local import; review or repair before copying"})


def copy_snapshot(root: Path, output: Path, rows: list[dict], additions: Path) -> dict:
    output = output.resolve()
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("--copy-to must be a fresh directory outside the source repo")
    if output.exists():
        raise ValueError("--copy-to must not exist (no overwrite/merge)")
    output.mkdir(parents=True)
    for row in rows:
        raw = (root / row["path"]).read_bytes()
        if digest(raw) != row["sha256"]:
            raise RuntimeError("Source changed after closure; rerun: " + row["path"])
        target = output / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    ignore = output / ".gitignore"
    ignore.write_bytes(ignore.read_bytes() + b"\n# Draft release ignore additions\n" + additions.read_bytes())
    return {"tree": str(output), "copied": len(rows), "gitignore_combined_sha256": digest(ignore.read_bytes()), "no_git": not (output / ".git").exists()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--policy", type=Path, default=HERE / "closure-policy.json")
    parser.add_argument("--baseline", type=Path, default=HERE / "commit-list.txt")
    parser.add_argument("--out", type=Path, default=HERE / "generated")
    parser.add_argument("--include-list", action="append", type=Path, default=[])
    parser.add_argument("--copy-to", type=Path)
    args = parser.parse_args(argv)
    started = time.monotonic()
    cpu_started = time.process_time()
    root = args.root.resolve()
    policy = json.loads(args.policy.read_text())
    original = read_list(args.baseline)
    extras = sorted({p for file in args.include_list for p in read_list(file)})
    closure = Closure(root, policy)
    closure.build(original, extras)
    rows = [dict(closure.files[path], reasons=sorted(closure.reasons[path])) for path in sorted(closure.files)]
    selected = {row["path"] for row in rows}
    added, removed = sorted(selected - set(original)), sorted(set(original) - selected)
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "source_root": str(root), "baseline_sha256": digest(args.baseline.read_bytes()), "policy_sha256": digest(args.policy.read_bytes()), "selected": len(rows), "bytes": sum(r["bytes"] for r in rows), "rows": rows, "added": added, "removed": removed, "excluded": {p: sorted(reasons) for p, reasons in sorted(closure.excluded.items())}, "import_edges": closure.import_edges, "unresolved_local_imports": [{"from": p, "module": m} for p, m in sorted(closure.unresolved)], "third_party_top_levels": sorted(closure.third_party), "runtime_references": closure.references, "external_requirements": policy["external_requirements"], "errors": closure.errors, "wall_s": time.monotonic() - started, "limits": ["Static conservative closure follows conditional imports and literal registries without executing code. Arbitrary computed paths/plugins cannot be proven complete; freeze rehearsal is mandatory.", "Original audited candidates are retained unless exclusions apply. Newly added source/assets need final sensitivity/rights review; this is no publication authorization.", "Docs remain excluded without individual user approval; raw KCS, models, provider/private/generated files stay excluded even when referenced.", "Asset directories deliberately include a superset to cover computed fixture/parameter/static filenames. External tokenizer/models and final demo bundle remain explicit prerequisites."]}
    report["pending_approved_release_roots"] = [p for p in policy.get("release_root_files", []) if p not in selected]
    report["tool_sha256"] = digest(Path(__file__).read_bytes())
    if args.copy_to and not closure.errors:
        report["copy"] = copy_snapshot(root, args.copy_to, rows, HERE / "gitignore.additions")
    report["total_wall_s"] = time.monotonic() - started
    report["cpu_s"] = time.process_time() - cpu_started
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        report["peak_rss_bytes"] = peak if sys.platform == "darwin" else peak * 1024
    except ImportError:
        report["peak_rss_bytes"] = None
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "candidate-paths.txt").write_text("# Regenerated candidate; review only, never staged\n" + "".join(f"{r['bytes']}\t{r['path']}\n" for r in rows))
    (args.out / "closure.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    diff = ["# Regenerated candidate diff vs REPO-PLAN.md's commit-list.txt", "", f"Selected {len(rows)} files, {report['bytes']:,} bytes. Added {len(added)}, removed {len(removed)}.", "", "No staging/publication. Read closure.json for import edges, exclusions, external setup and unresolved runtime paths.", "", "## Added", ""]
    diff.extend("- `" + path + "`" for path in added)
    diff.extend(["", "## Removed / still excluded", ""])
    diff.extend("- `" + path + "`: " + "; ".join(sorted(closure.excluded.get(path, {"missing source path"}))) for path in removed)
    diff.extend(["", "## Limits", ""] + ["- " + limit for limit in report["limits"]])
    (args.out / "DIFF.md").write_text("\n".join(diff) + "\n")
    print(json.dumps({k: report[k] for k in ["selected", "bytes", "errors", "wall_s"]} | {"added": len(added), "removed": len(removed), "copy": report.get("copy")}))
    return 1 if closure.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

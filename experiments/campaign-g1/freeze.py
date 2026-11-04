"""Pin the complete source epoch. A diagnostic epoch can never become evidence."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from eval.snapshot_io import read_snapshot


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "freeze-manifest.json"
DEFAULT_PATHS = ("src", "params", "data/fixtures", "pyproject.toml", "uv.lock")


def enforce_rule_policy(*args, **kwargs):
    spec = importlib.util.spec_from_file_location('campaign_rule_policy', Path(__file__).with_name('rule_policy.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.enforce_rule_policy(*args, **kwargs)


def migration_gate(*args, **kwargs):
    spec=importlib.util.spec_from_file_location("campaign_migration_policy",Path(__file__).with_name("rule_policy.py"))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.migration_gate(*args, **kwargs)


def _migration_digest(report):
    return hashlib.sha256(json.dumps(report,sort_keys=True,allow_nan=False).encode()).hexdigest()


def sha256_file(path):
    return read_snapshot(path).sha256


def numerical_cache_inventory(sources):
    """Hash every payload, metadata file and binary in the declared cache roots."""
    files = {}
    for name, source in sorted(sources.items()):
        root = Path(source)
        if not root.exists():
            continue
        if not root.is_dir() or root.is_symlink():
            raise ValueError(f"invalid numerical cache root: {root}")
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"numerical cache symlink is not supported: {path}")
            if path.is_file():
                files[f"{name}/{path.relative_to(root).as_posix()}"] = sha256_file(path)
    return files


def numerical_cache_digest(sources, files):
    return hashlib.sha256(json.dumps({"sources": sources, "files": files}, sort_keys=True).encode()).hexdigest()


def snapshot_numerical_caches(root):
    sources = {name: str((Path(root) / "experiments/cache" / name).resolve())
               for name in ("stability", "vessels", "linear")}
    if os.environ.get("STOWAGE_VESSEL_CACHE_DIR"):
        sources["vessels"] = str(Path(os.environ["STOWAGE_VESSEL_CACHE_DIR"]).resolve())
    files = numerical_cache_inventory(sources)
    return {"schema_version": 1, "sources": sources, "files": files,
            "sha256": numerical_cache_digest(sources, files),
            "generated_additions": "Attempt-local only; never promoted into or reused from the frozen snapshot."}


def verify_numerical_caches(pin, *, sources=None):
    if not isinstance(pin, dict) or not isinstance(pin.get("sources"), dict) or not isinstance(pin.get("files"), dict):
        raise ValueError("frozen numerical cache pin is missing; create a new freeze")
    if set(pin["sources"]) != {"stability", "vessels", "linear"}:
        raise ValueError("frozen numerical cache pin has invalid cache roots")
    if numerical_cache_digest(pin["sources"], pin["files"]) != pin.get("sha256"):
        raise ValueError("frozen numerical cache pin fingerprint is corrupt")
    actual = numerical_cache_inventory(pin["sources"] if sources is None else sources)
    expected = pin["files"]
    drift = [f"numerical cache {('added' if name not in expected else 'deleted' if name not in actual else 'changed')}: {name}"
             for name in sorted(set(expected) | set(actual)) if expected.get(name) != actual.get(name)]
    if drift:
        raise RuntimeError("numerical cache freeze drift:\n" + "\n".join(drift))
    return pin


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_manifest(path=DEFAULT_MANIFEST):
    return read_snapshot(path).json()


def _snapshot(root, paths, *, snapshots=None):
    result = {}
    for name in paths:
        path = root / name
        if not path.exists():
            raise FileNotFoundError(f"freeze input missing: {path}")
        for file in sorted(path.rglob("*")) if path.is_dir() else [path]:
            if file.is_file():
                snapshot = read_snapshot(file)
                key = str(file.relative_to(root))
                result[key] = snapshot.sha256
                if snapshots is not None:
                    snapshots[key] = snapshot
    return result


def _versions(root, *, snapshots=None):
    path = root / "src/stowage/versions.py"
    if not path.exists():
        return {}
    snapshot = (snapshots or {}).get("src/stowage/versions.py") or read_snapshot(path)
    values = {}
    for node in ast.walk(ast.parse(snapshot.data.decode("utf-8"))):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for target in targets:
                    if isinstance(target, ast.Name) and target.id.endswith("VERSION"):
                        values[target.id] = node.value.value
    catalogue = root / "src/stowage/rules/catalogue.toml"
    if catalogue.exists():
        catalogue_snapshot = (snapshots or {}).get("src/stowage/rules/catalogue.toml") or read_snapshot(catalogue)
        values["rule_catalogue"] = "sha256:" + catalogue_snapshot.sha256
    return values


def _creation(root,paths,date,diagnostic,evidence_eligible):
    return dict(root=str(root),paths=list(paths),created_at=date,
                diagnostic=diagnostic,evidence_eligible=evidence_eligible)


def _rule_epoch(source,cache,policy,migration,creation):
    return hashlib.sha256(json.dumps(dict(source=source,cache=cache,policy=policy,
        migration=_migration_digest(migration),creation=creation),sort_keys=True,allow_nan=False).encode()).hexdigest()


def _epoch_id(date,digest,diagnostic):
    parsed=datetime.fromisoformat(date)
    if parsed.tzinfo is None or parsed.utcoffset()!=timezone.utc.utcoffset(parsed):
        raise ValueError('freeze creation date must be UTC')
    return ('diagnostic-' if diagnostic else '')+parsed.strftime('%Y%m%d')+'-'+digest[:12]


def create_manifest(root=ROOT, paths=None, diagnostic=False, *, canary_report_path=None):
    root = Path(root).resolve()
    paths = list(DEFAULT_PATHS if paths is None else paths)
    snapshots = {}
    before = _snapshot(root, paths, snapshots=snapshots)
    versions = _versions(root, snapshots=snapshots)
    required_rules = enforce_rule_policy(root)
    migration=migration_gate(root,canary_report_path=canary_report_path,required=not diagnostic)
    numerical_caches = snapshot_numerical_caches(root)
    if migration['passed'] and migration['canaries'].get('numerical_cache_sha256')!=numerical_caches['sha256']:
        raise ValueError('migration canaries and source freeze use different numerical cache snapshots')
    after = _snapshot(root, paths)
    verify_numerical_caches(numerical_caches)
    if before != after:
        raise RuntimeError("source moved while freeze was being recorded; retry")
    enforce_rule_policy(root, required_rules)
    if migration_gate(root,canary_report_path=canary_report_path,required=not diagnostic)!=migration:
        raise RuntimeError("migration inputs moved while freeze was being recorded; retry")
    fingerprint = hashlib.sha256(json.dumps({"files": before, "versions": versions}, sort_keys=True).encode()).hexdigest()
    cache_digest=hashlib.sha256((fingerprint+numerical_caches["sha256"]).encode()).hexdigest()
    date = datetime.now(timezone.utc).isoformat()
    creation=_creation(root,paths,date,bool(diagnostic),not diagnostic)
    epoch_digest=_rule_epoch(fingerprint,numerical_caches['sha256'],required_rules['sha256'],migration,creation)
    return {"schema_version": 4,"creation_config":creation,"epoch_id":_epoch_id(date,epoch_digest,bool(diagnostic)),
            "created_at": date, "root": str(root), "migration": migration,
            "component_epoch": migration["component_epoch"], "evidence_eligible": not diagnostic, "paths": paths, "files": before,
            "versions": versions, "source_sha256": fingerprint, "diagnostic": bool(diagnostic),
            "label": "diagnostic, not evidence" if diagnostic else "frozen source epoch",
            "numerical_caches": numerical_caches, "required_rules": required_rules, "freeze_sha256": cache_digest, "rule_epoch_sha256": epoch_digest, "excluded": []}


def check_manifest(manifest_path=DEFAULT_MANIFEST, allow_drift=False, diagnostic=False):
    manifest_snapshot = read_snapshot(manifest_path)
    manifest = manifest_snapshot.json()
    recorded_digest = hashlib.sha256(json.dumps({"files": manifest["files"], "versions": manifest["versions"]}, sort_keys=True).encode()).hexdigest()
    if recorded_digest != manifest.get("source_sha256"):
        raise ValueError("freeze manifest fingerprint is corrupt")
    if allow_drift and not diagnostic:
        raise ValueError("--allow-drift requires --diagnostic")
    if manifest.get("diagnostic") != bool(diagnostic):
        raise ValueError("diagnostic/evidence mode differs from immutable freeze manifest")
    if type(manifest.get('diagnostic')) is not bool or type(manifest.get('evidence_eligible')) is not bool:
        raise ValueError('freeze creation mode must be explicit booleans')
    creation=_creation(manifest['root'],manifest['paths'],manifest['created_at'],manifest['diagnostic'],manifest['evidence_eligible'])
    if manifest.get('schema_version')!=4 or manifest.get('creation_config')!=creation:
        raise ValueError('freeze creation configuration/mode changed; create a new freeze')
    if not diagnostic and str(manifest.get('epoch_id','')).startswith('diagnostic-'):
        raise ValueError('diagnostic-origin epoch cannot be promoted to production')
    # Numerical drift is never waived: diagnostic source drift cannot authenticate
    # changed physics inputs or promote a mutable cache into the frozen snapshot.
    verify_numerical_caches(manifest.get("numerical_caches"))
    required_rules = enforce_rule_policy(manifest["root"], manifest.get("required_rules"))
    if "required_rules" not in manifest:
        raise ValueError("required rule policy pin missing; create a new freeze")
    migration=manifest.get("migration")
    if not isinstance(migration,dict):
        raise ValueError("migration gate pin missing; create a new freeze")
    if manifest.get("component_epoch")!=migration.get("component_epoch"):
        raise ValueError("migration component epoch is corrupt")
    proof_path=(migration.get("canaries") or {}).get("report_path")
    current=migration_gate(manifest["root"],canary_report_path=proof_path,required=not diagnostic)
    if migration.get('passed') and (migration.get('canaries') or {}).get('numerical_cache_sha256')!=manifest['numerical_caches']['sha256']:
        raise ValueError('migration canary numerical snapshot differs from frozen cache pin')
    if current!=migration:
        raise ValueError("migration gate inputs/proofs differ from frozen epoch; create a new freeze")
    if manifest.get("evidence_eligible") is not (not diagnostic):
        raise ValueError("freeze evidence eligibility differs from immutable mode")
    expected_epoch_digest=_rule_epoch(recorded_digest,manifest['numerical_caches']['sha256'],required_rules['sha256'],migration,creation)
    if manifest.get('epoch_id')!=_epoch_id(manifest['created_at'],expected_epoch_digest,diagnostic):
        raise ValueError('freeze epoch identity differs from creation mode and digest')
    cache_digest=hashlib.sha256((recorded_digest+manifest['numerical_caches']['sha256']).encode()).hexdigest()
    if manifest.get('freeze_sha256')!=cache_digest:
        raise ValueError('freeze numerical/source fingerprint is corrupt')
    if manifest.get('rule_epoch_sha256')!=expected_epoch_digest:
        raise ValueError('freeze rule migration fingerprint is corrupt')
    root = Path(manifest["root"])
    snapshots = {}
    try:
        actual = _snapshot(root, manifest["paths"], snapshots=snapshots)
    except FileNotFoundError:
        # Preserve named differences when an entire input directory disappears.
        actual = {}
        for name in manifest["paths"]:
            if (root / name).exists():
                actual.update(_snapshot(root, [name], snapshots=snapshots))
    expected = manifest["files"]
    drift = [f"{('added' if name not in expected else 'deleted' if name not in actual else 'changed')}: {name}"
             for name in sorted(set(expected) | set(actual)) if expected.get(name) != actual.get(name)]
    if _versions(root, snapshots=snapshots) != manifest["versions"]:
        drift.append("changed: stowage.versions values")
    if drift and not allow_drift:
        raise RuntimeError("source freeze drift:\n" + "\n".join(drift))
    return {**manifest, "drift": drift,
            "_manifest_sha256": manifest_snapshot.sha256}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--readiness",action="store_true",help="Report all migration blockers without freezing or running canaries")
    parser.add_argument("--canary-report",type=Path,help="Source/component-bound negative checker+mask canary proof journal")
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--allow-drift", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--paths", nargs="+")
    args = parser.parse_args(argv)
    if args.allow_drift and not args.diagnostic:
        parser.error("--allow-drift requires --diagnostic")
    if (args.root.resolve() != ROOT or args.paths is not None) and not args.diagnostic:
        parser.error("custom roots/paths are allowed only in diagnostics")
    if args.readiness and args.check:
        parser.error("--readiness and --check are mutually exclusive")
    try:
        if args.readiness:
            report=migration_gate(args.root,canary_report_path=args.canary_report,required=False)
            print(json.dumps(report,indent=2,sort_keys=True,allow_nan=False))
            return 0 if report["passed"] else 1
        if args.check:
            manifest = check_manifest(args.manifest, args.allow_drift, args.diagnostic)
            print(json.dumps({"epoch_id": manifest["epoch_id"], "checked_files": len(manifest["files"]), "drift": manifest["drift"], "label": manifest["label"]}))
        else:
            if args.manifest.exists():
                raise ValueError("refusing to replace an existing freeze; choose a new --manifest path")
            manifest = create_manifest(args.root, args.paths, args.diagnostic,canary_report_path=args.canary_report)
            atomic_json(args.manifest, manifest)
            print(json.dumps({"epoch_id": manifest["epoch_id"], "files": len(manifest["files"]), "manifest": str(args.manifest), "label": manifest["label"]}))
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

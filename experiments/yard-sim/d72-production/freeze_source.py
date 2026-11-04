"""Copy the actual source/input epoch for this diagnostic, without editing it."""
from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[3]
DEST = Path(__file__).resolve().parent / "source-snapshot"


def inventory():
    files = [p for d in (ROOT / "src/stowage", ROOT / "params") for p in d.rglob("*")
             if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    files += [ROOT / "pyproject.toml", ROOT / "experiments/splits/freeze.py", ROOT / "data/raw/kcs/kcs.fix"]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def main():
    if DEST.exists():
        raise SystemExit("snapshot already exists; never silently replace an experiment epoch")
    before = inventory()
    for relative, digest in before.items():
        src, dst = ROOT / relative, DEST / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        if hashlib.sha256(dst.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"source changed during copy: {relative}; preserve failed copy and retry under a new epoch")
    after = inventory()
    if before != after:
        raise SystemExit("source tree changed during snapshot; preserve failed copy and retry under a new epoch")
    payload = {"method": "byte-for-byte actual source and inputs; not modified for experiment",
               "files": before, "source_root": str(DEST)}
    (DEST.parent / "source-snapshot.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"copied and verified {len(before)} source/input files")


if __name__ == "__main__":
    main()

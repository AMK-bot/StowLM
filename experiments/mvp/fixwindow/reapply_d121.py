"""Restore only the leader-authorized, byte-exact D119 candidates."""
import ast
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "experiments/mvp/fixwindow/d121-20261030"
PINS = {
    "stability.py": ("43f02c8f516c6bf334b6f455918a522738ebac32b979036781657b6c0293a9ab",
                     "72c4ec7390b541dd5dd397b1a18c433547f1851b04a49b8cd49589f7da4a2e97"),
    "stability_budget.py": ("ad05b8ba3e35efeb3567f431bbff563afa2c43954dc824bf2e2edebb2aa0f231",
                            "51d5c7746240ede29c9d6483e043d4f24896504536adee0017f00ccc37189468"),
}


def digest(raw):
    return sha256(raw).hexdigest()


def main():
    candidates = subprocess.run(["rg", "--files", "-g", "stability.py", "-g", "stability_budget.py",
                                 "experiments/mvp/speed/checker"], cwd=ROOT,
                                check=True, capture_output=True, text=True).stdout.splitlines()
    found = {}
    for relative in candidates:
        path = ROOT / relative
        raw = path.read_bytes()
        if digest(raw) == PINS[path.name][1]:
            found.setdefault(path.name, (relative, raw))
    if set(found) != set(PINS):
        raise RuntimeError("STOP: byte-exact authorized candidates not found for both files")
    before = {}
    for name, (old, new) in PINS.items():
        raw = (ROOT / "src/stowage/checker" / name).read_bytes()
        if digest(raw) != old:
            raise RuntimeError(f"STOP: concurrent/live source drift: {name} {digest(raw)}")
        ast.parse(found[name][1], filename=name)
        before[name] = raw
    OUT.mkdir(parents=True, exist_ok=False)
    backup = OUT / "before"
    backup.mkdir()
    for name, raw in before.items():
        (backup / name).write_bytes(raw)
    rows = []
    # Budget first retains a compatible context manager for the brief mixed epoch.
    for name in ("stability_budget.py", "stability.py"):
        target = ROOT / "src/stowage/checker" / name
        if target.read_bytes() != before[name]:
            raise RuntimeError(f"STOP: source changed before installation: {name}")
        relative, raw = found[name]
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".d121-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        assert digest(target.read_bytes()) == PINS[name][1]
        rows.append({"file": target.relative_to(ROOT).as_posix(), "candidate": relative,
                     "before_sha256": PINS[name][0], "live_sha256": PINS[name][1]})
    now = datetime.now(timezone.utc).isoformat()
    receipt = {"decision": "D121", "applied_utc": now, "files": rows,
               "exact_authorized_candidates": True, "tests": "pending", "checker_ownership": "held"}
    (OUT / "REAPPLIED.json").write_text(json.dumps(receipt, indent=2) + "\n")
    note = [f"\n\n## D121 re-applied — {now} — checker held\n",
            "mvp-construct restored the exact two leader-authorized D119 candidate files.\n",
            "Production wall-clock/poll-count budgets no longer turn completed physics into\n",
            "load-dependent FAILs. Existing finite numerical kernel caps, residual tolerances,\n",
            "legacy budget configuration validation and hard rejection on numerical failure\n",
            "remain. No physical rule/tolerance changed. Tests are pending, not yet claimed.\n\n"]
    note.extend(f"- `{row['file']}` SHA256 `{row['live_sha256']}`\n" for row in rows)
    note.extend(["\nReceipt and exact previous source backup: `fixwindow/d121-20261030/REAPPLIED.json`.\n",
                 "Checker edits remain held by mvp-construct through tests and the fix window.\n",
                 "mvp-tomlcache must wait for an explicit release entry. Training generation\n",
                 "boundaries must record the new checker code hash; old artifacts stay historical.\n"])
    with (ROOT / "experiments/mvp/determinism/checker-notes.md").open("a") as stream:
        stream.write("".join(note))
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()

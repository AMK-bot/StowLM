"""Preserve absolute evidence identities while executing byte-pinned modules.

Only two storage/identity paths change. Every original qualification validator
still executes. The live primitive and policy bank must match pinned bytes.
"""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]


def install(output=None):
    import stowage.linear.margin_policy as policy
    import stowage.linear.boundary_calibration as boundary
    records = []
    for relative in ("src/stowage/linear/boundary_calibration.py", "src/stowage/linear/margin_policy_data.json",
                     "experiments/linear-model/epoch_requalification/necessary.py"):
        pinned, original = HERE / "frozen" / relative, REPO / relative
        left, right = hashlib.sha256(pinned.read_bytes()).hexdigest(), hashlib.sha256(original.read_bytes()).hexdigest()
        if left != right:
            raise ValueError(f"qualification path identity changed: {relative}")
        records.append(dict(path=relative, pinned_sha256=left, original_sha256=right))
    expected_boundary = HERE / "frozen/src/stowage/linear/boundary_calibration.py"
    if Path(boundary.__file__).resolve() != expected_boundary.resolve():
        raise ValueError("boundary calibration code was not imported from the source pin")
    policy._ROOT = REPO
    boundary.__file__ = str(REPO / "src/stowage/linear/boundary_calibration.py")
    result = dict(adaptation="evidence root and primitive file identity only; qualification logic unchanged",
                  checks=records, margin_policy_root=str(REPO), boundary_identity=boundary.__file__)
    if output:
        Path(output).mkdir(parents=True, exist_ok=True)
        (Path(output) / "qualification-path-adapter.json").write_text(json.dumps(result, indent=2) + "\n")
    return result

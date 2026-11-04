"""Feasibility experiment: corrected E8 mask only during construction.

Production files and full-checker acceptance stay unchanged. The later token
replay deliberately uses the unpatched production mask in a fresh process.
A checker-passing witness is not production-mask approval while that bug exists.
"""
from hashlib import sha256
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "run_showcase_twostep_d126.py"
assert sha256(BASE.read_bytes()).hexdigest() == "d79bd711f327e5737ccd54fe646c8feef3425cf043737244efc3482ab4a3a175"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    base = load("d130_original_showcase_stages", BASE)
    original = base.twostep_complete

    def private_constructor(instance, args, row):
        overlay = load("d130_private_heat_overlay", HERE / "heat/overlay.py")
        row["private_mask_experiment"] = overlay.install()
        row["private_mask_experiment"]["scope"] = "constructor and its postconstruction screen only"
        row["production_token_replay_unpatched"] = True
        return original(instance, args, row)

    base.twostep_complete = private_constructor
    # The inherited parent must launch this wrapper for every fresh stage.
    # Its original HERE/ROOT still identify the unmodified shared utilities.
    base.__file__ = str(Path(__file__).resolve())
    base.main()


if __name__ == "__main__":
    main()

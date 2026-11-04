"""Prepare the queued TimeoutError propagation patch; never edit live checker."""
from __future__ import annotations

import difflib
from hashlib import sha256
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
EXPECTED_D119 = "72c4ec7390b541dd5dd397b1a18c433547f1851b04a49b8cd49589f7da4a2e97"


def main():
    source = (ROOT / "src/stowage/checker/stability.py").read_text()
    assert sha256(source.encode()).hexdigest() == EXPECTED_D119, "Live D119 source epoch changed; review/rebase first"
    anchor = """                accelerated = cached_geometry(geometry,params,rho)
            except (OSError, ValueError, KeyError, TypeError, BadZipFile):
"""
    assert source.count(anchor) == 1
    candidate = source.replace(anchor, """                accelerated = cached_geometry(geometry,params,rho)
            except TimeoutError:
                # External watchdog expiry is not a damaged optional cache.
                # The caller must mark the interrupted check unavailable.
                raise
            except (OSError, ValueError, KeyError, TypeError, BadZipFile):
""", 1)
    compile(candidate, "stability-cache-timeout.py", "exec")
    patch = "".join(difflib.unified_diff(source.splitlines(keepends=True),
        candidate.splitlines(keepends=True), fromfile="a/src/stowage/checker/stability.py",
        tofile="b/src/stowage/checker/stability.py"))
    (HERE / "stability-before.py").write_text(source)
    (HERE / "stability-cache-timeout.py").write_text(candidate)
    (HERE / "stability-cache-timeout.patch").write_text(patch)
    (HERE / "checker-source-pins.json").write_text(json.dumps({
        "live_D119_sha256": EXPECTED_D119,
        "candidate_sha256": sha256(candidate.encode()).hexdigest(),
        "patch_sha256": sha256(patch.encode()).hexdigest(),
        "production_applied": False,
        "queued_until_after_fix_window": True,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()

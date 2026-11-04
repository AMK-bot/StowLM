"""Generate the timing-pilot inputs: the first v2 val allocation per (rung, ship) with the current v3 producer
(C2 screen per --c2). Diagnostic inputs: written here, never a release. Usage: gen_inputs.py OUT rung:ship ..."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "base-mask"))


def main():
    import stowage.interfaces  # noqa: F401
    from make_val_v3 import build_specs
    from stowage.generate import instances_v3 as v3
    from stowage.io import save_instance

    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    c2 = "--c2" in sys.argv
    for want in [a for a in sys.argv[2:] if ":" in a]:
        rung, ship = want.split(":")
        specs = [s for s in build_specs(profiles=[rung], c2=c2, max_attempts=20) if s["vessel"] == ship]
        t = time.perf_counter()
        for spec in specs[:3]:
            try:
                instance, audit = v3.generate(spec)
                path = out / f"{rung}-{ship}.json"
                save_instance(instance, path)
                print(json.dumps({"want": want, "ok": True, "id": instance.id, "boxes": len(instance.load_list),
                                  "s": round(time.perf_counter() - t, 1), "path": str(path)}), flush=True)
                break
            except Exception as exc:
                print(json.dumps({"want": want, "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}),
                      flush=True)


if __name__ == "__main__":
    main()

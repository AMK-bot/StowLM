"""Read live reservations and public Mach CPU accounting before heavy D130 work."""
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[4]


def ticks():
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    library.mach_host_self.restype = ctypes.c_uint
    library.host_statistics.argtypes = (ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint))
    library.host_statistics.restype = ctypes.c_int
    values, count = (ctypes.c_uint * 4)(), ctypes.c_uint(4)
    result = library.host_statistics(library.mach_host_self(), 3, ctypes.cast(values, ctypes.POINTER(ctypes.c_int)), ctypes.byref(count))
    if result or count.value != 4:
        raise RuntimeError("Mach accounting unavailable")
    return tuple(values)


def main():
    reservations = []
    for line in (ROOT / "experiments/fleet/ledger.md").read_text().splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 9 or not line.startswith("|"):
            continue
        try:
            cores, ram = float(cells[3]), float(cells[4])
        except ValueError:
            continue
        reservations.append({"owner": cells[1], "cores": cores, "ram_gb": ram})
    before = ticks()
    time.sleep(.5)
    deltas = [(end - start) & 0xffffffff for start, end in zip(before, ticks())]
    # A zero-tick sampling interval is unavailable evidence, never idle CPUs.
    total = sum(deltas)
    busy = (deltas[0] + deltas[1] + deltas[3]) / total * os.cpu_count() if total else None
    cores, ram = sum(row["cores"] for row in reservations), sum(row["ram_gb"] for row in reservations)
    record = {"observed_utc": datetime.now(timezone.utc).isoformat(), "reservations": reservations,
              "reserved_cores": cores, "reserved_ram_gb": ram, "mach_busy_cores": busy,
              "loadavg": os.getloadavg(), "request": {"cores": 1, "ram_gb": 4, "gpu": False},
              "fits": cores + 1 <= 16 and ram + 4 <= 112 and busy is not None and busy + 1 <= 16,
              "sampling_error": None if total else "Mach accounting returned zero ticks; retry",
              "memory_pressure": subprocess.run(["memory_pressure"], capture_output=True, text=True).stdout,
              "ps": "sandbox denied; no process-list bypass attempted"}
    path = ROOT / "experiments/mvp/fixwindow/d130/admission.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({key: value for key, value in record.items() if key not in ("reservations", "memory_pressure")}))


if __name__ == "__main__":
    main()

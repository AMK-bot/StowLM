# Gather the 27 Oct benchmark outputs into one JSON (results_2026-10-27.json). Usage: uv run python <this>
import json
import re
from pathlib import Path

d = Path(__file__).parent
rows = lambda p: [json.loads(l) for l in (d / p).read_text().splitlines() if l.strip()]
qlora = {}
for f in sorted((d / "qlora_logs").glob("seq*.log")):
    t = re.sub(r"\x1b\[[0-9;]*m", "", f.read_text())
    tps = [int(m) for m in re.findall(r"^\s+\d+\s+[\d.]+ [▼▲]\s+(\d+)\s", t, re.M)]
    peak = re.search(r"PEAK_GB ([\d.]+) WALL_S (\d+)", t)
    qlora[f.stem] = dict(tok_s_per_iter=tps, peak_gb=float(peak[1]) if peak else None)
qlora["seq8k_l16_no_gc"] = qlora["seq12k_l16_no_gc"] = dict(result="Metal OOM (kIOGPUCommandBufferCallbackErrorOutOfMemory)")
out = dict(
    date="2026-10-27", model="models/glimmer-30b-4bit (+3-bit row)", mlx="0.32.4", mlx_lm="0.32.2",
    note="GPU throttles under sustained load: b16 decode 127 tok/s cold -> ~80 sustained; prefill 825 -> ~420 tok/s",
    generation_batchgenerator=rows("long.jsonl"), calibration_cold=rows("calib.jsonl") + rows("calib_cold.jsonl"),
    bare_model_decode_step=json.loads((d / "step.json").read_text()) + json.loads((d / "step64.json").read_text()),
    qlora=qlora,
)
(d / "results_2026-10-27.json").write_text(json.dumps(out, indent=1))
print(json.dumps(qlora))

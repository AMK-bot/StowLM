"""Does the format or context limit block a size? Prompt length, engine build, action-space fit; no model load."""
import json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'src'))
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.io import load_instance
from llm.stowlm.planner import *  # noqa
import llm.stowlm.planner as P
files = json.loads((Path(__file__).parent/'maps.json').read_text())
rows = []
for f in files:
    t = time.perf_counter(); inst = load_instance(ROOT/f); t_load = time.perf_counter() - t
    tok = P.load_tokenizer(); space = P.get_action_space()
    t = time.perf_counter(); engine = make_feasibility_engine(inst, space, config=FeasibilityConfig(extra={'global_screen': 0})); t_eng = time.perf_counter() - t
    t = time.perf_counter(); codec = P.codec_for(inst, tok); prompt = P.encode_prompt(inst, tok); t_enc = time.perf_counter() - t
    r = dict(file=f, id=inst.id, size=inst.size_class, boxes=len(inst.load_list), prompt_tokens=len(prompt), load_s=round(t_load, 2), engine_s=round(t_eng, 2), encode_s=round(t_enc, 2))
    print(json.dumps(r), flush=True); rows.append(r)
(Path(__file__).parent/'preflight.json').write_text(json.dumps(rows, indent=1))

"""Rebuild the masked completions (C2 on/off, seeds 17/18) and save completed plans."""
import random, sys
from pathlib import Path
from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.mask import random_masked_plan
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import Instance
HERE = Path(__file__).parent
def main():
    tok = load_tokenizer()
    for name in ("random-tiny-1", "simulated-tiny-1"):
        inst = Instance.model_validate_json(Path(f"experiments/mask/c2-on-current/{name}.instance.json").read_text())
        for seed in (17, 18):
            for c2 in (True, False):
                cfg = FeasibilityConfig() if c2 else FeasibilityConfig(extra={"global_screen": 0})
                eng = make_feasibility_engine(inst, get_action_space("boxslot"), config=cfg)
                dec = random_masked_plan(eng, ActionCodec(tok, [b.id for b in inst.containers]), random.Random(seed))
                info = dec.status if dec.status != "dead_end" else f"dead_end {dec.dead_end.reason[:120]} {list(dec.dead_end.rule_ids)}"
                print(name, seed, "c2" if c2 else "noc2", info, flush=True)
                if dec.status == "complete":
                    (HERE / "plans").mkdir(exist_ok=True)
                    (HERE / "plans" / f"{name}-s{seed}-{'c2' if c2 else 'noc2'}.plan.json").write_text(dec.engine.to_plan().model_dump_json())
if __name__ == "__main__":
    main()

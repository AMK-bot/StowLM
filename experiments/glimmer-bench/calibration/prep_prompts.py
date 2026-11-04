"""Fixed token-ID prompts for the conversion-tolerance calibration (task 0.1): the four validate_conversion prompts
plus two in-domain StowLM prompts (stowlm-prompt, pilot-d58 tiny instances). IDs are fixed so both stacks see
identical inputs. Usage: uv run python prep_prompts.py OUT_DIR"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from validate_conversion import PROMPTS  # noqa: E402

REPO = HERE.parents[2]
TINY = REPO / "experiments/solver-diag-1028/pilot-d58/generation/instances"


def main():
    import stowage.interfaces  # noqa: F401
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.format_prompt import PROMPT_VERSION, encode_prompt
    from stowage.io import load_instance

    tok = load_tokenizer()
    encode = lambda text: list(getattr(tok.encode(text), "ids", tok.encode(text)))  # noqa: E731
    prompts = {f"generic_{i}": encode(p) for i, p in enumerate(PROMPTS)}
    for name, rel in (("stowlm_random_tiny", "random_yard/tiny/seed-110280000.json"),
                      ("stowlm_simulated_tiny", "simulated_yard/tiny/seed-120280000.json")):
        prompts[name] = list(encode_prompt(load_instance(TINY / rel), tok))
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompts.json").write_text(json.dumps(prompts))
    print(json.dumps({"prompt_version": PROMPT_VERSION, **{k: len(v) for k, v in prompts.items()}}))


if __name__ == "__main__":
    main()

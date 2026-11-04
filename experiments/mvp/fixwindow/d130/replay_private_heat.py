"""Replay the checked feasibility witness with active securing and private E8 fix.

This is labelled private-correction qualification; production mask source is not
modified. Unpatched production replay evidence remains preserved separately.
"""
from dataclasses import asdict
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent


def main():
    root = Path(sys.argv[1])
    target = root / "private-securing-mask.json"
    if target.exists():
        raise FileExistsError(target)
    cache_spec = importlib.util.spec_from_file_location(
        "d130_private_replay_cache_paths", HERE.parents[3] / "experiments/mvp/showcase_tools/run_hero.py")
    cache_helper = importlib.util.module_from_spec(cache_spec)
    cache_spec.loader.exec_module(cache_helper)
    cache_helper.isolate_caches(HERE.parent / "d126-20261030")
    spec = importlib.util.spec_from_file_location("private_heat_replay_overlay", HERE / "heat/overlay.py")
    overlay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(overlay)
    epoch = overlay.install()
    from stowage.io import load_instance, load_plan
    from stowage.model import CheckReport
    from stowage.checker.full import report_matches_inputs
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MaskConfig
    from llm.securing import securing_decoder_factory, securing_decode_metrics
    instance, plan = load_instance(root / "instance.json"), load_plan(root / "plan.json")
    report = CheckReport.model_validate_json((root / "report.json").read_text())
    assert report.passed and report_matches_inputs(report, instance, plan)
    started, cpu = time.perf_counter(), time.process_time()
    space = get_action_space()
    engine = make_feasibility_engine(instance, space, config=FeasibilityConfig(extra={"global_screen": 0}))
    codec = ActionCodec(load_tokenizer(), [box.id for box in instance.containers])
    decoder = securing_decoder_factory()(instance, engine, codec, MaskConfig(), 1)[0]
    assert decoder.engine.screen.bounds_available is True
    count = 0
    for action in space.from_plan(instance, plan):
        for token in codec.encode_action(action):
            decoder.advance(token)
        count += 1
    for token in codec.end_plan_tokens:
        decoder.advance(token)
    counters = securing_decode_metrics([decoder])
    gates = dict(decoder_complete=decoder.status == "complete",
                 metrics_complete=counters.get("securing_metrics_complete") is True,
                 securing_active=counters.get("securing_active") is True,
                 one_screen=counters.get("securing_screens") == 1,
                 calls_positive=isinstance(counters.get("securing_calls"), int) and counters["securing_calls"] > 0,
                 no_unavailable=counters.get("unavailable") == 0,
                 no_fail_open=counters.get("fail_open") == 0)
    evidence = dict(passed=all(gates.values()), gates=gates, counters=counters,
                    actions=count, private_mask_epoch=epoch, production_mask_approval=False,
                    wall_s=time.perf_counter()-started, cpu_s=time.process_time()-cpu,
                    engine_stats=asdict(engine.stats()),
                    artifact_sha256={name:sha256((root/name).read_bytes()).hexdigest()
                                     for name in ("instance.json", "plan.json", "report.json")})
    target.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence), flush=True)
    assert evidence["passed"], evidence


if __name__ == "__main__":
    main()

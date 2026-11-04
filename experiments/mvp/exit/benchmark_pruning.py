"""CPU-only synthetic lower-bound overhead; never estimates measured GPU gain."""
import json
from pathlib import Path
from time import perf_counter,process_time
from types import SimpleNamespace

from llm.exit.pruning import committed_lift_lower_bound


def main():
    instance=SimpleNamespace(objective=SimpleNamespace(relocate=1.,restow=1.,hatch_cover=1.,
                                                      future_restow=1.,future_hatch_cover=1.))
    moves=[SimpleNamespace(kind=('relocate','restow_shift','hatch_cover_on')[i%3]) for i in range(128)]
    iterations=10000
    wall,cpu=perf_counter(),process_time()
    for _ in range(iterations):assert committed_lift_lower_bound(instance,moves)==128
    elapsed,cpu=perf_counter()-wall,process_time()-cpu
    report=dict(fixture='synthetic 128 committed lifts; CPU only',iterations=iterations,
        wall_s=elapsed,cpu_s=cpu,cores=1,gpu=False,
        projected_bound_only_cpu_s_48_maps_k8_128_boundaries=cpu/iterations*48*8*128,
        projection='APPROXIMATION: constant 128-lift bound cost per action; proxy/provider overhead excluded',
        measured_gpu_time_saved_s=None)
    target=Path(__file__).with_name('cpu-pruning-benchmark.json')
    target.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()

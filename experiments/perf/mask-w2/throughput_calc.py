"""Arithmetic only; no source imports, GPU, network or benchmark work.

uv run python experiments/perf/mask-w2/throughput_calc.py --full-after SECONDS --medium-after SECONDS
"""
import argparse
import json

p = argparse.ArgumentParser()
p.add_argument('--full-before', type=float, default=.79527)
p.add_argument('--medium-before', type=float, default=.14825)
p.add_argument('--full-count-before', type=float, default=1.09324)
p.add_argument('--medium-count-before', type=float, default=.23128)
p.add_argument('--full-boundary-before', type=float, default=1.52871)
p.add_argument('--medium-boundary-before', type=float, default=.22308)
p.add_argument('--tiny-before', type=float, default=.0127)
p.add_argument('--small-before', type=float, default=.0291)
p.add_argument('--tiny-count-before', type=float, default=.015512)
p.add_argument('--small-count-before', type=float, default=.032136)
p.add_argument('--full-after', type=float)
p.add_argument('--medium-after', type=float)
p.add_argument('--after-counted', action='store_true', help='Use tiny/small count totals for the after mix')
p.add_argument('--tiny-share', type=float, default=.10)
a = p.parse_args()
if a.full_after is None:
    a.full_after = a.full_before
if a.medium_after is None:
    a.medium_after = a.medium_before
rates = {8: (100.,55.),16:(138.,80.)}
full = []
for mode,h in [('before_decode',a.full_before),('before_count',a.full_count_before),
               ('before_boundary_count',a.full_boundary_before),('after',a.full_after)]:
    for B,(burst,sustained) in rates.items():
        for W in (1,2,5):
            gpu = 9*B/sustained
            host = B*h/W
            full.append(dict(mode=mode,B=B,W=W,h=h,gpu_s_per_action=gpu,
                host_s_per_action=host,
                batch500_minutes=[500*max(gpu,host)/60,500*(gpu+host)/60],
                batch1000_minutes=[1000*max(gpu,host)/60,1000*(gpu+host)/60],
                gpu500_minutes=500*gpu/60,gpu1000_minutes=1000*gpu/60,
                cpu500_core_minutes=500*B*h/60,cpu1000_core_minutes=1000*B*h/60))
# Illustrative SFT total-token mix, NOT current evaluation split composition.
# Assume each rung has 9 output tokens/action and prompt tokens = 2*output tokens.
names=['tiny','small','medium','full']
if not 0 <= a.tiny_share <= .10:
    p.error('--tiny-share must lie in [0,.1]')
shares=[a.tiny_share,.30,.35,.35-a.tiny_share]
actions=[4,34,150,750]
raw=[s/(27*A) for s,A in zip(shares,actions)]
weights=[v/sum(raw) for v in raw]
h_before=[a.tiny_before,a.small_before,a.medium_before,a.full_before]
h_after=[a.tiny_count_before if a.after_counted else a.tiny_before,
         a.small_count_before if a.after_counted else a.small_before,a.medium_after,a.full_after]
h_count=[a.tiny_count_before,a.small_count_before,a.medium_count_before,a.full_count_before]
h_boundary=[a.tiny_count_before,a.small_count_before,a.medium_boundary_before,a.full_boundary_before]
mix=dict(assumed_actions=dict(zip(names,actions)),total_token_shares=dict(zip(names,shares)),
    plan_shares=dict(zip(names,weights)),mean_actions=sum(w*A for w,A in zip(weights,actions)),
    mean_output_tokens=sum(w*9*A for w,A in zip(weights,actions)),
    mean_mask_s_before=sum(w*A*h for w,A,h in zip(weights,actions,h_before)),
    mean_mask_s_after=sum(w*A*h for w,A,h in zip(weights,actions,h_after)),
    mean_mask_s_before_count=sum(w*A*h for w,A,h in zip(weights,actions,h_count)),
    mean_mask_s_before_boundary_count=sum(w*A*h for w,A,h in zip(weights,actions,h_boundary)))
mix['single_trajectory_decode_s_at_b1_21_28']=[mix['mean_output_tokens']/28,mix['mean_output_tokens']/21]
mix['best8_mask_cpu_s_before']=8*mix['mean_mask_s_before']
mix['best8_mask_cpu_s_after']=8*mix['mean_mask_s_after']
mix['best8_mask_cpu_s_count']=8*mix['mean_mask_s_before_count']
mix['best8_mask_cpu_s_boundary_count']=8*mix['mean_mask_s_before_boundary_count']
mix['best8_decode_s_at_b8_55']=8*mix['mean_output_tokens']/55
mix['best16_decode_s_at_b16_80']=16*mix['mean_output_tokens']/80
selections={}
for label,counts in [('c1',[75,75,75,20]),('base_paired',[75,75,75,10])]:
    n=sum(counts)
    selections[label]=dict(instances=n,
        mean_actions=sum(c*A for c,A in zip(counts,actions))/n,
        single_trajectory_mask_cpu_s=sum(c*A*h for c,A,h in zip(counts,actions,h_boundary))/n,
        best8_mask_cpu_s=sum(c*A*h*8 for c,A,h in zip(counts,actions,h_boundary))/n,
        best8_gpu_decode_s=sum(c*A*9*8/55 for c,A in zip(counts,actions))/n,
        best8_w2_ideal_decode_wall_s=sum(c*A*max(9*8/55,8*h/2)
            for c,A,h in zip(counts,actions,h_boundary))/n,
        best8_w2_no_overlap_decode_wall_s=sum(c*A*(9*8/55+8*h/2)
            for c,A,h in zip(counts,actions,h_boundary))/n,
        after_single_trajectory_mask_cpu_s=sum(c*A*h for c,A,h in zip(counts,actions,h_after))/n,
        after_best8_mask_cpu_s=sum(c*A*h*8 for c,A,h in zip(counts,actions,h_after))/n,
        after_best8_w2_ideal_decode_wall_s=sum(c*A*max(9*8/55,8*h/2)
            for c,A,h in zip(counts,actions,h_after))/n,
        after_best8_w2_no_overlap_decode_wall_s=sum(c*A*(9*8/55+8*h/2)
            for c,A,h in zip(counts,actions,h_after))/n)
print(json.dumps(dict(inputs=vars(a),rates_burst_sustained=rates,full=full,mix=mix,
    evaluation_selection=selections),indent=2))

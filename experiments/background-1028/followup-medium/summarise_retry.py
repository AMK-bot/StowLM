"""Summarise existing-generator medium retry trajectories, never raw draws."""
from pathlib import Path
import json
import statistics
import argparse

HERE=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--input',type=Path,default=HERE/'retry-trajectories.jsonl')
parser.add_argument('--output-json',type=Path,default=HERE/'retry-summary.json')
args=parser.parse_args()
rows=[json.loads(line)for line in args.input.read_text().splitlines()]
print('| Ship | Generator | Emitted | Candidate attempts | DG shipments redrawn | Arrival B2.3 | Arrival B/d | Witness/booked ballast t | Gate s/accepted | Total s/accepted |')
print('|---|---|---:|---:|---:|---:|---:|---:|---:|---:|')
summaries=[]
for row in rows:
    last=row['gate_calls'][-1]
    audit=last['mass_sizing_audit']
    arrival=last['result']['states']['arrival']
    entry=dict(vessel=row['vessel'],generator=row['generator'],accepted=row['accepted'],
        attempts=row['report']['attempts'],shipment_redraws=row['report']['shipment_redraws'],
        gate_seconds_per_accepted=row['gate_seconds'] if row['accepted'] else None,
        generation_and_independent_replay_s=row['wall_s'],
        gate_passes=sum(c['result']['accepted']for c in row['gate_calls']),gate_calls=len(row['gate_calls']),
        arrival_b23_ratio=arrival['b23_ratio'],arrival_gm_m=arrival['gm_m'],arrival_kg_m=arrival['kg_m'],
        arrival_B_over_d=audit['D57']['arrival']['beam_over_draught'],
        witness_ballast_t=audit['empty_witness_adjustable_mass_kg']/1000,
        booking_ballast_t=audit['booking_initial_adjustable_mass_kg']/1000,
        booking_D40_headroom_t=audit['full_booking_initial_D40_headroom_kg']/1000,
        independent_all_state_background_replay=row['emitted_background_replay_passed']and row['emitted_background_all_parts_ran'],
        actual_booking_tanks_preserved=row['emitted_booking_tanks_preserved'],
        source_start=row['source_start'],source_end=row['source_end'])
    summaries.append(entry)
    print(f"| {row['vessel']} | {row['generator']} | {int(row['accepted'])}/1 | {entry['attempts']} | {entry['shipment_redraws']} | "
          f"{entry['arrival_b23_ratio']:.3f} | {entry['arrival_B_over_d']:.3f} | {entry['witness_ballast_t']:.1f}/{entry['booking_ballast_t']:.1f} | "
          f"{row['gate_seconds']:.2f} | {row['wall_s']:.2f} |")
result=dict(measurement='six separate production retry trajectories; not independent fresh raw draws',cells=summaries,
    accepted=sum(r['accepted']for r in rows),candidate_attempts=sum(r['report']['attempts']for r in rows),
    observed_max_gate_s=max(r['gate_seconds']for r in rows),
    observed_max_total_s=max(r['wall_s']for r in rows),
    mean_gate_s=statistics.mean(r['gate_seconds']for r in rows),
    mean_total_s=statistics.mean(r['wall_s']for r in rows),
    extrapolation_note='Six trajectories do not establish tail guarantees for 2500 instances.')
args.output_json.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')

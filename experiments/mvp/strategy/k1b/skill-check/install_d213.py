"""Record a prospective fixed-prefix cap, without opening any trial outcome."""
import hashlib,json,os
from pathlib import Path
from datetime import datetime,timezone
P=Path(__file__).resolve().parent/'matched'
def main():
 target=P/'stop-plan.json'
 if target.exists(): raise RuntimeError('Stop plan already exists; never silently revise')
 manifest_path=P/'manifest.json';man=json.loads(manifest_path.read_text())
 ids=[j['instance_id'] for j in man['maps']]
 plan=dict(decision='D213',recorded_at=datetime.now(timezone.utc).isoformat(),map_limit=20,total_maps=55,total_trials=60,instance_ids=ids[:20],manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),basis='User decision before reading Part 2 outcomes; fixed existing order, no outcome-dependent stopping',outcome_rows_read_to_choose_limit=0)
 # The loaded D206 controller already refuses any pre-existing batch before spawning.
 # Fence every out-of-plan path now; it drains the 20th cohort then exits at map21.
 for iid in ids[20:]:
  b=P/'batches-D206'/iid
  try:b.mkdir(parents=True)
  except FileExistsError:raise RuntimeError(f'Unexpected out-of-plan batch already exists: {iid}')
  (b/'fence.json').write_text(json.dumps(dict(decision='D213',reason='Outside prospective first20 fixed-prefix scope; do not start',recorded_at=plan['recorded_at']))+'\n')
 target.write_text(json.dumps(plan,indent=1)+'\n')
 print(json.dumps(plan))
if __name__=='__main__':main()

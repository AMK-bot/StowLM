"""Final frozen-constructor epoch, saved+fresh requests, resumable four shards."""
from concurrent.futures import ThreadPoolExecutor
import json
from dispatch import HERE,execute,save
if __name__=='__main__':
 rows=json.loads((HERE/'selection-saved.json').read_text())+[json.loads(p.read_text()) for p in sorted((HERE/'generation/outcomes').glob('*.json'))]
 jobs=[('solver',r['id'],r['path'],'construct',300 if r['spec']['size']=='small' else 900) for r in rows if r['spec']['size']!='tiny' and r.get('path') and r.get('status') not in ('error','rejected')]
 save(HERE/'admission-final.json',{'workers':4,'cores_per_worker':1,'jobs':jobs,'repair':False,'production_mask':'CPU token decoder C2 on','source':'final-source snapshots plus per-attempt live-source hashes'})
 with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(execute,jobs))
 save(HERE/'final-results.json',results)

"""Content-pinned diagnostic source/cache copy; never modifies shared inputs."""
import argparse, hashlib, json, os, shutil, subprocess, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def snapshot(label):
 out=ROOT/'experiments/solver-diag-1028'/label/'snapshot'
 if out.exists(): raise ValueError(f'already exists: {out}')
 out.mkdir(parents=True)
 started=time.monotonic()
 for name in ('src','params'):
  shutil.copytree(ROOT/name,out/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
 (out/'experiments/cache').mkdir(parents=True)
 for name in ('vessels','stability','linear'):
  source=ROOT/'experiments/cache'/name
  subprocess.run(['/bin/cp','-cR',str(source),str(out/'experiments/cache'/name)],check=True)
 hashes={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for base in ('src','params') for p in (out/base).rglob('*') if p.is_file()}
 manifest={'label':label,'diagnostic':True,'evidence_eligible':False,'hashes':hashes,'wall_s':time.monotonic()-started}
 (out.parent/'source.json').write_text(json.dumps(manifest,indent=2)+'\n')
 print(json.dumps({'snapshot':str(out),'files':len(hashes),'wall_s':manifest['wall_s']}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('label');snapshot(p.parse_args().label)

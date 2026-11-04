"""Explicit historical metadata replay; never a current-epoch qualification.

Only in-process checker/ballast version constants and the cache's source-key
selector are restored, after byte-exact reconstruction of the old inventory.
No source/certificate files are rewritten. Native provenance is retained.
"""
import argparse,hashlib,json,os,runpy,sys,time
from pathlib import Path
from datetime import datetime,timezone
HERE=Path(__file__).resolve().parent

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 helper=runpy.run_path(str(HERE/'narrow.py'))
 from stowage.linear import cache
 import stowage.versions as versions
 old=json.loads((HERE/'signed-family/production-method1/feeder_1700/narrow-feeder_1700-full.json').read_text())
 native_fn=cache.source_fingerprint;native_source=native_fn();native_files=helper['source_file_hashes']();native_versions=versions.component_versions()
 assert native_versions['checker']=='1.2' and native_versions['ballast']=='2.1'
 versionpath=Path(versions.__file__);raw=versionpath.read_bytes()
 virtual=raw.replace(b'CHECKER_VERSION: Final = "1.2"',b'CHECKER_VERSION: Final = "1.1"').replace(b'BALLAST_VERSION: Final = "2.1"',b'BALLAST_VERSION: Final = "2.0"')
 files=dict(native_files);files['versions.py']=hashlib.sha256(virtual).hexdigest()
 assert files==old['source_file_hashes_start'],'non-version source drift or version reconstruction mismatch'
 assert helper['input_snapshot']('feeder_1700')==old['input_hashes_start']
 assert old['source_fingerprint_start']=='103cc58bc0477c146f3790395bc10a916a3e8e7b3d97732336b7b36bc0b69538'
 proof={'virtual_prior_metadata_epoch':True,'current_qualification':False,'native_source_start':native_source,
   'native_source_files_start':native_files,'native_versions_start':native_versions,
   'virtual_source':old['source_fingerprint_start'],'virtual_versions':old['component_versions_start'],
   'reconstructed_source_inventory_matches_original':True,'raw_inputs_match':True,
   'pid':os.getpid(),'start_utc':datetime.now(timezone.utc).isoformat(),
   'policy':'historical checker1.1/ballast2.0 reference retrieval only; no current promotion or stability file writes'}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.with_suffix('.replay-admission.json').write_text(json.dumps(proof,indent=2)+'\n')
 versions.CHECKER_VERSION='1.1';versions.BALLAST_VERSION='2.0';cache.source_fingerprint=lambda:old['source_fingerprint_start']
 sys.argv=[str(HERE/'narrow.py'),'--ship','feeder_1700','--level','light','--samples','128','--seed','3712','--signed-metrics','--require-liquid-tables','--output',str(a.output)]
 started=time.perf_counter();failure=None
 try:helper['main']()
 except Exception as exc:failure=repr(exc)
 finally:
  cache.source_fingerprint=native_fn;versions.CHECKER_VERSION='1.2';versions.BALLAST_VERSION='2.1'
  proof.update(native_source_end=native_fn(),native_source_files_end=helper['source_file_hashes'](),native_versions_end=versions.component_versions(),elapsed_seconds=time.perf_counter()-started,failure=failure)
  proof['native_epoch_unchanged']=proof['native_source_start']==proof['native_source_end'] and proof['native_source_files_start']==proof['native_source_files_end']
  for path in (a.output,a.output.with_suffix('.header.json')):
   if path.exists():
    d=json.loads(path.read_text());d['virtual_prior_metadata_epoch']=True;d['current_qualification']=False;d['virtual_source_clean']=d.get('source_clean');d['source_clean']=False;d['native_provenance']=proof;path.write_text(json.dumps(d,indent=2)+'\n')
  a.output.with_suffix('.replay-admission.json').write_text(json.dumps(proof,indent=2)+'\n')
 print(json.dumps({'output':str(a.output),'historical_only':True,'native_epoch_unchanged':proof['native_epoch_unchanged'],'elapsed_seconds':proof['elapsed_seconds'],'original_failure':failure}),flush=True)
 if failure:raise SystemExit(1)
if __name__=='__main__':main()

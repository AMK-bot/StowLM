"""Inspect an explicitly selected frozen epoch; legacy default roots are retired."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instances',type=Path,required=True,help='explicit frozen epoch instance root')
    parser.add_argument('--split-manifest',type=Path)
    parser.add_argument('--output',type=Path,default=Path(__file__).with_name('upstream-status.json'))
    args=parser.parse_args(argv)
    path=Path(__file__).with_name('driver.py')
    spec=importlib.util.spec_from_file_location('tiny_gold_status_driver',path)
    driver=importlib.util.module_from_spec(spec);sys.modules[spec.name]=driver;spec.loader.exec_module(driver)
    records,snapshot=driver.inspect_epoch_manifest(args.instances,args.split_manifest)
    manifest=snapshot.json()
    status=dict(checked_unix_s=time.time(),manifest_sha256=snapshot.sha256,
        epoch_id=manifest['epoch_id'],required_rules=manifest['freeze_manifest']['required_rules'],
        data_mode='pipeline_test',purpose='tiny_g1_exception_pipeline_test',
        eligible_train_tiny=len(records),component_versions=driver.component_versions(),
        relevant_source_sha256=driver.source_fingerprint()['sha256'],groups={})
    for group in driver.GROUPS:
        data=manifest.get('groups',{}).get(group,{})
        status['groups'][group]={key:data.get(key) for key in ('accepted','attempts','requested','rejected','complete','completion_rejections')}
        status['groups'][group]['eligible_train']=sum(r.group==group for r in records)
    snapshot.require_fresh()
    driver.atomic_json(args.output,status)
    print(json.dumps(status,sort_keys=True))
    return 0


if __name__=='__main__':raise SystemExit(main())

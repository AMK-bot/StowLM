"""Reuse geometry-only error evidence across versions or the curve consumer.

Physics source hashes, all vessel values, geometry digest, density and every
numerical table array must be identical. This does not invent new measurements:
the new certificate records the original SHA-bound certificate and the exact
metadata-only change. Any numerical/geometry-source/parameter change requires rerunning
the direct-reference validator instead. Useful when another owner bumps schema
or the aggregator after these geometry-only holdouts have completed.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker.stability_tables import cache_metadata, load_table_geometry, qualify_table
from stowage.vessel.fleet import load_ship_params


def refresh(source, output):
    payload = json.loads(source.read_text())
    for row in payload['validation']:
        previous = Path(row['cache_path'])
        certificate = json.loads(previous.with_suffix('.certificate.json').read_text())
        digest = hashlib.sha256(previous.read_bytes()).hexdigest()
        if certificate['cache_sha256'] != digest:
            raise ValueError('original certificate is not bound to table bytes')
        params = load_ship_params(row['vessel'])
        direct = StabilityGeometry(params)
        metadata = cache_metadata(direct, params, row['density_kg_m3'])
        with np.load(previous, allow_pickle=False) as old:
            old_metadata = json.loads(str(old['metadata']))
            if ({k:v for k,v in old_metadata.items() if k not in ('component_versions','source_sha256')} !=
                    {k:v for k,v in metadata.items() if k not in ('component_versions','source_sha256')} or
                    {k for k in old_metadata['source_sha256'] if old_metadata['source_sha256'][k]!=metadata['source_sha256'].get(k)} - {'stability_curve.py'}):
                raise ValueError('physics metadata changed; rerun direct validation')
            grid = {k:old[k].copy() for k in ('immersion_m','trim_m','heel_rad')}
            new = load_table_geometry(direct, params, row['density_kg_m3'], build=True, grid=grid)
            with np.load(new.cache_path, allow_pickle=False) as current:
                for key in ('values','kn_grid',*grid):
                    if not np.array_equal(old[key], current[key]):
                        raise ValueError(f'numerical table array changed: {key}')
        certified = qualify_table(new, certificate['validation'])
        evidence = json.loads(certified.read_text())
        evidence.update(inherited_from_cache_sha256=digest,
                        inherited_from_certificate=str(previous.with_suffix('.certificate.json')),
                        reason='identical hull geometry/interpolation sources and numerical arrays; curve-consumer/version rekey',
                        original_source_sha256=old_metadata['source_sha256'],
                        original_component_versions=old_metadata['component_versions'])
        certified.write_text(json.dumps(evidence, sort_keys=True, indent=2)+'\n')
        row.update(original_measured_cache_path=str(previous),cache_path=str(new.cache_path),
                   qualification_rekey=evidence['reason'])
    output.write_text(json.dumps(payload,indent=2)+'\n')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=Path(__file__).with_name('kn_validation.json'))
    parser.add_argument('--output',type=Path,default=Path(__file__).with_name('kn_validation_current.json'))
    args=parser.parse_args()
    refresh(args.source,args.output)

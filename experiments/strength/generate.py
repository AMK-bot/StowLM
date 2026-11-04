"""Reproduce all synthetic strength envelopes; one CPU, no GPU, <1 GB."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import tomllib

from stowage.checker.strength_limits import design_summary, permissible_stations, wave_loads
from stowage.params import load_param_file
from stowage.vessel.fleet import load_vessel, _parameter_bytes


def main():
    output = Path(__file__).resolve().parent
    summaries = {}
    for name in ('feeder_500','feeder_800','feeder_1700'):
        # Fleet inputs can change while cold KN tables are assembling. Match
        # the parameter snapshot to the built vessel rather than combining
        # old dimensions with newly loaded source geometry.
        for attempt in range(3):
            vessel = load_vessel(name, with_hull=True)
            payload = _parameter_bytes(name)
            source = f'params/fleet/{name}.toml sha256:{hashlib.sha256(payload).hexdigest()}'
            if source in vessel.sources:
                params = tomllib.loads(payload.decode('utf-8'))
                break
        else:
            raise RuntimeError(f'{name}: fleet inputs changed repeatedly during production assembly')
        stations = vessel.hull.stations
        summary = design_summary(params,vessel.hull)
        summary['geometry_family'] = params['hull_form']['family']
        summary['source_offsets_sha256'] = params['hull_form']['offsets_sha256']
        summary['vessel_sources'] = vessel.sources
        summary['geometry_status'] = 'Production load_vessel(with_hull=True), checksum-pinned KCS offsets'
        summaries[name] = summary
        kw = {k:summary[k] for k in ('length_m','beam_m','draught_m','block_coefficient',
                                     'waterplane_coefficient','bow_flare_coefficient')}
        with (output/f'{name}.csv').open('w',newline='') as f:
            fields = ['x_m','wave_rule_x_m','max_shear_n','min_shear_n','max_bending_nm','min_bending_nm',
                      'wave_hogging_nm','wave_sagging_nm','wave_max_shear_n','wave_min_shear_n']
            writer = csv.DictWriter(f,fieldnames=fields)
            writer.writeheader()
            for s in stations:
                row = s.model_dump(exclude={'max_torsion_nm'})
                # APPROXIMATION: extend rule endpoint envelopes onto the
                # physical overhang; all its buoyancy remains integrated.
                rule_x = min(max(s.x_m,0.),kw['length_m'])
                row['wave_rule_x_m'] = rule_x
                row.update({'wave_'+k:v for k,v in wave_loads(rule_x,**kw).items()})
                writer.writerow(row)
    (output/'envelopes.json').write_text(json.dumps(summaries,indent=2,sort_keys=True)+'\n')


if __name__ == '__main__':
    main()

"""One admitted cached-data probe of the exact historical-training exclusion.

No calibration, coefficient construction or exact equilibrium is run. The
certificate reader is isolated in memory; production evidence is untouched.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
RUN = HERE.parent
FRESH = RUN/'freshwater'
sys.path.insert(0, str(RUN))
sys.path.insert(0, str(FRESH))


def main():
    from guard import assert_current
    from stowage.linear.cache import load_model
    import necessary_adapter as adapter

    assert_current()
    path = FRESH/'ordinary/feeder_500/raw.json'
    raw = json.loads(path.read_text())
    template = load_model(raw['diagnostics']['coefficient_family_cache_key'])
    if template is None:
        raise ValueError('fresh cached coefficient template unavailable; never rebuild in this probe')
    certificate = adapter._expected(raw, template)
    folder = FRESH/'edges-v3/feeder_500'
    cases = []

    def probe(name, current_raw=raw, current_template=template,
              current_certificate=certificate, current_folder=folder, accept=False):
        proxy = SimpleNamespace(read=lambda unused_path: deepcopy(current_certificate))
        try:
            accepted = adapter.validate_exclusion(proxy, current_folder, current_raw, current_template)
            message = None
        except (ValueError, AssertionError) as error:
            accepted, message = False, str(error)
        passed = (accepted is True) if accept else (accepted is False)
        cases.append(dict(name=name, expected_accepted=accept, actual_accepted=accepted,
                          rejection=message, passed=passed))
        if not passed:
            raise AssertionError('exclusion negative probe failed: '+name)

    probe('exact unchanged physical point/current profile proof accepted', accept=True)
    altered = deepcopy(certificate)
    altered['total_mass_kg'] -= 1.
    probe('mutated certificate total rejected', current_certificate=altered)
    probe('wrong density cannot use exclusion', current_template=replace(template, water_density_kg_m3=1025.))
    altered_raw = deepcopy(raw)
    altered_raw['ship'] = 'feeder_1000'
    probe('wrong ship cannot use exclusion', current_raw=altered_raw)
    probe('in-band physical point cannot use exclusion', current_template=replace(template,
        band=replace(template.band, displacement_max_kg=certificate['total_mass_kg']+1000.)))
    probe('wrong campaign folder cannot use exclusion', current_folder=FRESH/'edges-v2/feeder_500')
    altered_raw = deepcopy(raw)
    altered_raw['diagnostics']['response_calibration_identity'] = '0'*64
    probe('certificate with different response rejected', current_raw=altered_raw)
    probe('changed tank order rejected', current_template=replace(template,
          tank_ids=tuple(reversed(template.tank_ids))))
    assert_current()
    result = dict(passed=all(case['passed'] for case in cases), checked_utc=datetime.now(timezone.utc).isoformat(),
        cases=cases, coefficient_key=raw['diagnostics']['coefficient_family_cache_key'],
        response_identity=raw['diagnostics']['response_calibration_identity'],
        total_mass_kg=certificate['total_mass_kg'], band_upper_kg=certificate['band_upper_kg'],
        positive_exceedance_kg=certificate['positive_exceedance_kg'], holdouts_skipped=0,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='Cached template and in-memory certificate reader only; no exact/geometry/calibration run or evidence edit')
    output = HERE/'n1-negative-probe.json'
    with output.open('x') as stream:
        stream.write(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

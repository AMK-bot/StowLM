"""Check both middle-hull names for both qualified density profiles."""
import importlib.util
import json
from pathlib import Path
import sys
import numpy as np
from stowage.linear import build_linear_model,linear_constraints
from stowage.linear.bands import band_for_instance
ROOT=Path(__file__).resolve().parent
OWNER=ROOT.parent

def main():
    spec=importlib.util.spec_from_file_location('alias_original_driver',OWNER/'driver.py')
    d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
    records=[]
    for rho in (1025.,1000.):
        values={}
        for ship in ('feeder_800','feeder_1000'):
            instance,_=d.cargo_instance(ship,'light')
            instance=instance.model_copy(update={'port':instance.port.model_copy(update={'water_density_kg_m3':rho})})
            model=build_linear_model(instance,band=band_for_instance(instance))
            assert model.measurement_diagnostics['margin_policy']['production_usable'] is True
            values[ship]=(model,{direction:linear_constraints(model,direction=direction) for direction in ('necessary','conservative')})
        a,b=(values[x] for x in ('feeder_800','feeder_1000'))
        identity=a[0].measurement_diagnostics['response_calibration_identity']
        assert identity==b[0].measurement_diagnostics['response_calibration_identity']
        for direction in ('necessary','conservative'):
            first,second=a[1][direction],b[1][direction]
            assert first.names==second.names
            for field in ('a_cell','a_tank','const','lb','ub'):
                np.testing.assert_array_equal(getattr(first,field),getattr(second,field))
        records.append(dict(density_kg_m3=rho,response_identity=identity,passed=True))
    (ROOT/'alias-validation.json').write_text(json.dumps(dict(passed=True,profiles=records),indent=2)+'\n')
    print(json.dumps(records))
if __name__=='__main__':main()

"""Separate optimistic errors from cautious predictions, without changing physics.

A positive safety margin means a quantity is inside its declared limit. Unsafe
error is max(predicted margin - exact margin, 0); conservatism is the opposite
positive tail. A two-sided quantity has TWO rows: a sign cannot be called safe
without naming the bound. Nearest-limit margins are diagnostic only; they never
replace the individual row errors. GM's lower bound makes an overprediction
unsafe. Absolute errors remain in the original calibration output.

The affine GM diagnostic also subtracts the deliberate all-tank maximum FSM
reserve, before any measured-error margin. The known-fill point estimate and
that additional reserve are reported separately. Sources: the project's frozen
linear contract and IMO 2008 IS Code Part B 3.1 free-surface corrections.
"""
from __future__ import annotations
from contextlib import contextmanager
from types import SimpleNamespace
import numpy as np

SCHEMA_VERSION=1
METRICS=('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm')

def distribution(values):
    array=np.asarray(values,dtype=float)
    return {name:np.asarray(value).tolist() for name,value in {
        'min':np.min(array,axis=0),'max':np.max(array,axis=0),
        'p01':np.percentile(array,1,axis=0),'p99':np.percentile(array,99,axis=0)}.items()}

def tails(values):
    array=np.asarray(values,dtype=float)
    return {'signed_margin_overstatement':distribution(array),
        'unsafe':distribution(np.maximum(array,0.)),
        'conservatism':distribution(np.maximum(-array,0.)),
        'unsafe_count':np.asarray(np.sum(array>0.,axis=0)).tolist(),
        'conservative_count':np.asarray(np.sum(array<0.,axis=0)).tolist()}

class SignedResiduals:
    def __init__(self,model):
        self.model=model
        self.residuals={k:[] for k in METRICS}
        self.nearest={k:[] for k in METRICS}
        self.rows={}
        self.affine_gm=[]
        self.reserve=[]
        self.reference_usage={}
        self.bounds={
            'gm_m':(model.gm_min_m,np.inf),
            'trim_m':(-min(model.max_trim_m,model.band.max_abs_trim_m),min(model.max_trim_m,model.band.max_abs_trim_m)),
            'list_rad':(-min(model.max_list_rad,model.band.max_abs_heel_rad),min(model.max_list_rad,model.band.max_abs_heel_rad)),
            'draught_m':(model.band.draught_min_m,model.band.draught_max_m),
            'sf_n':(model.sf_min_n,model.sf_max_n),
            'bm_nm':(model.bm_min_nm,model.bm_max_nm)}
    def add(self,approx,exact,*,affine_gm):
        for key,value in exact.items():
            if key.startswith('liquid_') and np.isscalar(value) and isinstance(value,(int,float,np.number)):
                self.reference_usage.setdefault(key,[]).append(float(value))
        for key in METRICS:
            predicted=np.asarray(approx[key],dtype=float)
            truth=np.asarray(exact[key],dtype=float)
            residual=predicted-truth
            self.residuals[key].append(residual)
            lower,upper=self.bounds[key]
            margin_predicted=np.minimum(predicted-lower,upper-predicted)
            margin_exact=np.minimum(truth-lower,upper-truth)
            self.nearest[key].append(margin_predicted-margin_exact)
            if np.all(np.isfinite(lower)):
                self.rows.setdefault(key+'_lower',[]).append(residual)
            if np.all(np.isfinite(upper)):
                self.rows.setdefault(key+'_upper',[]).append(-residual)
        self.affine_gm.append(float(affine_gm-exact['gm_m']))
        self.reserve.append(float(approx['gm_m']-affine_gm))
        if hasattr(self.model,'hull_max_draught_m'):
            for sign,name in ((1.,'draught_aft'),(-1.,'draught_forward')):
                predicted=approx['draught_m']+sign*.5*approx['trim_m']
                truth=exact['draught_m']+sign*.5*exact['trim_m']
                self.rows.setdefault(name+'_upper',[]).append(truth-predicted)
                if sign<0:
                    self.rows.setdefault(name+'_lower',[]).append(predicted-truth)
    def summary(self):
        assert self.residuals['gm_m'],'no accepted signed measurements'
        return {'schema_version':SCHEMA_VERSION,'samples':len(self.residuals['gm_m']),
            'sign_definition':'point residual = linear - exact; unsafe = positive overstatement of a named safety margin',
            'two_sided_policy':'both lower/upper rows retained; nearest-limit margin is diagnostic only',
            'point_signed_residuals':{k:distribution(v) for k,v in self.residuals.items()},
            'directional_rows':{k:tails(v) for k,v in self.rows.items()},
            'nearest_limit_margin_diagnostic':{k:tails(v) for k,v in self.nearest.items()},
            'affine_gm_before_measured_margin':tails(self.affine_gm),
            'deliberate_max_fsm_reserve_m':distribution(self.reserve),
            'reference_usage':{key:{**distribution(values),'sum':float(np.sum(values)),
                'samples_nonzero':int(np.count_nonzero(values))} for key,values in self.reference_usage.items()},
            'station_x_m':np.asarray(self.model.station_x_m).tolist(),
            'bounds':{k:{'lower':np.where(np.isfinite(lo),lo,None).tolist(),
                         'upper':np.where(np.isfinite(hi),hi,None).tolist()} for k,(lo,hi) in self.bounds.items()}}
    def merge(self,other):
        assert np.array_equal(self.model.station_x_m,other.model.station_x_m)
        for key in self.residuals:self.residuals[key].extend(other.residuals[key])
        for key in self.nearest:self.nearest[key].extend(other.nearest[key])
        for key,values in other.rows.items():self.rows.setdefault(key,[]).extend(values)
        self.affine_gm.extend(other.affine_gm);self.reserve.extend(other.reserve)
        for key,values in other.reference_usage.items():self.reference_usage.setdefault(key,[]).extend(values)

@contextmanager
def collect_signed(model,instance):
    """Observe accepted samples of physics.measure_errors; never resample or edit it."""
    import stowage.linear.physics as physics
    from stowage.linear.operations import actual_fsm_kgm
    collector=SignedResiduals(model)
    original_exact,original_linear=physics.exact_condition,physics._linear_arrays
    pending=[]
    def exact(*args,**kwargs):
        truth=original_exact(*args,**kwargs)
        pending[:]=[(np.asarray(args[2]).copy(),np.asarray(args[3]).copy(),truth)]
        return truth
    def linear(*args,**kwargs):
        approx=original_linear(*args,**kwargs)
        if pending:
            cell,tank,truth=pending[0]
            band=model.band
            admitted=(band.displacement_min_kg<=truth['displacement_kg']<=band.displacement_max_kg
                and band.draught_min_m<=truth['draught_m']<=band.draught_max_m
                and abs(truth['trim_m'])<=band.max_abs_trim_m and abs(truth['list_rad'])<=band.max_abs_heel_rad)
            if admitted and np.array_equal(cell,args[1]) and np.array_equal(tank,args[2]):
                reserve=(float(np.sum(model.tank_fsm_max_kgm))-actual_fsm_kgm(model,instance,tank))/truth['displacement_kg']
                collector.add(approx,truth,affine_gm=float(approx['gm_m']-reserve))
                pending.clear()
        return approx
    physics.exact_condition,physics._linear_arrays=exact,linear
    try:yield collector
    finally:physics.exact_condition,physics._linear_arrays=original_exact,original_linear

@contextmanager
def capture_calibration():
    """Observe builder calibration without modifying its errors or cached metadata."""
    import stowage.linear.physics as physics
    original=physics.measure_errors
    batches=[]
    def measured(model,instance,**kwargs):
        with collect_signed(model,instance) as collector:
            result=original(model,instance,**kwargs)
        assert collector.summary()['samples']==result[1]['samples']
        batches.append((collector,{'seed':result[1]['seed'],'samples':result[1]['samples']}))
        return result
    physics.measure_errors=measured
    try:yield batches
    finally:physics.measure_errors=original

def self_test():
    band=SimpleNamespace(max_abs_trim_m=1.,max_abs_heel_rad=.1,draught_min_m=2.,draught_max_m=8.,
                         displacement_min_kg=500.,displacement_max_kg=1500.)
    model=SimpleNamespace(gm_min_m=.15,max_trim_m=1.,max_list_rad=.1,band=band,
        sf_min_n=np.array([-100.,-200.]),sf_max_n=np.array([100.,200.]),
        bm_min_nm=np.array([-1000.,-2000.]),bm_max_nm=np.array([1000.,2000.]),station_x_m=np.array([1.,2.]))
    exact=dict(gm_m=1.,trim_m=.5,list_rad=.05,draught_m=6.,sf_n=np.array([80.,-150.]),bm_nm=np.array([800.,-1500.]))
    approx=dict(gm_m=.8,trim_m=.3,list_rad=.07,draught_m=6.1,sf_n=np.array([70.,-160.]),bm_nm=np.array([700.,-1600.]))
    samples=SignedResiduals(model);samples.add(approx,exact,affine_gm=.7);d=samples.summary()
    assert d['samples']==1 and d['directional_rows']['gm_m_lower']['unsafe']['max']==0.
    assert np.isclose(d['directional_rows']['gm_m_lower']['conservatism']['max'],.2)
    assert np.isclose(d['directional_rows']['trim_m_upper']['unsafe']['max'],.2)
    assert np.isclose(d['directional_rows']['trim_m_lower']['conservatism']['max'],.2)
    assert np.allclose(d['directional_rows']['sf_n_upper']['unsafe']['max'],[10.,10.])
    assert np.allclose(d['nearest_limit_margin_diagnostic']['sf_n']['unsafe']['max'],[10.,0.])
    assert np.isclose(d['affine_gm_before_measured_margin']['conservatism']['max'],.3)
    import stowage.linear.physics as physics
    import stowage.linear.operations as operations
    old_exact,old_linear,old_fsm=physics.exact_condition,physics._linear_arrays,operations.actual_fsm_kgm
    model.tank_fsm_max_kgm=np.zeros(1)
    cell,tank=np.array([100.]),np.array([0.])
    physics._linear_arrays=lambda *a,**kw:approx
    physics.exact_condition=lambda *a,**kw:{**exact,'displacement_kg':1000.}
    operations.actual_fsm_kgm=lambda *a,**kw:0.
    try:
        with collect_signed(model,SimpleNamespace()) as observed:
            physics._linear_arrays(model,cell,tank,instance=None)
            physics.exact_condition(model,None,cell,tank)
            physics._linear_arrays(model,cell,tank,instance=None)
            physics._linear_arrays(model,cell,tank,instance=None)
        assert observed.summary()['samples']==1,'preliminary estimates must not count as accepted samples'
    finally:
        physics.exact_condition,physics._linear_arrays,operations.actual_fsm_kgm=old_exact,old_linear,old_fsm
    print('signed metrics self-test passed: lower/upper direction, array station tails, affine GM reserve')

if __name__=='__main__':self_test()

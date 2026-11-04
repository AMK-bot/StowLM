"""Offline mass/trim-frontier calibration using unchanged exact physics.

The usual L1 projection stays close to a random ballast draw. Here a sampling
LP instead minimises or maximises ballast at prescribed signed trim frontiers.
The ``both`` mode covers both mass endpoints at every prescribed frontier. Real boxes,
their placement constraints, tank windows and exact band admission remain in
force. This changes the experimental sampling distribution, never a ship or
checker equation. It is calibration evidence, not an independent holdout.
Source: linear programming extreme-point sampling; scipy.optimize.linprog.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import signal
import time
import warnings

import numpy as np
from scipy.optimize import linprog, OptimizeWarning

HERE=Path(__file__).resolve().parent
TARGETED_CALIBRATION_VERSION='1.0'


class FrontierProjection:
    def __init__(self,mass_objective='min',tank_pairs=()):
        if mass_objective not in ('min','max','both','grid'):
            raise ValueError('mass objective must be min, max, both or grid')
        self.mass_objective=mass_objective
        self.tank_pairs=tuple(tank_pairs)
        if self.tank_pairs and mass_objective!='grid':
            raise ValueError('selected tank knots require the grid mass objective')
        self.calls=0
        self.solved=[]

    def __call__(self,model,instance,cell,heights,tank):
        size=len(tank)
        active=model.tank_adjustable.astype(float)
        bounds=[]
        windows=getattr(model,'_tank_mass_bounds_kg',{})
        for i,tid in enumerate(model.tank_ids):
            bounds.append(windows.get(tid,(0.,model.tank_capacity_kg[i]))
                          if active[i] else (0.,0.))
        dc=model.const_mass_kg+sum(cell)
        xc=model.const_moment_x_kgm+model.cell_lcg_m@cell
        yc=model.const_moment_y_kgm+model.cell_tcg_m@cell
        zc=model.const_moment_z_kgm+heights@cell
        tx=model.tank_lcg_m*active
        ty=model.tank_tcg_m*active
        tz=model.tank_vcg_m*active
        a,b=model.km_line
        c0,cx,cd=model.trim_line
        d0,dd=model.draught_line
        trim_c=c0+cx*xc+cd*dc
        trim_t=cx*tx+cd*active
        fractions=((0.,-.25,.25,-.375,.375,-.5,.5,-.65,.65,-.8,.8,-.95,.95)
            if self.mass_objective=='grid' else
            (0.,-.25,.25,-.5,.5,-.65,.65,-.8,.8,-.95,.95))
        trim_index=self.calls//4 if self.mass_objective=='grid' else self.calls
        selected=None
        if self.tank_pairs:
            pair=self.tank_pairs[self.calls%len(self.tank_pairs)]
            pair_cycle=self.calls//len(self.tank_pairs)
            pattern=('balanced','complementary','port-near-empty','starboard-near-empty')[pair_cycle%4]
            fraction=(0.,.05,.1,.25,.5,.75,.9,.95,1.)[(pair_cycle//4)%9]
            fills={'balanced':(fraction,fraction),'complementary':(fraction,1-fraction),
                'port-near-empty':(.05,fraction),'starboard-near-empty':(fraction,.05)}[pattern]
            selected=dict(pair=pair,pattern=pattern,port_fraction=fills[0],starboard_fraction=fills[1])
            for suffix,fraction in zip(('-P','-S'),fills):
                tid=pair+suffix
                if tid not in model.tank_ids:
                    raise ValueError('unknown selected tank '+tid)
                index=model.tank_ids.index(tid)
                if not active[index]:
                    raise ValueError('selected calibration tank is not adjustable: '+tid)
                mass=fraction*model.tank_capacity_kg[index]
                if not bounds[index][0]<=mass<=bounds[index][1]:
                    self.calls+=1
                    return None
                bounds[index]=(mass,mass)
            # Rotate the mass/trim grid between repeats of the complete pair/
            # pattern/knot sequence. This prevents a fixed tank corner from
            # forever being tested at only one conditional mass.
            repetitions=self.calls//(len(self.tank_pairs)*4*9)
            trim_index=5*(self.calls//4)+repetitions
        target=fractions[trim_index%len(fractions)]*min(model.max_trim_m,model.band.max_abs_trim_m)
        objective_kind=self.mass_objective
        if objective_kind=='both':
            objective_kind='min' if self.calls//len(fractions)%2==0 else 'max'
        target_mass=None
        if objective_kind=='grid':
            mass_index=(self.calls+(self.calls//(len(self.tank_pairs)*4*9)
                if self.tank_pairs else 0))%4
            mass_fraction=(.2,.4,.6,.8)[mass_index]
            target_mass=model.band.displacement_min_kg+mass_fraction*(
                model.band.displacement_max_kg-model.band.displacement_min_kg)
        self.calls+=1
        width=.005*min(model.max_trim_m,model.band.max_abs_trim_m)
        rows=[];rhs=[]
        def add(coeff,const,lo=-np.inf,hi=np.inf):
            if np.isfinite(hi):rows.append(coeff);rhs.append(hi-const)
            if np.isfinite(lo):rows.append(-coeff);rhs.append(const-lo)
        add(active,dc,model.band.displacement_min_kg,model.band.displacement_max_kg)
        if target_mass is not None:
            mass_width=max(1.,1e-5*(model.band.displacement_max_kg-model.band.displacement_min_kg))
            add(active,dc,target_mass-mass_width,target_mass+mass_width)
        add(trim_t,trim_c,target-width,target+width)
        fsm=sum(model.tank_fsm_max_kgm)
        add(tz+(model.gm_min_m-b)*active,
            zc+(model.gm_min_m-b)*dc+fsm-a,hi=0.)
        restore_c=a+b*dc-zc-fsm
        restore_t=b*active-tz
        angle=np.tan(.3*min(model.max_list_rad,model.band.max_abs_heel_rad))
        for sign in (-1.,1.):add(sign*ty-angle*restore_t,sign*yc-angle*restore_c,hi=0.)
        add(dd*active,d0+dd*dc,model.band.draught_min_m,model.band.draught_max_m)
        maximum=min(instance.vessel.hull.max_draught_m,
                    instance.port.max_draught_m or instance.vessel.hull.max_draught_m)
        add(dd*active+.5*trim_t,d0+dd*dc+.5*trim_c,hi=maximum)
        add(dd*active-.5*trim_t,d0+dd*dc-.5*trim_c,
            lo=instance.vessel.hull.min_draught_fwd_m,hi=maximum)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',OptimizeWarning)
            if objective_kind=='grid':
                # Preserve a varied partial-fill mix while visiting interior
                # masses. L1 projection is the original random draw's absolute
                # distance; auxiliary u satisfies |new_mass-random_mass|<=u.
                matrix=[np.r_[row,np.zeros(size)] for row in rows]
                for i in range(size):
                    row=np.zeros(2*size);row[i]=1.;row[size+i]=-1.
                    matrix.append(row);rhs.append(tank[i])
                    row=np.zeros(2*size);row[i]=-1.;row[size+i]=-1.
                    matrix.append(row);rhs.append(-tank[i])
                objective=np.r_[np.zeros(size),1/np.maximum(model.tank_capacity_kg,1.)]
                lp_bounds=bounds+[(0.,None)]*size
            else:
                matrix=rows;objective=active if objective_kind=='min' else -active
                lp_bounds=bounds
            result=linprog(objective,
                A_ub=np.asarray(matrix),b_ub=np.asarray(rhs),bounds=lp_bounds,
                method='highs',options={'threads':1,'parallel':False})
        if not result.success:return None
        projected=np.array([np.clip(value,lo,hi) for value,(lo,hi) in zip(result.x[:size],bounds)])
        self.solved.append(dict(target_affine_trim_m=target,
            mass_objective=objective_kind,
            selected_tank_corner=selected,
            target_displacement_kg=target_mass,
            affine_trim_m=float(trim_c+trim_t@projected),
            displacement_kg=float(dc+sum(projected)),ballast_kg=float(sum(projected))))
        return projected


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ship',default='feeder_1000')
    parser.add_argument('--level',choices=('light','medium','full'),default='light')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--prepared',type=Path,required=True,
        help='Existing192-sample raw checkpoint directory; never recalibrate')
    parser.add_argument('--samples',type=int,default=4)
    parser.add_argument('--seed',type=int,default=3821)
    parser.add_argument('--budget-seconds',type=float,default=120.)
    parser.add_argument('--no-public',action='store_true')
    parser.add_argument('--mass-objective',choices=('min','max','both','grid'),default='min')
    parser.add_argument('--tank-pairs',nargs='*',default=[])
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('targeted_epoch_driver',HERE/'driver.py')
    driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
    from stowage.linear import bands
    if not (args.prepared/args.ship/'raw.json').exists():
        raise ValueError('targeted sampling requires an existing raw calibration checkpoint')
    prepared=driver.prepare(args.ship,args.prepared)
    projector=FrontierProjection(args.mass_objective,args.tank_pairs)
    original=bands.project_ballast_for_sampling
    from stowage.linear import physics
    original_exact=physics.exact_condition
    accepted_corners=[]
    def exact_capture(*positional,**keywords):
        exact=original_exact(*positional,**keywords)
        band=positional[0].band
        if (projector.solved and band.displacement_min_kg<=exact['displacement_kg']<=band.displacement_max_kg
                and band.draught_min_m<=exact['draught_m']<=band.draught_max_m
                and abs(exact['trim_m'])<=band.max_abs_trim_m
                and abs(exact['list_rad'])<=band.max_abs_heel_rad):
            accepted_corners.append({**projector.solved[-1],
                'exact_displacement_kg':exact['displacement_kg'],
                'exact_trim_m':exact['trim_m'],'exact_list_rad':exact['list_rad']})
        return exact
    started=time.perf_counter()
    failure=None
    def deadline(*_):raise TimeoutError('targeted sampling pilot exceeded declared wall budget')
    previous=signal.signal(signal.SIGALRM,deadline)
    signal.setitimer(signal.ITIMER_REAL,args.budget_seconds)
    try:
        bands.project_ballast_for_sampling=projector
        physics.exact_condition=exact_capture
        record=driver.physical_case(args.ship,args.level,'train',args.output,prepared,
            samples=args.samples,seed=args.seed,public=not args.no_public)
        record['sampling_kind']=('targeted calibration: '+args.mass_objective+
            ' ballast mass at signed affine trim frontiers'+
            (' with selected tank-pair fill knots' if args.tank_pairs else ''))
        record['sampling_projection']=dict(calls=projector.calls,solved=projector.solved,
            accepted_corners=accepted_corners)
        record['independent_holdout']=False
        record['sampler_builder_version']=TARGETED_CALIBRATION_VERSION
        record['sampler_source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        record['sampler_config']=dict(ship=args.ship,level=args.level,samples=args.samples,
            seed=args.seed,mass_objective=args.mass_objective,tank_pairs=args.tank_pairs,
            mass_grid_fractions=[.2,.4,.6,.8],
            signed_trim_grid_fractions=[0.,-.25,.25,-.375,.375,-.5,.5,-.65,.65,-.8,.8,-.95,.95]
                if args.mass_objective=='grid' else [0.,-.25,.25,-.5,.5,-.65,.65,-.8,.8,-.95,.95],
            fill_knots=[0.,.05,.1,.25,.5,.75,.9,.95,1.],
            pair_patterns=['balanced','complementary','port-near-empty','starboard-near-empty'],
            near_empty_fraction=.05,trim_half_width_fraction=.005,
            mass_half_width_fraction=1e-5,mass_half_width_min_kg=1.,
            other_tank_objective='L1 distance from original random draw divided by tank capacity',
            public_reference_check=not args.no_public,
            constraints='unchanged mass/GM/list/trim/end-draught/tank bounds; exact band admission')
        numerical_inputs=dict(physical_inputs=record['physical_inputs_start'],
            config=record['sampler_config'],builder_version=record['sampler_builder_version'],
            source_sha256=record['sampler_source_sha256'])
        record['sampler_inputs_hash']=hashlib.sha256(json.dumps(numerical_inputs,
            sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        driver.write(args.output/args.ship/f'train-{args.level}.json',record)
    except (TimeoutError,ValueError) as exc:
        failure=str(exc)
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL,0.)
        signal.signal(signal.SIGALRM,previous)
        bands.project_ballast_for_sampling=original
        physics.exact_condition=original_exact
        driver.write(args.output/args.ship/f'targeted-{args.level}-status.json',
            dict(seconds=time.perf_counter()-started,failure=failure,
                attempted_projections=projector.calls,solved_projections=projector.solved))


if __name__=='__main__':main()

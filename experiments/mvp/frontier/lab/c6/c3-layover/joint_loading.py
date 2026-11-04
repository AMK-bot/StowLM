"""Joint box-edge and tank-fraction loading rows, calibrated at one cold reference.

The reference is a complete plan built in this invocation. It supplies a local
hull/liquid tangent, not an incumbent read from disk. Native full checking is
mandatory after any joint assignment/recipe is decoded.
"""
from __future__ import annotations
from collections import Counter
from time import monotonic
import math
import numpy as np
from scipy.sparse import csc_matrix


def cargo_mass_moments(instance, ship, box_ids):
    """Exact modeled cargo terms in native stability_loading's base bookkeeping.

    This copies only its nominal-body support/half-height accumulation. Fixed
    loads and every tank stay outside these terms; they are never subtracted.
    Values are kg and kg*m, in x/y/z order. No LP or checker is invoked.
    """
    from stowage.conventions import cells_of_slot
    wanted=set(box_ids)
    boxes={b.id:b for b in instance.containers}
    cells={c.id:c for c in instance.vessel.cells}
    occupied={cid:boxes[p.container_id] for p in ship.placements for cid in cells_of_slot(p.slot)}
    floors={}
    for stack in instance.vessel.stacks:
        z=float(stack.base_z_m)
        for cid in stack.cell_ids:
            floors[cid]=z
            if cid in occupied:z+=float(occupied[cid].height_m)
    out=np.zeros(4)
    found=Counter()
    for placement in ship.placements:
        if placement.container_id not in wanted:continue
        found[placement.container_id]+=1
        box=boxes[placement.container_id]
        ids=cells_of_slot(placement.slot)
        supports=[floors[cid] for cid in ids]
        if max(supports)-min(supports)>1e-6:raise ValueError('unequal cargo supports in joint reference')
        x=sum(cells[cid].lcg_m for cid in ids)/len(ids)
        y=sum(cells[cid].tcg_m for cid in ids)/len(ids)
        z=sum(supports)/len(supports)+float(box.height_m)/2
        mass=float(box.gross_mass_kg)
        out+=mass*np.array([1.,x,y,z])
    if set(found)!=wanted or any(n!=1 for n in found.values()):
        raise ValueError('joint reference does not contain every modeled box exactly once')
    return out


def reference_balance_offsets(ctx, reference_drafts, fractions, *, tank_mode='half'):
    """Anchor affine hull balances at the unchanged native reference point.

    The loading assembler uses upright fixed-liquid moments and half-fill
    adjustable-tank arms. Native finite-liquid equilibrium uses the actual
    reference fill, heel and trim. Add the resulting mass/X balance residual
    to the hull equations, in physical units before row normalization.
    """
    from ballast_loading import _tank_reference
    ta,tf=reference_drafts
    point=ctx.direct_geometry.full_submerged(ta,tf,0.,ctx.rho)
    arms,_=_tank_reference(ctx,mode=tank_mode)
    masses=ctx.capacity*np.asarray(fractions,float)
    model_mass=float(ctx.base.mass_kg+sum(masses))
    model_x=float(ctx.base_moments[0]+masses@arms[:,0])
    hull_mass=float(point.displacement_kg)
    hull_x=float(hull_mass*point.lcb_m)
    return dict(buoyancy_kg=model_mass-hull_mass,longitudinal_kgm=model_x-hull_x), dict(
        reference_model_mass_kg=model_mass,reference_hull_buoyancy_kg=hull_mass,
        reference_model_longitudinal_kgm=model_x,reference_hull_longitudinal_kgm=hull_x,
        added_buoyancy_rhs_kg=model_mass-hull_mass,
        added_longitudinal_rhs_kgm=model_x-hull_x)


def pressed_reference_gm(ctx, probe, fractions):
    """Remove adjustable slack-FSM/centroid bias from a pressed-tank reference.

    `_probe` uses native finite-liquid GM. To calibrate a full-arm solid tank
    model, add back the adjustable tanks' actual FSM and replace their actual
    vertical moments by their full-arm vertical moments. Fixed-liquid effects
    remain present in the native reference and its calibration.
    """
    from ballast_loading import _tank_reference
    arms,_=_tank_reference(ctx,mode='pressed')
    masses=ctx.capacity*np.asarray(fractions,float)
    heel=float(probe['heel_rad'])
    trim=math.atan2(float(probe['trim_m']),float(ctx.instance.vessel.hull.lpp_m))
    actual_fsm=0.;actual_z=0.
    for tank,mass in zip(ctx.tanks,masses):
        if mass<=0.:continue
        solid=ctx.solids[tank.id]
        volume=min(float(mass/tank.content_density_kg_m3),float(solid.capacity_m3))
        liquid=solid.liquid(volume,heel,trim)
        actual_fsm+=float(liquid.fsm_m4)*float(tank.content_density_kg_m3)
        actual_z+=float(mass)*float(liquid.centroid_m[2])
    selected_z=float(masses@arms[:,2])
    mass=float(probe['mass_kg'])
    if not math.isfinite(mass) or mass<=0.:raise ValueError('positive native reference mass required')
    add_fsc=actual_fsm/mass
    replace_z=(actual_z-selected_z)/mass
    adjusted=float(probe['gm_m'])+add_fsc+replace_z
    return adjusted,dict(native_gm_m=float(probe['gm_m']),native_gm_selected_tank_model_m=adjusted,
        actual_adjustable_fsm_kgm=actual_fsm,added_adjustable_fsc_m=add_fsc,
        actual_adjustable_vertical_kgm=actual_z,selected_full_arm_vertical_kgm=selected_z,
        adjustable_vertical_correction_m=replace_z,
        semantics='remove adjustable slack FSM and replace adjustable CG by full-tank arms; fixed liquid calibration retained')


def tank_objective(capacity_kg,arm_z_m,*,base_cost=.001,mass_reward_per_t=0.,vertical_cost_per_tm=0.):
    """Cost per tank fraction, with rewards/prices in lifts per t and t*m."""
    capacity=np.asarray(capacity_kg,float)
    z=np.asarray(arm_z_m,float)
    if capacity.ndim!=1 or z.shape!=capacity.shape or not np.isfinite(capacity).all() or not np.isfinite(z).all():
        raise ValueError('finite tank capacities and matching vertical arms required')
    if (capacity<0.).any():raise ValueError('nonnegative tank capacities required')
    values=(float(base_cost),float(mass_reward_per_t),float(vertical_cost_per_tm))
    if not all(math.isfinite(v) for v in values) or values[1]<0. or values[2]<0.:
        raise ValueError('finite tank objective with nonnegative reward and vertical price required')
    out=values[0]-values[1]*capacity/1000.+values[2]*capacity*z/1000.
    if not np.isfinite(out).all():raise ValueError('tank objective overflow')
    return out


def kg_caps_below_probes(probes,step_m,existing=None):
    """Native-reference KG backoff, without another physical evaluation.

    A new selected area failure may tighten caps once. Bounded construction
    failures alone must not keep subtracting the step from the same reference.
    """
    step=float(step_m)
    if not math.isfinite(step) or step<0.:raise ValueError('KG search step must be finite and nonnegative')
    if step==0.:return dict(existing or {})
    # A newly selected plan gets a fresh local restriction. Carrying a
    # stricter old cap can permanently freeze an unrelated reference model.
    caps={}
    for probe in probes:
        value=float(probe['kg_m'])-step
        if not math.isfinite(value) or value<=0.:raise ValueError('native KG backoff must remain positive and finite')
        phase=probe['phase']
        caps[phase]=value
    return caps


def backoff_kg_caps(caps,probes,step_m):
    """Relax a failed local KG restriction toward its current native reference.

    Reaching baseline removes that phase's optional cap. This varies bounded
    search restrictions; it does not change the native checker or prove that
    the failed local model describes an infeasible ship.
    """
    step=float(step_m)
    if not math.isfinite(step) or step<0.:raise ValueError('KG backoff step must be finite and nonnegative')
    current=dict(caps or {})
    if step==0. or not current:return current
    baseline={p['phase']:float(p['kg_m']) for p in probes}
    if set(current)-set(baseline) or any(not math.isfinite(v) or v<=0. for v in baseline.values()):
        raise ValueError('KG backoff needs finite current native phase references')
    relaxed={}
    for phase,value in current.items():
        value=float(value)
        if not math.isfinite(value) or value<=0.:raise ValueError('KG backoff cap must be positive and finite')
        next_value=min(baseline[phase],value+step)
        if next_value<baseline[phase]-1e-12:relaxed[phase]=next_value
    return relaxed


class JointLoading:
    """Builder implementing gap.Settings.joint_loading.build(solver)."""
    def __init__(self, instance, reference_plan, gm_floor=None, *, tank_quantum=1e-4,
                 draft_quantum=1e-3, tank_cost=.001, equality_grid_reserve=False,
                 tank_mode='half',tcg_halfwidth_m=None,tank_mass_reward_per_t=0.,
                 tank_vertical_cost_per_tm=0.,kg_caps_m=None):
        from stowage.solver.ballast import _validated_replay
        from stowage.checker.full import _ballast
        from stowage.solver.ballast_physics import ExactContext
        from stowage.solver.ballast_v4_evaluator import conditions
        from loading_seed import _probe
        from ballast_loading import affine_gm
        from ballast_loading import _tank_reference
        started=monotonic()
        if tank_mode not in ('half','pressed'):raise ValueError('joint tank mode must be half or pressed')
        self.tank_mode=tank_mode
        if tcg_halfwidth_m is not None and (not math.isfinite(float(tcg_halfwidth_m)) or float(tcg_halfwidth_m)<0.):
            raise ValueError('joint total TCG halfwidth must be finite and nonnegative')
        self.tcg_halfwidth_m=None if tcg_halfwidth_m is None else float(tcg_halfwidth_m)
        self.instance=instance
        self.reference_plan=reference_plan
        replay=_validated_replay(instance,reference_plan)
        ship,invalid=_ballast(instance,reference_plan,replay.final_state.ship)
        if invalid:raise ValueError('invalid ballast in joint reference')
        self.ship=ship
        self.contexts=[(phase,ExactContext(inst,state,float('inf'))) for phase,inst,state in conditions(instance,ship)]
        if not self.contexts:raise ValueError('joint reference has no native conditions')
        if any(ctx.variable_consumables for _,ctx in self.contexts):
            raise ValueError('joint reference does not support adjustable consumable tanks')
        self.tank_ids=tuple(t.id for t in self.contexts[0][1].tanks)
        if any(tuple(t.id for t in ctx.tanks)!=self.tank_ids for _,ctx in self.contexts):
            raise ValueError('joint tank catalog changes between native voyage conditions')
        self.capacity=np.asarray(self.contexts[0][1].capacity,float)
        if any(not np.array_equal(ctx.capacity,self.capacity) for _,ctx in self.contexts):
            raise ValueError('joint tank capacities change between voyage conditions')
        self.gm_floor=None if gm_floor is None else float(gm_floor)
        self.probes,self.initial_loading_quality=_probe(instance,ship,search_gm_floor=self.gm_floor)
        if len(self.probes)!=len(self.contexts):raise ValueError('joint native probe phase count differs')
        self.references=[tuple(row['draft_m']) for row in self.probes]
        self.initial_fractions=np.asarray([ship.tank_masses_kg.get(tid,0.)/cap if cap>0 else 0.
                                           for tid,cap in zip(self.tank_ids,self.capacity)])
        if not np.isfinite(self.initial_fractions).all() or (self.initial_fractions<0).any() or (self.initial_fractions>1+1e-9).any():
            raise ValueError('joint reference has invalid adjustable tank fractions')
        self.corrections={}
        self.gm_calibration=[]
        self.balance_calibration=[]
        for row,reference,(phase,ctx) in zip(self.probes,self.references,self.contexts):
            if row['phase']!=phase:raise ValueError('joint probe phase ordering differs')
            estimate=affine_gm(ctx,reference,self.initial_fractions,tank_reference_mode=self.tank_mode)
            selected_gm=float(row['gm_m'])
            pressed_details={}
            if self.tank_mode=='pressed':
                selected_gm,pressed_details=pressed_reference_gm(ctx,row,self.initial_fractions)
            bias=float(selected_gm-estimate)
            self.corrections[phase+'/gm']=bias
            self.gm_calibration.append(dict(phase=phase,affine_gm_m=estimate,native_gm_m=row['gm_m'],
                selected_native_gm_m=selected_gm,native_minus_affine_m=bias,pressed_adjustment=pressed_details))
            offsets,details=reference_balance_offsets(ctx,reference,self.initial_fractions,tank_mode=self.tank_mode)
            self.corrections.update({phase+'/'+key:value for key,value in offsets.items()})
            self.balance_calibration.append(dict(phase=phase,reference_drafts_m=list(reference),
                native_heel_rad=float(row['heel_rad']),**details))
        self.equality_grid_reserve=bool(equality_grid_reserve)
        self.quantum=np.r_[np.full(len(self.tank_ids),float(tank_quantum)),
                           np.full(2*len(self.contexts),float(draft_quantum))]
        if not np.isfinite(self.quantum).all() or (self.quantum<=0).any():raise ValueError('positive finite joint auxiliary grid required')
        arms,_=_tank_reference(self.contexts[0][1],mode=self.tank_mode)
        tank_costs=tank_objective(self.capacity,arms[:,2],base_cost=tank_cost,
            mass_reward_per_t=tank_mass_reward_per_t,vertical_cost_per_tm=tank_vertical_cost_per_tm)
        self.cost=np.r_[tank_costs,np.zeros(2*len(self.contexts))]
        self.bounds=[(0.,1.) for _ in self.tank_ids]
        for _,ctx in self.contexts:self.bounds.extend([(0.,float(ctx.instance.vessel.hull.depth_m))]*2)
        self.aux_integrality=np.r_[np.full(len(self.tank_ids),int(self.tank_mode=='pressed')),
                                   np.zeros(2*len(self.contexts),dtype=int)].astype(int)
        self.metadata=dict(reference='same-invocation concrete complete plan',cold_reference_requirement=True,
            native_conditions=self.probes,gm_calibration=self.gm_calibration,
            balance_calibration=self.balance_calibration,
            initial_loading_quality=list(self.initial_loading_quality),tank_ids=list(self.tank_ids),
            initial_fractions=self.initial_fractions.tolist(),search_gm_floor=self.gm_floor,
            auxiliary_quantum=self.quantum.tolist(),equality_grid_reserve=self.equality_grid_reserve,setup_s=monotonic()-started,
            tank_mode=self.tank_mode,auxiliary_integrality=self.aux_integrality.tolist(),
            total_tcg_search_halfwidth_m=self.tcg_halfwidth_m,
            tank_objective=dict(base_fraction_cost=float(tank_cost),
                mass_reward_per_t=float(tank_mass_reward_per_t),
                vertical_cost_per_tm=float(tank_vertical_cost_per_tm),
                selected_mode_vertical_arms_m=arms[:,2].tolist(),
                cost_per_fraction=tank_costs.tolist(),
                negative_cost_bounded_by='physical tank fraction bounds 0..1'),
            outward=False,physical_infeasibility_proven=False,full_checker_required=True)
        self.last_spec=None
        self.kg_caps_m={}
        self.set_kg_caps(kg_caps_m)

    def set_kg_caps(self,caps):
        """Set optional phase KG search caps before the joint rows are built."""
        if self.last_spec is not None:raise RuntimeError('cannot change KG caps after joint rows were built')
        values={} if caps is None else {str(k):float(v) for k,v in dict(caps).items()}
        if set(values)-{phase for phase,_ in self.contexts} or any(not math.isfinite(v) or v<=0. for v in values.values()):
            raise ValueError('KG search caps require known phases and positive finite metres')
        self.kg_caps_m=values
        self.metadata['total_kg_search_caps_m']=dict(values)
        self.metadata['kg_cap_scope']='inward affine search restriction; native full checker unchanged'
        return dict(values)

    def build(self,solver):
        """Remove each modeled cargo constant once, then insert its edge terms."""
        from ballast_loading import _assemble
        T,model=solver.T,solver.m
        names={T.box_ids[b] for b in model.boxes}
        departure_ids={p.container_id for p in self.ship.placements}
        if not names<=departure_ids:raise ValueError('joint modeled box missing from reference plan')
        removed=[cargo_mass_moments(ctx.instance,ctx.ship,names) for _,ctx in self.contexts]
        cargo_mass=np.asarray([float(T.mass[b]) for b,q in solver.pair])
        cargo_moments=np.zeros((len(solver.pair),3))
        for v,(b,q) in enumerate(solver.pair):
            j,k=solver.slot_page[q][0]
            x,y=model._slot_xy(model.pages[j],k)
            # Exact fixed-prefix body support. Free-support reference height is
            # a linear search approximation when free heights differ; this
            # recorded cohort uses uniform ordinary-height ISO20 bodies.
            z=solver.slot_cg_z(b,q)
            cargo_moments[v]=cargo_mass[v]*np.array([x,y,z])
        a,b,names_rows,calibration=_assemble(self.contexts,references=self.references,
            corrections=self.corrections,search_gm_floor=self.gm_floor,
            cargo_mass=cargo_mass,cargo_moments=cargo_moments,removed_cargo=removed,
            auxiliary_quantum=self.quantum if self.equality_grid_reserve else None,
            tank_reference_mode=self.tank_mode,tcg_halfwidth_m=self.tcg_halfwidth_m,
            kg_caps_m=self.kg_caps_m or None)
        npairs=len(solver.pair);naux=len(self.quantum)
        if a.shape!=(len(b),npairs+naux):raise ValueError('joint assembly dimensions differ from pair/auxiliary split')
        metadata=dict(self.metadata,assembly=calibration,modeled_box_count=len(model.boxes),
                      pair_count=npairs,removed_cargo_kg_kgm=[v.tolist() for v in removed],
                      policy=('affine loading rows hard; hull equalities have explicit auxiliary-grid construction band' if self.equality_grid_reserve else 'affine loading rows hard; hull equalities exact with continuous auxiliary variables'))
        spec=dict(A_pair=csc_matrix(a[:,:npairs]),A_aux=csc_matrix(a[:,npairs:]),b=np.asarray(b,float),
                  names=list(names_rows),bounds=list(self.bounds),quantum=self.quantum.copy(),cost=self.cost.copy(),
                  aux_integrality=self.aux_integrality.copy(),
                  metadata=metadata,tank_ids=list(self.tank_ids),capacity=self.capacity.copy(),decode=self.candidate_recipe)
        self.last_spec=spec
        return spec

    def __call__(self,solver):
        return self.build(solver)

    def candidate_recipe(self,auxiliary_values):
        """Decode adjustable tank fractions; fixed tanks remain inherited."""
        from stowage.model import BallastCondition
        values=np.asarray(auxiliary_values,float)
        if values.shape!=(len(self.quantum),) or not np.isfinite(values).all():
            raise ValueError('joint auxiliary vector has wrong shape or nonfinite values')
        fractions=values[:len(self.tank_ids)]
        if (fractions < -1e-7).any() or (fractions > 1+1e-7).any():
            raise ValueError('joint adjustable tank fraction lies outside capacity')
        if getattr(self,'tank_mode','half')=='pressed':
            if np.max(np.abs(fractions-np.rint(fractions)),initial=0.)>1e-6:
                raise ValueError('pressed-or-empty recipe requires binary tank fractions')
            fractions=np.rint(fractions)
        return BallastCondition(tank_masses_kg={tid:float(cap*fraction) for tid,cap,fraction in
            zip(self.tank_ids,self.capacity,np.clip(fractions,0.,1.))},source='finisher')

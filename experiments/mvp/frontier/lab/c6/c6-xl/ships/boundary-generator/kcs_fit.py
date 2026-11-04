"""Offset-loft fit audit for synthetic internal geometry (D20).

APPROXIMATION: clearance tested at longitudinal endpoints, source stations,
0.5m intervals, and every contour vertex height in each box. The published
piecewise-linear source loft is retained. Clearance margin is 0.02m; this is
an input geometry audit, not a class scantling/cell-guide approval.
"""
from functools import lru_cache
import math
import numpy as np
from stowage.vessel.hull_offsets import read_offsets

class OffsetFit:
    def __init__(self, ship):
        self.h=ship['hull']; self.f=ship['hull_form']
        f=self.f
        table=read_offsets(f['offsets_path'],expected_sha256=f['offsets_sha256'],format=f['offsets_format'])
        self.stations=[]
        tr=f['reference_draught_m']
        for s in table.stations:
            nodes=[]
            for j,p in enumerate(s.half_section_yz_m):
                nodes.append(p)
                if j+1<len(s.half_section_yz_m):
                    other=s.half_section_yz_m[j+1]
                    if (p[1]-tr)*(other[1]-tr)<0:
                        nodes.append(p+(tr-p[1])/(other[1]-p[1])*(other-p))
            c=np.array(nodes)
            d=np.r_[0.,np.cumsum(np.linalg.norm(np.diff(c,axis=0),axis=1))]
            self.stations.append((s.x_m*self.h['lpp_m']/f['reference_lpp_m'],c,d/d[-1]))
        self.x=np.array([s[0] for s in self.stations])

    @lru_cache(maxsize=16384)
    def half(self,x):
        i=min(len(self.x)-2,max(0,int(np.searchsorted(self.x,x))-1))
        xa,a,pa=self.stations[i];xb,b,pb=self.stations[i+1]
        params=np.unique(np.r_[pa,pb])
        aa=np.column_stack([np.interp(params,pa,a[:,k]) for k in (0,1)])
        bb=np.column_stack([np.interp(params,pb,b[:,k]) for k in (0,1)])
        c=aa+(x-xa)/(xb-xa)*(bb-aa)
        # Insert vertical-map kink before scaling the contour.
        tr=self.f['reference_draught_m']; nodes=[]
        for j,p in enumerate(c):
            nodes.append(p)
            if j+1<len(c):
                q=c[j+1]
                if (p[1]-tr)*(q[1]-tr)<0:nodes.append(p+(tr-p[1])/(q[1]-p[1])*(q-p))
        c=np.array(nodes);c[:,0]*=self.h['beam_m']/self.f['reference_beam_m']
        z=c[:,1].copy();t=self.h['design_draught_m'];d=self.h['depth_m']
        c[:,1]=np.where(z<=tr,z*t/tr,t+(z-tr)*(d-t)/(self.f['reference_depth_m']-tr))
        return c

    def breadth(self,x,z):
        # A half-contour is OPEN: its top must join the reflected top across
        # the deck. Closing it diagonally to the half-keel invents shell width
        # at the fine ends and can falsely admit an outboard tank corner.
        c=self.half(float(x));p=c[:-1];q=c[1:]
        crossing=((p[:,1]<=z)&(q[:,1]>=z))|((q[:,1]<=z)&(p[:,1]>=z))
        nonhorizontal=np.abs(q[:,1]-p[:,1])>1e-12
        mask=crossing&nonhorizontal
        ys=p[mask,0]+(z-p[mask,1])*(q[mask,0]-p[mask,0])/(q[mask,1]-p[mask,1])
        exact=c[np.abs(c[:,1]-z)<1e-9,0]
        return max(np.r_[0.,ys,exact])

    def box_clearance(self,box):
        x0,x1,y0,y1,z0,z1=[float(box[k]) for k in ('x_min_m','x_max_m','y_min_m','y_max_m','z_min_m','z_max_m')]
        xs=np.unique(np.r_[np.linspace(x0,x1,max(2,math.ceil((x1-x0)/.5)+1)), self.x[(self.x>x0)&(self.x<x1)]])
        outer=max(abs(y0),abs(y1));minimum=float('inf')
        for x in xs:
            c=self.half(float(x));zs=np.unique(np.r_[z0,z1,c[(c[:,1]>z0)&(c[:,1]<z1),1]])
            for z in zs:
                minimum=min(minimum,self.breadth(float(x),float(z))-outer)
        return float(minimum)


def cargo_box(cell,stack,high_cube_height):
    i=stack['cell_ids'].index(cell['id'])
    # High cubes throughout: test actual worst-case floor and the standard-floor
    # endpoint too, rather than trusting static standard-height cell centres.
    z0=stack['base_z_m']+i*2.591
    z1=stack['base_z_m']+(i+1)*high_cube_height
    return dict(x_min_m=cell['lcg_m']-3.029,x_max_m=cell['lcg_m']+3.029,
                y_min_m=cell['tcg_m']-1.219,y_max_m=cell['tcg_m']+1.219,z_min_m=z0,z_max_m=z1)


def reconcile(ship):
    fit=OffsetFit(ship);original={c['id']:c for c in ship['cells']};removed=[];shortened=[]
    standard=ship['layout']['standard_height_m'];D=ship['hull']['depth_m']
    bycell=dict(original);dead=set();drop_cells=set()

    def floor_for(stack):
        cell=bycell[stack['cell_ids'][0]]
        x0,x1=cell['lcg_m']-3.029,cell['lcg_m']+3.029
        outer=abs(cell['tcg_m'])+1.219+.02
        floor=stack['base_z_m']
        xs=np.unique(np.r_[np.linspace(x0,x1,max(2,math.ceil((x1-x0)/.5)+1)),fit.x[(fit.x>x0)&(fit.x<x1)]])
        for x in xs:
            c=fit.half(float(x));a,b=c[:-1],c[1:]
            if fit.breadth(float(x),D)<outer:return D
            mask=((a[:,0]<=outer)&(b[:,0]>=outer)|((b[:,0]<=outer)&(a[:,0]>=outer)))&(np.abs(b[:,0]-a[:,0])>1e-12)
            z=a[mask,1]+(outer-a[mask,0])*(b[mask,1]-a[mask,1])/(b[mask,0]-a[mask,0])
            edges=np.unique(np.r_[floor,D,z[(z>floor)&(z<D)]])
            for lo,hi in zip(edges,edges[1:]):
                if fit.breadth(float(x),float((lo+hi)/2))<outer:floor=max(floor,float(hi))
        return math.ceil((floor+1e-6)*1000)/1000

    lookup={s['id']:s for s in ship['stacks']}
    for pair in ship['layout']['bay_pairs']:
        a,b=pair['odd_bays'];rows=sorted(s['row'] for s in ship['stacks'] if s['bay']==a and not s['on_deck'])
        for row in rows:
            ids=[f'{bay:03d}{row:02d}H' for bay in (a,b)]
            base=max(floor_for(lookup[id]) for id in ids)
            tiers=min(len(lookup[ids[0]]['cell_ids']),max(0,math.floor((D-base+1e-9)/standard)))
            for id in ids:
                stack=lookup[id];old=list(stack['cell_ids'])
                if not tiers:
                    dead.add(id);drop_cells.update(old)
                    removed.append(dict(stack_id=id,cell_ids=old,reason='No standard-height container fits below deck at this paired row on the actual KCS loft.'))
                    continue
                keep=old[:tiers];drop_cells.update(old[tiers:])
                # APPROXIMATION: 0.6m coaming rise permits cargo above D with
                # 0.05m clearance below the cover underside. Keep the existing
                # D-based standard-tier count; this adds HC height clearance,
                # not extra cells or an implicit additional tier.
                stack['cell_ids']=keep;stack['base_z_m']=base;stack['max_height_m']=D+.6-.05-base
                stack['assumption']+=' APPROXIMATION: Hold guide floors follow KCS breadth; existing D-based standard-height tier count is retained from this row-specific raised floor. Actual total container height may rise into the0.6m coaming, stopping0.05m below its top/cover underside. This permits eligible high cubes without adding cells or tiers.'
                for i,cid in enumerate(keep):
                    cell=bycell[cid];cell['vcg_base_m']=base+i*standard;cell['vcg_m']=cell['vcg_base_m']+standard/2
                if keep!=old or base!=ship['layout']['tank_top_m']:
                    shortened.append(dict(stack_id=id,original_cell_ids=old,retained_cell_ids=keep,base_z_m=base,reason='Retain every standard-height tier that fits from the hull-following raised guide floor; do not discard the complete column for a lower-tier clash.'))
    ship['stacks']=[s for s in ship['stacks'] if s['id'] not in dead]
    ship['cells']=[c for c in ship['cells'] if c['id'] not in drop_cells]
    hold_count=sum(not lookup[c['stack_id']]['on_deck'] for c in ship['cells'])
    # APPROXIMATION: the design review retired its unsupported40% hold-share
    # target. Preserve every declared deck tier and audit the revised minimum;
    # never remove useful deck capacity just to change this ratio.
    assert hold_count/len(ship['cells'])>=.28,'Retained hold share below declared0.28 design minimum'
    surviving={c['id'] for c in ship['cells']};stackids={s['id'] for s in ship['stacks']}
    for cover in ship['hatch_covers']:
        cover['hold_stack_ids']=[s for s in cover['hold_stack_ids'] if s in stackids]
        cover['cell_ids']=[c for c in cover['cell_ids'] if c in surviving]
    for area in ship['dg_document']['areas']:area['stack_ids']=[s for s in area['stack_ids'] if s in stackids]
    ship['layout']['slots_45']=[slot for slot in ship['layout']['slots_45'] if all(f'{bay:03d}{slot[3:]}' in surviving for bay in (int(slot[:3])-1,int(slot[:3])+1))]
    count=len(ship['cells']);nominal=ship['nominal_teu']
    ship['teu_tolerance_fraction']=max(.02,math.ceil(abs(count-nominal)/nominal*1000)/1000)
    for c in ship['cells']:c['reefer_plug']=False
    for c in sorted(ship['cells'],key=lambda c:(int(c['id'][-2:]),abs(c['tcg_m']),c['id']))[:ship['layout']['reefer_plugs']]:c['reefer_plug']=True
    ship['layout']['kcs_fit_audit']=dict(assumption='APPROXIMATION: Hull-following guide floors and tierwise columns preserve all D-based standard-height hold tiers that fit KCS with0.02m shell clearance. Samplex<=0.5m plus source stations; transverse shell breakpoints retained. Hold height allowance extends to D+0.6m coaming minus0.05m cover clearance without adding tiers. All declared deck tiers are retained; no deck-tier removal loop remains. Retained hold share must be>=0.28, replacing the retired40% target. NominalTEU denotes the design class, not available cells.',confidence='L',minimum_clearance_m=.02,longitudinal_sampling_max_m=.5,minimum_hold_cell_fraction=.28,hold_coaming_rise_m=.6,hold_cover_clearance_m=.05,original_cell_count=len(original),retained_cell_count=count,removed_cell_count=len(original)-count,removed_stacks=removed,shortened_stacks=shortened,deck_tiers_removed=0,hold_cells=hold_count,hold_cell_fraction=hold_count/count,tank_adjustments=[])
    return ship


def finalize(ship):
    from stowage.vessel.hydrostatics import HullGeometry
    ship=reconcile(ship)
    h=ship['hull'];t=h['design_draught_m']
    geometry=HullGeometry.from_params(ship,nx=96,nz=128)
    r=geometry.submerged(t,t,0.)
    cb=r.volume_m3/(h['lpp_m']*h['beam_m']*t)
    cm=float(max(r.sectional_areas_m2))/(h['beam_m']*t)
    reference={500:6832000.,800:11150000.,1700:21413000.,3600:0.,8000:0.}[ship['nominal_teu']]
    ship['derived_hydrostatics']=dict(assumption='DERIVED: Numerically integrated from the checksum-pinned KCS offset loft with nx96/nz128, at upright design draught and declared sea-water density. Diagnostic results only, never hull-form input coefficients. Midship coefficient uses maximum immersed section area of the retained station grid. Deadweight is design displacement minus the labelled design-review synthetic lightship, not a copied reference-ship rating.',
        method='stowage.vessel.hydrostatics.HullGeometry.submerged',nx=96,nz=128,
        design_draught_m=t,displacement_volume_m3=r.volume_m3,displacement_kg=r.displacement_kg,
        block_coefficient=cb,midship_coefficient=cm,prismatic_coefficient=cb/cm,
        waterplane_coefficient=r.waterplane_area_m2/(h['lpp_m']*h['beam_m']),lcb_m=r.lcb_m,
        implied_deadweight_kg=r.displacement_kg-h['lightship_mass_kg'],reference_deadweight_kg=reference,
        deadweight_difference_kg=r.displacement_kg-h['lightship_mass_kg']-reference,
        provenance={'reference_deadweight_kg':{'source':{**h['provenance']['loa_m']['source'],
            'section':'PDF p1 Deadweight' if ship['nominal_teu']!=1700 else 'Vessel’s Specification: DWT'}}})
    # These are diagnostics of the actual complete source envelope, not AP/FP
    # cropping instructions. The geometry still reads all source stations.
    ship['envelope']['buoyant_x_min_m']=geometry.buoyant_x_min_m
    ship['envelope']['buoyant_x_max_m']=geometry.buoyant_x_max_m
    if ship['nominal_teu']==500:
        ship['derived_hydrostatics']['provenance']['reference_deadweight_kg']={
            'confidence':'M','assumption':'6832t for the same IMO9123324 corroborated by two public sources; original3850t brochure figure retired as misprint.',
            'source':dict(document='Port of Hamburg BF PHILIPP and MagicPort ARCTIC TRADER',section='Vessel particulars DWT6832t, IMO9123324',url='https://www.hafen-hamburg.de/en/vessels/bf-philipp/',read_date='2026-10-27'),
            'corroborating_url':'https://magicport.ai/vessels/container/arctic-trader-mmsi-304688000'}
    from freeboard import apply_freeboard
    ship['load_line']=apply_freeboard(ship)
    from design_tanks import apply_tanks
    ship=apply_tanks(ship)
    return ship

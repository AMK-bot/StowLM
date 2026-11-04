"""D24 hull-following synthetic tanks; generator-only code, no downloaded code.

Contract for the 2.1 tank API: geometry.shape='hull_sections'; x_m and
integration_weights_m are matching quadrature abscissae/weights, sections_yz_m
is [section][polygon][vertex][y,z]. Each polygon is counterclockwise. Bounds
x/y/z_min/max_m accompany the section solid. Empty sections are omitted.
APPROXIMATION: finite Gauss section quadrature of the production KCS loft;
clipping and upright polygon moments use production hydrostatics functions.
This is explicit finite-angle geometry, never an equivalent rectangular prism.
"""
from __future__ import annotations
import math
import numpy as np
from shapely.geometry import Polygon, box
from shapely.affinity import translate
from shapely import union_all, constrained_delaunay_triangles
from stowage.vessel.hydrostatics import HullGeometry, _clip, _moments, _water_segments
from kcs_fit import OffsetFit

ASSUMPTION = ('APPROXIMATION: Synthetic hull-following tank arrangement, confidence L; capacity targets confidence M from experiments/physics-review/design/sourced-values.md section 3. Explicit checksum-pinned KCS sections clipped inside shell and clear of cargo/ER/consumable tank boxes. Unbaffled liquid; upright sounding table computed by production polygon clipping/moment functions. Section Gauss quadrature includes all KCS source station breaks and is approximate; no equivalent rectangular-prism geometry is used. Uniform Euclidean shell allowance 0.02 m, centreline separation 0.04 m. Geometry is retained for finite-angle liquid clipping.')
SOURCE = dict(document='Arctic Trader and Dina Trader particulars; ship-design review', section='Tank capacities; experiments/physics-review/design/sourced-values.md section 3 and after.md section 1', url='https://mmred.no/images/filer/Arctic-Trader-Brochure-Rev-0-170226.pdf', read_date='2026-10-27')

def _bound(poly,y0,y1,z0,z1):
    for ny,nz,h in [(-1,0,-y0),(1,0,y1),(0,-1,-z0),(0,1,z1)]:
        if len(poly)<3:return np.empty((0,2))
        poly=_clip(poly,ny,nz,h)
    return poly if len(poly)>=3 and _moments(poly)[0]>1e-10 else np.empty((0,2))

def _solid(xs,ws,polys,bounds):
    entries=[(float(x),float(w),[p.tolist() for p in pp if len(p)>=3]) for x,w,pp in zip(xs,ws,polys) if any(len(p)>=3 for p in pp)]
    if not entries:return None
    return dict(shape='hull_sections',x_m=[e[0] for e in entries],integration_weights_m=[e[1] for e in entries],sections_yz_m=[e[2] for e in entries],**bounds)

def _capacity(g):
    return float(sum(w*sum(_moments(np.asarray(p))[0] for p in pp) for w,pp in zip(g['integration_weights_m'],g['sections_yz_m'])))

def sounding(g,f):
    cap=_capacity(g);volume=cap*f
    xs=np.asarray(g['x_m']);ws=np.asarray(g['integration_weights_m']);sections=[[np.asarray(p) for p in pp] for pp in g['sections_yz_m']]
    lo,hi=g['z_min_m'],g['z_max_m']
    for _ in range(42):
        level=(lo+hi)/2;tot=np.zeros(4)
        for x,w,pp in zip(xs,ws,sections):
            m=sum((_moments(_clip(p,0,1,level)) for p in pp),np.zeros(3));tot+=w*np.array([m[0],x*m[0],m[1],m[2]])
        if tot[0]<volume:lo=level
        else:hi=level
    if f==0:
        # Consistent zero-volume convention, not the limit of fluid CG.
        return dict(volume_m3=0.,lcg_m=(g['x_min_m']+g['x_max_m'])/2,tcg_m=(g['y_min_m']+g['y_max_m'])/2,vcg_m=g['z_min_m'],fsm_m4=0.)
    centroid=tot[1:]/tot[0];area=my=my2=0.
    if 0<f<1:
        for w,pp in zip(ws,sections):
            for p in pp:
                for a,b in _water_segments(p,0,1,level):
                    yl,yh=sorted((a[0],b[0]));area+=w*(yh-yl);my+=w*(yh*yh-yl*yl)/2;my2+=w*(yh**3-yl**3)/3
    return dict(volume_m3=volume,lcg_m=float(centroid[0]),tcg_m=float(centroid[1]),vcg_m=float(centroid[2]),fsm_m4=max(0.,float(my2-my*my/area)) if area else 0.)

def apply_tanks(ship):
    h=ship['hull'];L,B,D=(h[k] for k in ('lpp_m','beam_m','depth_m'));base=min(s['base_z_m'] for s in ship['stacks'] if not s['on_deck'])
    # Instantiate production geometry as the explicit source contract. OffsetFit
    # evaluates that same pinned loft at quadrature nodes including cargo edges.
    HullGeometry.from_params(ship,nx=16,nz=32)
    fit=OffsetFit(ship);stacks={s['id']:s for s in ship['stacks']};cargo=[]
    # Full guide/hatchway columns, including unoccupied tiers, remain cargo
    # space. A tank cannot occupy the space above a shortened hold stack.
    for stack in ship['stacks']:
        if stack['on_deck']:continue
        for cid in stack['cell_ids']:
            c=next(c for c in ship['cells'] if c['id']==cid)
            cargo.append((c['lcg_m']-3.029-.02,c['lcg_m']+3.029+.02,abs(c['tcg_m'])+1.219+.02,stack['base_z_m']-.02,D+.6))
    # A paired 40ft container bridges the 76mm gap between its 20ft support
    # footprints. Reserve its complete guide/hatchway prism, never that gap.
    for pair in ship['layout']['bay_pairs']:
        bays=set(pair['odd_bays']);rows={s['row'] for s in ship['stacks'] if s['bay'] in bays and not s['on_deck']}
        for row in rows:
            ss=[s for s in ship['stacks'] if s['bay'] in bays and s['row']==row and not s['on_deck']]
            cc=[c for c in ship['cells'] if c['stack_id'] in {s['id'] for s in ss}]
            if len(ss)!=2:raise ValueError('paired hold columns required')
            cargo.append((pair['lcg_m']-6.096-.02,pair['lcg_m']+6.096+.02,max(abs(c['tcg_m']) for c in cc)+1.219+.02,min(s['base_z_m'] for s in ss)-.02,D+.6))
    hold0=min(c[0] for c in cargo);hold1=max(c[1] for c in cargo)
    retained=[]
    oil_bottom=max(.76,min(B/20,2.))
    oil_source=dict(document='IMO MEPC.141(54), MARPOL Annex I regulation 12A',section='Annex, Reg12A.1,3,6-7; PDF pages3-5 (printed pages2-4)',url='https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MEPCDocuments/MEPC.141%2854%29.pdf',read_date='2026-10-27')
    oil_envelopes={}
    # All boundaries included in quadrature; cargo geometry is constant per slab.
    breaks=sorted(set([0.,.025*L,.08*L,.10*L,.11*L,.12*L,.35*L,.45*L,.55*L,.65*L,.88*L,.92*L,.97*L,hold0,hold1]+[q for c in cargo for q in c[:2]]+[q for t in retained for q in (t['geometry']['x_min_m'],t['geometry']['x_max_m'])]+list(fit.x)))
    breaks=[x for x in breaks if 0<=x<=.97*L]
    # Source and design boundaries can coincide to floating-point roundoff.
    # Coalesce only sub-nanometric duplicates; otherwise a nominal zero-width
    # panel produces repeated rounded Gauss abscissae and an invalid table.
    coalesced=[]
    for x in breaks:
        if not coalesced or x-coalesced[-1]>1e-8:coalesced.append(x)
    breaks=coalesced;xs=[];ws=[];slabs=[]
    for a,b in zip(breaks,breaks[1:]):
        for aa,bb in zip(np.linspace(a,b,max(2,math.ceil((b-a)/1.5)+1))[:-1],np.linspace(a,b,max(2,math.ceil((b-a)/1.5)+1))[1:]):
            for sign in [-1,1]:xs.append((aa+bb)/2+sign*(bb-aa)/(2*math.sqrt(3)));ws.append((bb-aa)/2);slabs.append((aa,bb))
    def free_polys(x,z0,z1):
        half=fit.half(float(x)).copy()
        source=Polygon(np.vstack([half,half[::-1]*[-1,1]]))
        if not source.is_valid:
            # Fine stern includes a doubled centreline stalk below its raised
            # keel. Remove zero-area self-touching linework, never repair a
            # finite-area crossing silently.
            cleaned=source.buffer(0)
            if abs(cleaned.area-source.area)>1e-8*max(1.,source.area):
                raise ValueError(f'Finite-area invalid KCS source polygon at x={x}')
            source=cleaned
        # APPROXIMATION: 20 mm uniform Euclidean shell allowance. Buffer the
        # WHOLE source polygon, including raised-keel stern: subtracting y
        # from a half contour can move its lower boundary outside the hull.
        oil_envelopes[float(x)]=source.buffer(-.7601,join_style=2).intersection(translate(source,yoff=oil_bottom))
        inset=source.buffer(-.02,join_style=2)
        pieces=list(inset.geoms) if inset.geom_type=='MultiPolygon' else [inset]
        hulls=[]
        for piece in pieces:
            if piece.is_empty:continue
            poly=np.asarray(piece.exterior.coords[:-1])
            hulls.append(poly if _moments(poly)[0]>0 else poly[::-1])
        cuts=sorted(set([z0,z1]+[z for c in cargo if c[0]<x<c[1] for z in c[3:] if z0<z<z1]+[z for t in retained if t['geometry']['x_min_m']<x<t['geometry']['x_max_m'] for z in (t['geometry']['z_min_m'],t['geometry']['z_max_m']) if z0<z<z1]))
        out=[]
        for a,b in zip(cuts,cuts[1:]):
            z=(a+b)/2;yi=.02
            if .025*L<x<.11*L and base<z<D:yi=max(yi,.08*B+.02)
            yi=max([yi]+[c[2] for c in cargo if c[0]<x<c[1] and c[3]<z<c[4]])
            for t in retained:
                g=t['geometry']
                if g['x_min_m']<x<g['x_max_m'] and g['z_min_m']<z<g['z_max_m']:yi=max(yi,abs(g['y_min_m'])+.02,abs(g['y_max_m'])+.02)
            for hull in hulls:
                p=_bound(hull,yi,B/2,a,b)
                if len(p):out.append(p)
        return out
    # Disjoint region allocation: peaks replace DB/wing beyond their boundaries.
    pools={k:[] for k in ['DB-A','DB-M','DB-F','WING-A','HEEL','WING-F','FWD-DEEP','FPT','APT']}
    for x in xs:
        for k in pools:pools[k].append([])
        if .10*L<x<.88*L:
            key='DB-A' if x<.35*L else ('DB-M' if x<.65*L else 'DB-F');pools[key][-1]=free_polys(x,.15,base-.02)
        if .08*L<x<.88*L:
            key='WING-A' if x<.45*L else ('HEEL' if x<.55*L else 'WING-F');pools[key][-1]=free_polys(x,base+.02,D-.02)
        if .88*L<x<.97*L:pools['FWD-DEEP' if x<.92*L else 'FPT'][-1]=free_polys(x,.15,D-.02)
        if 0<x<.08*L:pools['APT'][-1]=free_polys(x,.55*D,D-.02)
    def components(shape):
        if shape.is_empty or shape.area<1e-12:return []
        if shape.geom_type=='Polygon':
            if shape.interiors:
                # Retain every carved void. Exterior-only conversion would
                # silently refill DO/HFO holes and double-count their volume.
                tiles=constrained_delaunay_triangles(shape)
                if abs(sum(q.area for q in tiles.geoms)-shape.area)>1e-9*max(1.,shape.area):
                    raise ValueError('Hole-preserving triangulation volume mismatch')
                return [p for q in tiles.geoms for p in components(q)]
            p=np.asarray(shape.exterior.coords[:-1]);return [p if _moments(p)[0]>0 else p[::-1]]
        return [p for part in shape.geoms for p in components(part)] if hasattr(shape,'geoms') else []

    def clip_parts(pp,y0=-1000.,y1=1000.,z0=-1000.,z1=1000.):
        # Shapely splits disconnected nonconvex clips into actual components;
        # a half-plane ring clip alone can leave zero-width crossing bridges.
        window=box(y0,z0,y1,z1)
        return [q for p in pp for q in components(Polygon(p).intersection(window))]

    def oil_allowed(pp,i):
        return clip_parts([q for p in pp for q in components(Polygon(p).intersection(oil_envelopes[float(xs[i])]))],z0=oil_bottom)

    def carve(target,candidates,allowed=lambda pp,i:pp):
        """Consume exact positive-side volume, retaining explicit differences."""
        wanted=target;out=[[] for _ in xs]
        for k,i in candidates:
            if wanted<=1e-8:break
            pp=allowed(pools[k][i],i)
            if not pp:continue
            v=ws[i]*sum(_moments(p)[0] for p in pp)
            if v<=wanted:take=pp
            else:
                lo,hi=.02,B/2
                for _ in range(40):
                    cut=(lo+hi)/2;take=clip_parts(pp,y1=cut)
                    if ws[i]*sum(_moments(p)[0] for p in take)<wanted:lo=cut
                    else:hi=cut
                take=[p for p in take if len(p)>=3 and _moments(p)[0]>1e-10]
            removed=Polygon() if not take else union_all([Polygon(p) for p in take])
            pools[k][i]=[q for p in pools[k][i] for q in components(Polygon(p).difference(removed))]
            out[i].extend(take);wanted-=ws[i]*sum(_moments(p)[0] for p in take)
        if wanted>1e-5:raise ValueError(f"{ship['id']}: tank target {2*target:.3f}m3 cannot fit, missing {2*wanted:.3f}m3")
        return out

    # Sourced aggregate DO/FW capacities, symmetric P/S tanks. Allocate from
    # aft double bottom first, then aft wing; all volumes removed before WB.
    targets={500:(103.,110.),800:(115.,120.),1700:(250.,200.),3600:(475.,380.),8000:(825.,660.)}[ship['nominal_teu']]
    consumables={}
    for id,target in zip(('DO','FW'),targets):
        candidates=[(k,i) for k in ('DB-A','WING-A') for i in sorted(range(len(xs)),key=lambda i:xs[i]) if pools[k][i]]
        consumables[id]=carve(target/2,candidates,oil_allowed if id=='DO' else lambda pp,i:pp)

    # Size double-side inner skins to sourced aggregate ballast capacity.
    # APPROXIMATION: synthetic inner-skin position, confidence L. Moving it
    # outward preserves cargo clearance; DB and peak tanks remain unchanged.
    wb_target={500:3100.,800:4100.,1700:7000.,3600:14000.,8000:23000.}[ship['nominal_teu']]/2
    wingkeys=('WING-A','HEEL','WING-F')
    fixed=sum(w*sum(_moments(p)[0] for p in pp) for k in pools if k not in wingkeys for w,pp in zip(ws,pools[k]))
    def wing_volume(edge):
        return sum(w*sum(_moments(_clip(p,-1,0,-edge))[0] for p in pp) for k in wingkeys for w,pp in zip(ws,pools[k]))
    available=wing_volume(.02)
    if fixed>wb_target or fixed+available<wb_target:
        raise ValueError(f"{ship['id']}: ballast target {2*wb_target:.1f} cannot fit: fixed {2*fixed:.1f}, max {2*(fixed+available):.1f} m3")
    low,high=.02,B/2
    for _ in range(36):
        edge=(low+high)/2
        if fixed+wing_volume(edge)>wb_target:low=edge
        else:high=edge
    # HFO is carved only AFTER the wing inner skin is solved and strictly
    # INBOARD of it, protected from shell/bottom and outside full hold columns.
    fuel_target={500:554.,800:790.,1700:1500.,3600:2850.,8000:4900.}[ship['nominal_teu']]/2
    fuel={}
    for id,pred in [('FO-F',lambda x:x>=.55*L),('FO-A',lambda x:.08*L<=x<.45*L)]:
        candidates=[(k,i) for k in ('WING-A','WING-F') for i in range(len(xs)) if pred(xs[i]) and pools[k][i]]
        candidates.sort(key=lambda ki:xs[ki[1]],reverse=id=='FO-F')
        def allowed(pp,i):
            return clip_parts(oil_allowed(pp,i),y1=edge-.02)
        fuel[id]=carve(fuel_target/2,candidates,allowed)

    # Clip WB only after inboard HFO allocation; these surfaces are disjoint.
    for k in wingkeys:
        pools[k]=[clip_parts(pp,y0=edge) for pp in pools[k]]
    tanks=[]
    for kind,records in [('ballast',pools),('fuel_oil',fuel),('diesel_oil',{'DO':consumables['DO']}),('fresh_water',{'FW':consumables['FW']})]:
        for id,polys in records.items():
            allp=[p for pp in polys for p in pp]
            if not allp:continue
            yz=np.vstack(allp);indices=[i for i,pp in enumerate(polys) if pp]
            bound=dict(x_min_m=min(slabs[i][0] for i in indices),x_max_m=max(slabs[i][1] for i in indices),y_min_m=float(yz[:,0].min()),y_max_m=float(yz[:,0].max()),z_min_m=float(yz[:,1].min()),z_max_m=float(yz[:,1].max()))
            for tag,sign in [('P',1),('S',-1)]:
                pp=polys if sign==1 else [[(p*[-1,1])[::-1] for p in sec] for sec in polys]
                bb=bound if sign==1 else dict(bound,y_min_m=-bound['y_max_m'],y_max_m=-bound['y_min_m'])
                g=_solid(xs,ws,pp,bb);cap=_capacity(g)
                record=dict(id=id+'-'+tag,kind=kind,assumption=ASSUMPTION,confidence='L',source=SOURCE,content_density_kg_m3={'ballast':1025.,'fuel_oil':970.,'diesel_oil':850.,'fresh_water':1000.}[kind],heated=kind=='fuel_oil',adjustable=kind=='ballast',capacity_m3=cap,x_aft_m=bb['x_min_m'],x_fore_m=bb['x_max_m'],geometry=g,table=[sounding(g,f) for f in [0.,.25,.5,.75,1.]])
                if kind in ('fuel_oil','diesel_oil'):
                    record['oil_protection']=dict(assumption='APPROXIMATION: Conservative whole-contour 0.7601m inward protection (0.1mm numerical allowance above0.76m) intersected with the original source contour translated upward by the statutory bottom height; protection follows raised keel/bilge as well as flat baseline; geometric design screen, not an approved compliance certificate. Rule confidence H; synthetic geometry confidence L.',source=oil_source,side_distance_m=.76,numerical_side_allowance_m=.0001,bottom_height_m=oil_bottom,bottom_reference='local KCS source bottom contour at the same transverse coordinate, not baseline alone',individual_tank_below_500_m3=cap<500.)
                if kind in ('diesel_oil','fresh_water'):
                    n=ship['nominal_teu']
                    cap_source=(dict(document='Arctic Trader brochure',section='PDF p1 Capacities: gas oil 103m3 / fresh water 110m3',url=SOURCE['url'],read_date='2026-10-27') if n==500 else
                        dict(document='Dina Trader brochure',section='PDF p1 Capacities: diesel oil100t / fresh water120m3; 115m3 uses labelled density conversion',url='https://mmred.no/images/Dina_Trader_Brochure-2022.pdf',read_date='2026-10-27') if n==800 else
                        dict(document='Vega Tau / Bardu particulars and sourced-values review',section='experiments/physics-review/design/sourced-values.md section3; DO: Vega Tau gas oil scaled1.2, FW: Bardu200m3 transfer',url='https://www.vega-reederei.de/pdf/tc_VEGA_TAU.pdf' if kind=='diesel_oil' else 'https://na.swireshipping.com/documents/pdfs/MV%20Bardu%20Specifications.pdf?rvn=27',read_date='2026-10-27'))
                    record['capacity_provenance']=dict(assumption='APPROXIMATION: Aggregate sourced anchor capacities transferred to symmetric synthetic tanks; 1700 DO scales Vega Tau by1.2, FW uses Bardu200m3. Source details experiments/physics-review/design/sourced-values.md section3.',confidence='H' if ship['nominal_teu']!=1700 else 'L',source=cap_source,aggregate_target_m3=targets[0 if kind=='diesel_oil' else 1])
                # The production 2.1 hull_sections hook is now available.
                # Persist its exact API outputs; the standalone polygon method
                # above remains an independent derivation and cross-check.
                from stowage.vessel.tanks import TankGeometry
                physical=TankGeometry.from_params(record)
                if not math.isclose(physical.capacity_m3,cap,rel_tol=1e-10,abs_tol=1e-10):
                    raise ValueError(f"{record['id']}: production tank capacity mismatch")
                api_table=[physical.sounding(physical.capacity_m3*f).model_dump() for f in (0.,.25,.5,.75,1.)]
                for independent,actual in zip(record['table'],api_table):
                    for field in ('lcg_m','tcg_m','vcg_m','fsm_m4'):
                        if not math.isclose(independent[field],actual[field],rel_tol=1e-7,abs_tol=1e-7):
                            raise ValueError(f"{record['id']}: independent/production sounding {field} mismatch")
                api_table[-1]['volume_m3']=cap
                record['table']=api_table
                tanks.append(record)
    ship['tanks']=tanks
    ship['heat_sources']=[x for x in ship['heat_sources'] if x['kind']!='heated_tank']+[dict(id='heat-'+t['id'],kind='heated_tank',assumption=ASSUMPTION,**{k:t['geometry'][k] for k in ('x_min_m','x_max_m','y_min_m','y_max_m','z_min_m','z_max_m')}) for t in tanks if t['heated']]
    ship['tank_design']=dict(assumption=ASSUMPTION,confidence='M',ballast_capacity_m3=sum(t['capacity_m3'] for t in tanks if t['kind']=='ballast'),hfo_capacity_m3=sum(t['capacity_m3'] for t in tanks if t['kind']=='fuel_oil'),target_ballast_m3={500:3100.,800:4100.,1700:7000.,3600:14000.,8000:23000.}[ship['nominal_teu']],target_hfo_m3=2*fuel_target,diesel_oil_capacity_m3=sum(t['capacity_m3'] for t in tanks if t['kind']=='diesel_oil'),fresh_water_capacity_m3=sum(t['capacity_m3'] for t in tanks if t['kind']=='fresh_water'),wing_inner_edge_m=float(edge),hfo_max_abs_tcg_bound_m=max(t['geometry']['y_max_m'] for t in tanks if t['kind']=='fuel_oil'))
    return ship

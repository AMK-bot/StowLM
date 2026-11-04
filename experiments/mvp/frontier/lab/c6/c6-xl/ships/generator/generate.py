"""Rebuild labelled synthetic particulars with D20 checksum-pinned KCS geometry.

APPROXIMATION: Published KCS offsets are scaled to feeder dimensions; retain
the complete source envelope and seal its 16m cutoff at the target deck.
No downloaded code is imported or executed. Production geometry and the local
offset-fit audit compute resulting coefficients and remove invalid hold cells.
"""
from __future__ import annotations
import json
import math
from pathlib import Path

ROOT = Path('.')
OUT = Path(__file__).resolve().parents[1]
DATE = '2026-10-27'
MANIFEST = json.loads((ROOT/'data/raw/reference-ships/MANIFEST').read_text())
URLS = {r['file']:r['url'] for r in MANIFEST}
SYNTHETIC = 'APPROXIMATION: Synthetic new-build parameter, not a surveyed vessel. Derived by the explicitly labelled component method in experiments/fleet-params/generate.py and params/fleet/README.md.'

def source(file, section):
    return dict(document=file,section=section,url=URLS[file],read_date=DATE)

def value(v):
    if isinstance(v, bool): return str(v).lower()
    if isinstance(v, str): return json.dumps(v,ensure_ascii=False)
    if isinstance(v,int): return repr(int(v))
    if isinstance(v,float): return repr(float(v))
    if isinstance(v,list): return '['+', '.join(value(x) for x in v)+']'
    if isinstance(v,dict): return '{ '+', '.join(f'{k} = {value(x)}' for k,x in v.items())+' }'
    raise TypeError(v)

def write_toml(data,path):
    # Inline tables keep every component's inherited provenance adjacent to its values.
    lines=['# Synthetic fleet particulars; rebuild with experiments/fleet-params/generate.py.']
    for k,v in data.items():
        if isinstance(v,dict):
            lines += ['',f'[{k}]']+[f'{a} = {value(b)}' for a,b in v.items()]
        elif isinstance(v,list) and v and isinstance(v[0],dict):
            for item in v:
                lines += ['',f'[[{k}]]']+[f'{a} = {value(b)}' for a,b in item.items()]
        else: lines.append(f'{k} = {value(v)}')
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text('\n'.join(lines)+'\n')
    temporary.replace(path)

def windage_inputs(L, D, T, coamings):
    # APPROXIMATION: Synthetic solid aft deckhouse, no mast/rail permeability.
    # Its top clears the existing D+18m bridge eye. The funnel is contained in
    # this lateral silhouette. Buoyancy still excludes the deckhouse entirely.
    # APPROXIMATION: conservative rectangular lateral bounding silhouette of
    # the complete KCS x envelope, including source AP/FP overhangs. It does
    # not claim to integrate the shaped stern/stem profile in wind projection.
    rectangles = [dict(id='hull_freeboard', x_min_m=-6.*L/230., x_max_m=236.9*L/230.,
                       z_min_m=T, z_max_m=D),
                  dict(id='deckhouse', x_min_m=.025*L, x_max_m=.11*L,
                       z_min_m=D, z_max_m=D+19.)]
    rectangles += [dict(id=c['id'], **{k:c[k] for k in
                    ('x_min_m','x_max_m','z_min_m','z_max_m')}) for c in coamings]
    # Exact union of axis-aligned projected rectangles. Both sides' coamings
    # overlap in projection and must count only once, as must shielded cargo.
    xs=sorted({r[k] for r in rectangles for k in ('x_min_m','x_max_m')})
    area=moment=0.
    for x0,x1 in zip(xs,xs[1:]):
        intervals=sorted((r['z_min_m'],r['z_max_m']) for r in rectangles
                         if r['x_min_m'] <= (x0+x1)/2 <= r['x_max_m'])
        merged=[]
        for z0,z1 in intervals:
            if merged and z0<=merged[-1][1]: merged[-1][1]=max(merged[-1][1],z1)
            else: merged.append([z0,z1])
        for z0,z1 in merged:
            area+=(x1-x0)*(z1-z0)
            moment+=(x1-x0)*(z1*z1-z0*z0)/2
    return dict(assumption='APPROXIMATION: Exact union of DECLARED rectangular upright lateral silhouettes at design draught: conservative freeboard bounding rectangle spanning full scaled KCS source x=-6..236.9m, aft deckhouse 0.025-0.11Lpp from D to D+19m, and secured hatch/coaming boxes. The shaped stem/stern lateral profile is NOT integrated for windage. Funnel lies within deckhouse silhouette. No cargo included. Deckhouse remains excluded from buoyancy; its mass is already within estimated lightship. No extra slender ventilation-trunk/mast/rail projected area assumed.',
                reference_waterline_m=T,reference_underwater_centroid_z_m=T/2,
                centroid_z_m=moment/area,area_m2=area,first_moment_m3=moment,
                projected_rectangles=rectangles,
                lever_definition='centroid_z_m minus half design draught; IS Code A2.3.2 approximate underwater lateral-area centroid, not height above baseline or above waterline')

def dg_inputs(stacks, pairs):
    # APPROXIMATION: Declared synthetic equipment and operating permissions,
    # not a real Administration-issued Document of Compliance.
    deck=['1.4S','2.1','2.2','2.3','3','4.1','4.2','4.3','5.1','5.2','6.1','8','9']
    hold=['2.2','3','4.1','4.2','4.3','5.1','6.1','8','9']
    forward=['2.2','4.1','6.1','8','9']
    forms=['packaged_goods_in_closed_freight_containers']
    areas=[dict(id='WEATHER-DECK',kind='deck',classes_allowed=deck,
                packing_forms_allowed=forms,stack_ids=[],
                assumption='APPROXIMATION: Weather-deck packaged DG authorization. No explosives except 1.4S, no infectious or radioactive cargo acceptance program. Exact DGL stowage/heat/accommodation/segregation rules remain mandatory.',
                equipment_assumptions='Synthetic fire-main coverage and emergency response equipment; no explosives magazine. 1.4S authorization does not bypass currently unsupported class-1 stowage-category checks.')]
    for p in range(pairs):
        areas.append(dict(id=f'HOLD-{p+1:02d}',kind='hold',hold_id=f'HOLD-{p+1:02d}',
            even_bay=4*p+2,stack_ids=[],classes_allowed=forward if p==0 else hold,
            packing_forms_allowed=forms,
            assumption='APPROXIMATION: One declared cargo hold per 40ft bay pair with its two hatch pontoons. Forward hold has reduced authorization excluding flammable liquids, water-reactive, self-heating and oxidizing classes. All holds exclude explosives, flammable/toxic gases, organic peroxides, infectious and radioactive cargo. These are synthetic choices, not typical certified feeder facts.',
            equipment_assumptions='Synthetic water supply, smoke detection, fixed fire extinguishing, protected electrical equipment, suitable separate bilge drainage, PPE and six empty-hold air changes per hour with appropriate upper/lower non-sparking extraction. No class 2.1/2.3 service. SOLAS II-2/19.3 design premise only; no approval claimed.',
            ventilation_air_changes_per_hour=6.))
    byid={a['id']:a for a in areas}
    for stack in stacks:
        id='WEATHER-DECK' if stack['on_deck'] else f"HOLD-{(stack['bay']-1)//4+1:02d}"
        area=byid[id];area['stack_ids'].append(stack['id'])
        stack['dg_doc_area_id']=id;stack['dg_classes_allowed']=list(area['classes_allowed'])
    return dict(assumption='APPROXIMATION: Synthetic SOLAS II-2/19 DoC-style space authorization, not a certificate or automatic shipment clearance. SOLAS Table 19.3 specifies equipment by cargo/form, not permission alone. Exclusion of classes 6.2/7 is operating policy, not a claim that SOLAS bans them. Physical packing forms and all subsidiary risks must be independently checked. Per-hold grouping is not evidence of an IMDG segregation bulkhead.',
        source=dict(document='SOLAS II-2/19 and IMO MSC.1/Circ.1266/Rev.1',
                    section='SOLAS Tables 19.1/19.3 and 19.4; circular Annex 1 and appendix space/form authorization',
                    url='https://puc.overheid.nl/nsi/doc/PUC_1949_14/2/',read_date=DATE),
        guidance_urls=['https://www.imorules.com/solas/03_chapter_II-2.html','https://www.mardep.gov.hk/filemanager/en/share/msnote/pdf/msin2580.pdf'],
        packing_forms_allowed=forms,
        packing_forms_prohibited=['bulk','portable_tank','tank_container','open_freight_container'],areas=areas)

def downflooding_openings(L,B,D,layout):
    # APPROXIMATION: Positions are lower edges, not opening-centre coordinates.
    # Vents are in 14m bay-pair walkways, outside 13.716m cargo footprints.
    openings=[]
    def add(id,kind,x,y,z):
        openings.append(dict(id=id,kind=kind,x_m=x,y_m=y,z_m=z,
            closure='not_weathertight_closable',progressive_flooding=True,
            coordinate_reference='lowest_unprotected_aperture_edge',
            assumption='APPROXIMATION: Synthetic unprotected ventilation aperture communicating with internal space; no weathertight closure credited. Coordinates are its lowest progressive-flooding edge. Hold vent outlets stand 4.5m above deck in bay walkways, engine-room air inlets 6m above deck on the aft deckhouse. Not measured feeder arrangements. IS Code A2.3.1 downflooding definition applies; reevaluate immersion at actual heel and trim.'))
    for p,pair in enumerate(layout['bay_pairs']):
        for side,sign in [('P',1),('S',-1)]:
            add(f'HOLD-{p+1:02d}-VENT-{side}','hold_ventilator',pair['lcg_m']+7.,sign*.4*B,D+4.5)
    for side,sign in [('P',1),('S',-1)]:
        add(f'ER-AIR-{side}','engine_room_air_inlet',.075*L,sign*.08*B,D+6.)
    return openings

def build(n, loa,L,B,D,T,pairs,rows,hold,deck,plugs,anchor):
    # APPROXIMATION: Exact physics-review component-design values replace the
    # unsuitable geared-ship TEU-only CG regression; no survey is claimed.
    mass={500:2900000.,800:5200000.,1700:8074000.,3600:15500000.,8000:28000000.}[n]
    lcg={500:49.6,800:57.2,1700:73.6,3600:105.8,8000:151.8}[n]
    vcg={500:.72*D,800:7.80,1700:9.24,3600:12.0,8000:14.0}[n]
    edges=[0,.15,.3,.55,.8,1]; fractions=[.20,.15,.25,.25,.15]
    mids=[(edges[i]+edges[i+1])/2 for i in range(5)]
    # APPROXIMATION: Exponential reweighting keeps the longitudinal density
    # positive and exactly fits the review-design total mass and LCG.
    # It does not infer structural detail or an observed weight distribution.
    low,high=-100.,100.
    for _ in range(80):
        tilt=(low+high)/2
        weights=[w*math.exp(tilt*x) for w,x in zip(fractions,mids)]
        if sum(w*x for w,x in zip(weights,mids))/sum(weights)<lcg/L:low=tilt
        else:high=tilt
    weights=[w/sum(weights) for w in weights]
    dist=[[edges[i]*L,edges[i+1]*L,mass*weights[i]] for i in range(5)]
    ls_source=dict(document='Physics review DESIGN exact1.6 table; sourced-values section2; public feeder anchors',
        section='Lightship component build-up and dimensions/operating particulars; no surveyed KG claimed',
        url='https://www.hafen-hamburg.de/en/vessels/bf-philipp/' if n==500 else URLS[anchor],read_date=DATE)
    ls_assumption='APPROXIMATION: Exact physics-review design inputs, mass confidenceM, LCG/VCG confidenceL. Component estimates use UNVERIFIED Watson-Gilfillan/Kupras-type coefficients from secondary sources. TEU-only geared-ship KG regression retired. No surveyed lightship KG or approved loading manual claimed.'
    coordinate_assumption='Design coordinates x forward of AP, z above keel baseline; port/starboard symmetry gives TCG0.'
    provenance={k:{'source':source(anchor,'PDF p1 Dimensions' if anchor.endswith('pdf') else 'Vessel’s Specification')} for k in ['loa_m','beam_m','depth_m','scantling_draught_m']}
    if n!=1700: provenance['lpp_m']={'source':source(anchor,'PDF p1 Dimensions')}
    else: provenance['lpp_m']={'assumption':'APPROXIMATION: Lpp=160m chosen inside 171.99m LOA, leaving 11.99m total overhang while accommodating nine 14m cargo bay pairs; no Lpp reported in listing.'}
    if n==500:
        provenance['depth_m']={'confidence':'M','source':dict(document='MagicPort ARCTIC TRADER IMO9123324; physics review corroboration',section='Vessel particulars: moulded depth9.2m; third-party source beam inconsistency excluded',url='https://magicport.ai/vessels/container/arctic-trader-mmsi-304688000',read_date=DATE),'assumption':'Review-corrected freeboard-deck depth9.2m replaces brochure8.25m; geometry and stability recomputed.'}
    if n==500:
        provenance['scantling_draught_m']={'confidence':'H','source':dict(document='Port of Hamburg BF PHILIPP IMO9123324',section='Vessel particulars draught7.05m; convergence correction item8',url='https://www.hafen-hamburg.de/en/vessels/bf-philipp/',read_date=DATE)}
        provenance['design_draught_m']={'assumption':'APPROXIMATION: Synthetic design draught equals the published7.05m maximum draught.','confidence':'M','source':provenance['scantling_draught_m']['source']}
    for label in provenance.values():
        label.setdefault('confidence','H' if 'source' in label else 'L')
    for key in ('lightship_mass_kg','lightship_lcg_m','lightship_vcg_m'):
        provenance[key]={'source':ls_source,'confidence':'M' if key=='lightship_mass_kg' else 'L','assumption':ls_assumption+(' '+coordinate_assumption if key!='lightship_mass_kg' else '')}
    provenance['lightship_distribution']={'assumption':'APPROXIMATION: Uniform mass on each of five recorded longitudinal intervals. Base weights 0.20/0.15/0.25/0.25/0.15 are exponentially tilted and normalized to match exactly the review-design mass and LCG; no empirical SF/BM distribution or structural-member weights implied.'}
    h=dict(assumption='APPROXIMATION: Principal dimensions use anchor facts or review-corrected depth where provenance overrides. Design draught equals stated max for synthetic design. Seawater density1025kg/m3. Exact physics-review component-design lightship values and moment-matched synthetic distribution; no ship survey claimed.',
        loa_m=loa,lpp_m=L,beam_m=B,depth_m=D,design_draught_m=T,scantling_draught_m=T,
        ap_x_m=0.,fp_x_m=L,water_density_kg_m3=1025.,
        lightship_mass_kg=mass,lightship_lcg_m=lcg,lightship_tcg_m=0.,lightship_vcg_m=vcg,
        lightship_derivation=dict(assumption=ls_assumption,source=ls_source,
            method='physics_review_component_design_2026',confidence_mass='M',confidence_cg='L',mass_kg=mass,lcg_m=lcg,vcg_m=vcg,vcg_uncertainty_m={500:.5,800:.6,1700:.7,3600:1.,8000:1.3}[n],reference_docs=['experiments/physics-review/DESIGN.md#exact-parameter-changes-per-owner','experiments/physics-review/design/sourced-values.md#2-lightship'],
            coordinate_assumption=coordinate_assumption),
        lightship_distribution=dist,hull_offsets_ref=f'params/fleet/feeder_{n}.toml#hull_form',provenance=provenance)
    if n==500:
        h['lightship_derivation']['mass_uncertainty_kg']=250000.
    family=dict(assumption='APPROXIMATION: D20 published KCS offset loft scaled x by Lpp/230 and y by B/32.2; below reference draught scale z by T/10.8, above it map the source16m cutoff to targetD. Reference depth16m is the synthetic source sealing cutoff, not published KCS moulded depth19m. Preserve all source overhangs; no coefficient targets are inputs.',
        source=dict(document='KRISO KCS published body plan kcs.fix and readhull.txt, NMRI Gothenburg2000; Tokyo2015 main particulars',section='Full-scale body-plan coordinates and Main particulars table',url='https://www.nmri.go.jp/study/research_organization/fluid_performance/cfd/cfdws05/gothenburg2000/data/KCS_data/kcs_offsets.ZIP',read_date=DATE),
        family='stowlm_offsets_v1',parent='kcs',offsets_format='kcs_fix',
        offsets_path='data/raw/kcs/kcs.fix',offsets_sha256='aafc7e4f4d773bc50452d397985554c6a20f054150b8865ef70d61f836809190',
        reference_lpp_m=230.,reference_beam_m=32.2,reference_draught_m=10.8,reference_depth_m=16.,
        provenance={'reference_depth_m':{'assumption':'APPROXIMATION: KCS source offsets stop at16m; close that computational cutoff watertight and map it to the target deck. Published moulded depth is19m.'}})
    layout=dict(assumption='APPROXIMATION: Rectangular synthetic LP-style cell grid; ISO external dimensions follow stowage.conventions. Bay pairs spaced 14m to clear 45ft boxes. Individual 20ft cells separated 6.134m in each pair; 40/45 centre is mean of two cells. High cubes require dynamic floor recomputation and stack height check.',
        standard_height_m=2.591,high_cube_height_m=2.896,row_pitch_m=2.5,bay_pitch_m=14.,
        container_lengths_m=[6.058,12.192,13.716],reefer_plugs=plugs,bay_pairs=[],slots_45=[],tank_top_m=1.3 if n==500 else 1.5,
        provenance={'reefer_plugs':{'source':source(anchor,'PDF p1 Reefer plugs/connections' if anchor.endswith('pdf') else 'Vessel’s Specification: Reefer Slots')}})
    cells=[]; stacks=[]; covers=[]; coamings=[]
    base=1.3 if n==500 else 1.5
    hc=D+.6
    # APPROXIMATION: closed steel pontoon depth0.6m above coaming; confidenceL.
    deck_base=D+1.2
    layout.update(hatch_coaming_height_m=.6,hatch_cover_depth_m=.6,hold_cover_clearance_m=.05)
    for p in range(pairs):
        even=4*p+2; odd=[even-1,even+1]
        x=L/2+((pairs-1)/2-p)*14 # BAPLIE bay increases aftward
        nr=8 if n==1700 and p in (0,pairs-1) else rows
        layout['bay_pairs'].append(dict(even_bay=even,odd_bays=odd,lcg_m=x,accepts_45=False))
        for side,sign in [('P',1),('S',-1)]:
            cid=f'HC-{even:03d}-{side}'
            cv=dict(assumption='APPROXIMATION: One steel pontoon per side and 40ft bay; mass=surface area*250kg/m2 included in lightship, not added again. Cover depth0.6m aboveD+0.6 coaming; VCG atD+0.9; confidenceL. All associated hold/deck cell IDs explicitly enumerated.',id=cid,bays=[*odd,even],hold_stack_ids=[],deck_stack_ids=[],cell_ids=[],mass_kg=12.5*(nr/2*2.5)*250,lcg_m=x,tcg_m=sign*nr*2.5/4,vcg_m=D+.9)
            covers.append(cv)
            y0,y1=(0,nr*2.5/2) if sign==1 else (-nr*2.5/2,0)
            coamings.append(dict(id=cid,x_min_m=x-6.25,x_max_m=x+6.25,y_min_m=y0,y_max_m=y1,z_min_m=D,z_max_m=hc))
            for bay in odd:
                for k in range(nr//2):
                    row=2*(k+1) if sign==1 else 2*k+1
                    y=sign*(k+.5)*2.5
                    for on,nt in [(False,hold),(True,deck)]:
                        sid=f'{bay:03d}{row:02d}'+('D' if on else 'H')
                        limit20,limit40=(68000.,68000.) if n==500 and not on else ((45000.,60000.) if n==500 else ((100000.,140000.) if not on else (60000.,90000.)))
                        if n>=1700:
                            limit20,limit40=(130000.,160000.) if not on else ((60000.,90000.) if p==0 else (70000.,100000.))
                        st=dict(assumption='APPROXIMATION: Synthetic structural local stack rating, not S11A global SF/BM. Arctic hold68t/68t and hatch45t/60t (PDFp1) are capacity anchors; larger ships use Vega Tau tanktop100t/140t and hatch60t/90t (PDFp3) as synthetic design targets, not certified limits. 40ft TOTAL rating is HALVED per 20ft support stack; mixed stacks use conservative max_mass_kg. Source URLs in README.',id=sid,bay=bay,row=row,on_deck=on,cell_ids=[],max_mass_kg=min(limit20,limit40/2),max_mass_20_kg=limit20,max_mass_40_kg=limit40/2,max_height_m=nt*2.896,base_z_m=deck_base if on else base,hatch_cover_id=cid)
                        st['confidence']='L' if n==1700 else 'M'
                        if n>=1700:
                            st['assumption']='APPROXIMATION: Physics-review designer-declared hold130/160t and hatch70/100t per20/40ft, foremosthatch60/90t. ConfidenceL, interpolated VegaTau/Bardu ratings under IRS Pt3Ch12 section6.2.4; not a surveyed loading-manual limit. Store HALF the40ft TOTAL rating per20ft support because checker mass allocation is grossmass/2 per cell; no double-halving. Mixed stacks use conservative lower support rating.'
                        stacks.append(st); cv['deck_stack_ids' if on else 'hold_stack_ids'].append(sid)
                        for t in range(nt):
                            tier=82+2*t if on else 2+2*t
                            cellid=f'{bay:03d}{row:02d}{tier:02d}'
                            z=st['base_z_m']+t*2.591
                            cell=dict(id=cellid,stack_id=sid,accepts_20=True,accepts_40=True,accepts_45=on,accepts_high_cube=True,reefer_plug=False,lcg_m=x+(3.067 if bay==odd[0] else -3.067),tcg_m=y,vcg_base_m=z,vcg_m=z+2.591/2)
                            cells.append(cell);st['cell_ids'].append(cellid);cv['cell_ids'].append(cellid)
                            if on and bay==odd[0]:layout['slots_45'].append(f'{even:03d}{row:02d}{tier:02d}')
    # APPROXIMATION: deterministic bottom-up allocation, no claim of copied reefer wiring.
    for cell in sorted(cells,key=lambda c:(int(c['id'][-2:]),abs(c['tcg_m']),c['id']))[:plugs]:cell['reefer_plug']=True
    tanks=[];heat=[]
    # APPROXIMATION: tank volumes arise from nonoverlapping explicit boxes, not anchor totals.
    def tank(id,kind,x0,x1,y0,y1,z0,z1,density,heated=False):
        g=dict(shape='rectangular_prism',x_min_m=x0,x_max_m=x1,y_min_m=y0,y_max_m=y1,z_min_m=z0,z_max_m=z1)
        cap=(x1-x0)*(y1-y0)*(z1-z0)
        table=[dict(volume_m3=cap*f,lcg_m=(x0+x1)/2,tcg_m=(y0+y1)/2,vcg_m=z0+(z1-z0)*f/2,fsm_m4=(x1-x0)*(y1-y0)**3/12 if 0<f<1 else 0.) for f in [0.,.25,.5,.75,1.]]
        tanks.append(dict(assumption='APPROXIMATION: Independently designed rectangular internal tank, no copied real arrangement. Upright exact fill h=fH; centroid at x/y midpoints,z=zmin+h/2; transverse free-surface inertia L*b^3/12 when slack and zero exactly empty/pressed full. Do NOT interpolate endpoint FSM linearly; large-angle fluid transfer requires clipping this geometry. Density is a synthetic content assumption.',id=id,kind=kind,content_density_kg_m3=density,capacity_m3=cap,table=table,heated=heated,x_aft_m=x0,x_fore_m=x1,adjustable=kind=='ballast',geometry=g))
        if heated:heat.append(dict(id='heat-'+id,kind='heated_tank',**{k:v for k,v in g.items() if k!='shape'}))
    for i,(x0,x1) in enumerate([(.2,.4),(.4,.6),(.6,.8)]):
        for side,sign in [('P',1),('S',-1)]:
            y0,y1=(.05*B,.35*B) if sign>0 else (-.35*B,-.05*B)
            tank(f'WB-{i+1}-{side}','ballast',x0*L,x1*L,y0,y1,.15,base-.1,1025.)
    for side,sign in [('P',1),('S',-1)]:
        y0,y1=(.10*B,.18*B) if sign>0 else (-.18*B,-.10*B)
        tank('FO-'+side,'fuel_oil',.06*L,.085*L,y0,y1,1.5,.65*D,970.,True)
    # DO/FW sourced capacity targets are carved as hull_sections by design_tanks;
    # no retained rectangular placeholders or reduced-capacity fallback.
    heat.extend([dict(id='ER',kind='engine_room',x_min_m=.025*L,x_max_m=.11*L,y_min_m=-.08*B,y_max_m=.08*B,z_min_m=base,z_max_m=D),dict(id='funnel',kind='funnel',x_min_m=.05*L,x_max_m=.08*L,y_min_m=-1.,y_max_m=1.,z_min_m=D,z_max_m=D+15)])
    dg_document=dg_inputs(stacks,pairs)
    openings=downflooding_openings(L,B,D,layout)
    if n>=800:
        for opening in openings:
            opening['z_m']+=3.
            opening['assumption']+=' APPROXIMATION: Physics-review design raises this aperture3m; confidenceL, sufficient among tested+0/+3/+5m cases, not proven minimum.'
    # APPROXIMATION: closed cover top D+1.2 contributes windage; the declared
    # KN coaming envelope remains closed atD+0.6, excluding cover buoyancy.
    windage=windage_inputs(L,D,T,[dict(c,z_max_m=deck_base) for c in coamings])
    is_source=dict(document='IMO MSC.267(85), 2008 IS Code',
                   section='Part A 2.2.4, PDF p13; Part A 2.3.2, PDF p14; Part A 2.3.4, PDF p15',
                   url='https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.267(85).pdf',read_date=DATE)
    limits=dict(assumption='APPROXIMATION: Synthetic loading-manual operating envelope, not an approved or empirically established typical limit. Forward draught at least 0.35 design draught prevents extremely light bow conditions but is NOT a verified slamming criterion. Absolute trim <=0.01Lpp and list <=2 degrees are chosen operational planning caps, distinct from stability criteria and dynamic heel. GM 0.15m is only the general IS Code floor; full GZ/weather/free-surface criteria remain mandatory.',
                min_draught_fwd_m=.35*T,max_trim_m=.01*L,max_list_rad=math.radians(2),
                min_gm_m=.15,bilge_keel=False,windage_area_m2=windage['area_m2'],
                windage_lever_m=windage['centroid_z_m']-T/2,
                provenance=dict(
                    min_draught_fwd_m={'assumption':'APPROXIMATION: 0.35 of design draught is a synthetic minimum forward immersion planning bound, not a verified slamming or propeller-immersion calculation.'},
                    max_trim_m={'assumption':'APPROXIMATION: Absolute aft-minus-forward draught difference capped at 0.01Lpp as a synthetic loading-manual planning limit; no claim of a surveyed typical value.'},
                    max_list_rad={'assumption':'APPROXIMATION: Static loading-condition list capped at 2 degrees converted to radians; a synthetic planning limit, not a cap on dynamic roll or GZ integration angles.'},
                    min_gm_m={'source':is_source},
                    bilge_keel={'assumption':'APPROXIMATION: No bilge or bar keels fitted on any synthetic design. Rounded bilges alone do not justify roll-damping credit. IS Code A2.3.4 gives k=1 for this configuration; no inferred keel area.'},
                    windage_area_m2={'assumption':'APPROXIMATION: Exact union area of the synthetic component silhouettes in windage.projected_rectangles at design draught; includes hull freeboard, deckhouse, closed hatch covers/coamings.'},
                    windage_lever_m={'assumption':'APPROXIMATION: Projected union centroid_z_m minus T/2, using the IS Code A2.3.2 approximate underwater centre at half mean draught; see windage metadata.'}))
    return dict(assumption=SYNTHETIC,confidence='L',id=f'feeder_{n}',name='Synthetic feeder ~800 TEU' if n==800 else f'Synthetic feeder {n}',nominal_teu=n,teu_tolerance_fraction=.02,
        hull=h,hull_form=family,layout=layout,operating_limits=limits,windage=windage,dg_document=dg_document,
        envelope=dict(assumption='APPROXIMATION: Closed weather deck at D; coamings are CLOSED watertight boxes through D+0.6 with hatch covers secured. Union these solids with hull for KN integration. No deckhouse/container buoyancy credited. Open hatches invalidate this envelope. Declared unprotected ventilators and engine-room air inlets in openings limit intact stability at immersion; the geometric KN envelope alone does not imply closure. Tank air pipes assumed to have effective automatic closures and do not directly communicate with a floodable cargo/machinery space, so are not unprotected progressive-flooding points.',deck_z_m=D,count_deckhouse=False,hatch_coamings=coamings,openings=openings,buoyant_x_min_m=0.,buoyant_x_max_m=L),
        bridge=dict(assumption='APPROXIMATION: Synthetic aft bridge conning eye at 0.10Lpp and D+18m. Nonbuoyant overhangs split LOA-Lpp equally aft/forward; bridge visibility must be computed with actual cargo heights.',x_m=.10*L,eye_z_m=D+18,bow_x_m=L+(loa-L)/2),
        cells=cells,stacks=stacks,hatch_covers=covers,tanks=tanks,heat_sources=heat)

if __name__=='__main__':
    from kcs_fit import finalize
    OUT.mkdir(parents=True,exist_ok=True)
    specs=[(500,116.4,107.8,19.2,9.2,7.05,6,6,3,5,92,'arctic-trader.pdf'),(800,134.44,124.41,22.5,11.3,8.7,7,8,3,5,234,'dina-trader.pdf'),(1700,171.99,160.,27.6,14.,9.52,9,10,4,6,192,'bangkokmax-1700.html')]
    for spec in specs:
        ship=finalize(build(*spec));write_toml(ship,OUT/(ship['id']+'.toml'))
        print(ship['id'],len(ship['cells']),'cells',len(ship['stacks']),'stacks',len(ship['tanks']),'tanks')

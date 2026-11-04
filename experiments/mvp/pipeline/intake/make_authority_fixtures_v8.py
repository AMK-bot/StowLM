"""Independent source-authority fixtures; freezes blind bytes before public tuning.

No intake reader, parser or scorer is imported. Run this file directly. All
companies, cargo and paperwork are synthetic. Never opens a sealed map.
"""
from __future__ import annotations

import csv
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import random
import sys
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from stowage.model.cargo import DangerousGoods
from stowage.model.instance import Instance
from stowage.rules.catalogue import load_catalogue, rule_freeze_v1_enabled
from stowage.rules.imdg.dgl import dangerous_goods_list

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).with_name('fixtures') / 'authority_v8'
VESSEL, VOYAGE = 'SYNTHETIC LANTA', '2612S'
LABEL = 'SYNTHETIC DOCUMENT / invented cargo and companies / no customer data'
POLICY = {'document_authority':True,'this_port':'THLCH','vessel':VESSEL,'voyage':VOYAGE,
          'max_gross_mass_kg_by_length':{'20':30480,'40':32500,'45':32500},
          'reefer_setpoint_c':{'min':-35,'max':30}}
REPRESENTATIVE_TARE = {'22G1':2200.,'42G1':3800.,'45G1':3900.,'22R1':3000.,'45R1':4500.,'L5G1':4800.}


def wait_for_slot():
    while True:
        try:
            state = json.loads((ROOT / 'experiments/fleet/core-state.json').read_text())
            if int(state['mask']).bit_count() >= 8 and state['load1'] <= 45 and 0 <= time.time()-state['t'] < 120:
                return
        except (OSError,ValueError,KeyError):
            pass
        time.sleep(5)


def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def dg(un=1263,*,mp=False,lq=False,additional=()):
    entry = next(e for e in dangerous_goods_list() if e.un_number == un and e.packing_group == 'III')
    return DangerousGoods(un_number=f'{un:04d}',imdg_class=entry.hazard_class,packing_group='III',
                         stowage_category=entry.stowage_category,subsidiary_risks=list(entry.subsidiary_risks),
                         stowage_codes=[code for code in entry.stowage_codes if not code.startswith('H')],
                         handling_codes=[code for code in entry.stowage_codes if code.startswith('H')],
                         segregation_codes=list(entry.segregation_codes),marine_pollutant=mp,
                         limited_quantity=lq,additional=list(additional)).model_dump(mode='json')


def csv_form(title,scope,headers,rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream,lineterminator='\n')
    width = len(headers)
    writer.writerow([LABEL]+['']*(width-1))
    writer.writerow([title]+['']*(width-1))
    writer.writerow([scope]+['']*(width-1))
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue()


def canonical(row):
    row['weight_class'] = load_catalogue().weight_classes.classify(row['gross_mass_kg'])
    masskey = f"mass_kg={float(row['gross_mass_kg']).hex()}" if rule_freeze_v1_enabled() else row['weight_class']
    row['category'] = None if row['dg'] or row['reefer'] or row['special_stowage'] or row['oog'] else f"{row['pod']}:{row['iso_type']}:{masskey}:{'empty' if row['empty'] else 'loaded'}"
    return row


def base_rows(source,number,label):
    base = json.loads(source.read_text())
    if base['port']['locode'] != 'THLCH' or base['rotation']['calls'][base['call_index']]['locode'] != 'THLCH':
        raise ValueError('Only unused unsealed development maps with THLCH current port may be used')
    base['id'] = 'authority-v8-'+label
    base['vessel']['name'] = VESSEL
    base['rule_overrides']['intake.policy'] = deepcopy(POLICY)
    by_id = {r['id']:r for r in base['containers']}
    yard = {cid:stack for stack in base['yard']['stacks'] for cid in stack['boxes']}
    pool = [rid for rid in base['load_list'] if rid in yard]
    ids = []
    for length in [20,40,40,20,40,40,20,40,20,40,20,40]:
        rid = next((rid for rid in pool if rid not in ids and by_id[rid]['length_ft']==length),None)
        if rid is None:
            raise ValueError('Source needs the declared twelve-box mix with yard-backed identities')
        ids.append(rid)
    base['load_list'] = ids
    pods = [call['locode'] for call in base['rotation']['calls'][base['call_index']+1:] if call['locode']!='THLCH']
    rows = []
    for i,rid in enumerate(ids):
        row = deepcopy(by_id[rid])
        iso = ('22' if row['length_ft']==20 else '45') + ('R1' if i in (2,3) else 'G1')
        row.update(iso_type=iso,height_m=2.591 if iso[1]=='2' else 2.896,tare_kg=REPRESENTATIVE_TARE[iso],
                   gross_mass_kg=float(11000+740*i+20*number),max_gross_mass_kg=None,
                   pol='THLCH',pod=pods[i%len(pods)],empty=False,reefer=i in (2,3),
                   reefer_setpoint_c=(-18. if i==2 else 4. if i==3 else None),dg=None,
                   direct_delivery=False,special_stowage=[],oog=None)
        if i==0: row['dg'] = dg(additional=[dg(3082,mp=True)])
        if i==1: row['dg'] = dg(3082,mp=True,lq=True)
        if i==8: row.update(empty=True,gross_mass_kg=row['tare_kg'])
        rows.append(canonical(row))
    return base,rows


def tally(path,rows,values,number):
    rng = random.Random(81200+number)
    image = Image.new('RGB',(1720,1700),'#e9e1cc')
    draw = ImageDraw.Draw(image)
    hand = ImageFont.truetype('/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf',40)
    small = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',24)
    heading = ImageFont.truetype('/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf',50)
    draw.text((65,28),'SYNTHETIC PICTURE / rendered with PIL / not a real gate record',font=small,fill='#644b3b')
    draw.text((80,90),'LAEM CHABANG / hand tally',font=heading,fill='#273f5c')
    draw.text((80,165),f'{VESSEL} {VOYAGE}    02 DEC 2026',font=hand,fill='#273f5c')
    draw.text((80,245),'Container no.                POD      Seal / condition',font=hand,fill='#304563')
    cells=[]
    for i,row in enumerate(rows):
        y=338+i*92
        pod=values.get(row['id'],row['pod'])
        draw.line((65,y+68,1630,y+68),fill='#b0a48c',width=2)
        draw.text((80+rng.randint(-4,4),y+rng.randint(-3,3)),row['id'],font=hand,fill='#314b71')
        draw.text((755+rng.randint(-4,4),y),pod,font=hand,fill='#314b71')
        draw.text((1120,y+2),['seal seen','intact','OK'][i%3],font=hand,fill='#3a4c64')
        cells.append({'row_id':row['id'],'field':'pod','value':pod})
    for _ in range(700):
        draw.point((rng.randrange(1720),rng.randrange(1700)),fill='#b6aa91')
    image=image.rotate((-1.4,1.8,-2.1)[number%3],Image.Resampling.BICUBIC,expand=True,fillcolor='#736d62')
    image.filter(ImageFilter.GaussianBlur(.6)).save(path)
    return cells


def build(source,label,number,si_headers,vgm_headers,trap_names,*,public=False):
    out = OUT / label
    out.mkdir(parents=True,exist_ok=False)
    base,truth = base_rows(source,number,label)
    booking_ref=f'SYN-LCH-26{number:04d}'
    observed=deepcopy(truth)
    booking={r['id']:{'pod':r['pod'],'vessel':VESSEL,'status':'CONFIRMED'} for r in truth}
    vgm={r['id']:r['gross_mass_kg'] for r in truth}
    dg_source={r['id']:deepcopy(r['dg']) for r in truth if r['dg']}
    si_gross={r['id']:r['gross_mass_kg'] for r in truth}
    si_flags={}
    si_temperature={r['id']:('' if r['reefer_setpoint_c'] is None else f"{r['reefer_setpoint_c']:g} C") for r in truth}
    tally_values={}
    amendments=[]
    traps=[]
    answers=[]
    for i,name in enumerate(trap_names):
        index=9+i
        target,row=truth[index],observed[index]
        rid=row['id']
        field,reason='pod','cross_document_conflict'
        if name=='image_pod':
            tally_values[rid]=next(p for p in {r['pod'] for r in truth} if p!=target['pod'])
        elif name=='declared_vs_vgm':
            si_gross[rid]=target['gross_mass_kg']-target['tare_kg']
            field='gross_mass_kg'
        elif name=='wrong_vessel':
            booking[rid]['vessel']='SYNTHETIC MANORA'
            field,reason='booked_vessel','wrong_vessel'
        elif name in ('dg_mp_conflict','dg_lq_conflict'):
            # The DG identity is genuine in BOTH key and authoritative DGD.
            target['dg']=dg(3082,mp=True,lq=name=='dg_lq_conflict')
            row['dg']=deepcopy(target['dg'])
            dg_source[rid]=deepcopy(target['dg'])
            si_flags[rid]={'mp':not target['dg']['marine_pollutant'] if name=='dg_mp_conflict' else target['dg']['marine_pollutant'],
                           'lq':not target['dg']['limited_quantity'] if name=='dg_lq_conflict' else target['dg']['limited_quantity']}
            field='dg.marine_pollutant' if name=='dg_mp_conflict' else 'dg.limited_quantity'
        elif name in ('amendment_pod','cod'):
            alt=next(p for p in {r['pod'] for r in truth} if p!=target['pod'])
            amendments.append((rid,alt,f'{LABEL}\nFrom: Mali <export@example.invalid>\nSubject: RE: {booking_ref} / {rid}\nวันที่ 2 ธันวาคม 2569 เวลา 18.42 น.\nเรียนฝ่ายเรือ\nตู้ {rid} เปลี่ยนปลายทางเป็น {alt} ตามใบแก้ไขเลขที่ AMD-{number:02d} กรุณาใช้ปลายทางใหม่สำหรับเที่ยว {VOYAGE} ค่ะ\nมาลี\n'))
            reason='amendment_review'
        elif name=='fahrenheit':
            iso='45R1' if target['length_ft']==40 else '22R1'
            for entry in (target,row): entry.update(iso_type=iso,height_m=2.896 if iso[1]=='5' else 2.591,reefer=True,reefer_setpoint_c=-18.,tare_kg=REPRESENTATIVE_TARE[iso])
            si_temperature[rid]='-0.4 F'
            field,reason='reefer_setpoint_c','fahrenheit_setpoint'
        elif name=='reefer_iso':
            iso='45R1' if target['length_ft']==40 else '22R1'
            target.update(iso_type=iso,height_m=2.896 if iso[1]=='5' else 2.591,reefer=True,reefer_setpoint_c=4.,tare_kg=REPRESENTATIVE_TARE[iso])
            row.update(reefer=True,reefer_setpoint_c=4.,tare_kg=target['tare_kg'])
            si_temperature[rid]='4 C'
            field,reason='reefer','reefer_iso_mismatch'
        elif name=='missing_vgm':
            vgm.pop(rid)
            field,reason='gross_mass_kg','missing_vgm'
        elif name=='unknown_un':
            target['dg']=dg();row['dg']=deepcopy(target['dg']);dg_source[rid]=deepcopy(target['dg']);dg_source[rid]['un_number']='9999'
            field,reason='dg.un_number','unknown_un'
        elif name=='wrong_pol':
            row['pol']='THBKK'
            field,reason='pol','wrong_pol'
        elif name=='duplicate':
            field,reason='id','duplicate_id'
        elif name=='temperature_range':
            iso='45R1' if target['length_ft']==40 else '22R1'
            for entry in (target,row): entry.update(iso_type=iso,height_m=2.896 if iso[1]=='5' else 2.591,reefer=True,reefer_setpoint_c=-18.,tare_kg=REPRESENTATIVE_TARE[iso])
            si_temperature[rid]='-180 C'
            field,reason='reefer_setpoint_c','reefer_setpoint_range'
        elif name=='empty_gross':
            row['empty']=True
            field,reason='empty','empty_mass_mismatch'
        else:
            raise ValueError(name)
        plausible=name not in ('unknown_un','missing_vgm','temperature_range')
        traps.append({'kind':name,'row_id':rid,'field':field,'reason':reason,'expected':'question_or_rejection','plausible':plausible})
        value=target
        try:
            for part in field.split('.'): value=value[part]
        except (KeyError,TypeError):
            value=VESSEL if field=='booked_vessel' else None
        if name=='duplicate': value='keep_one'
        answers.append({'row_id':rid,'field':field,'value':value,'reason':reason})
    # Recompute genuine business targets after trap-specific cargo amendments.
    for row in truth: canonical(row)
    ids=[r['id']for r in truth]
    by_id={r['id']:r for r in base['containers']}
    for row in truth: by_id[row['id']].update(row)
    base=Instance.model_validate(base).model_dump(mode='json')
    assert len({t['row_id']for t in traps}) <= len(truth)//3

    booking_lines=[LABEL,'BOOKING CONFIRMATION',f'Booking reference: {booking_ref}',f'Vessel: {VESSEL}',f'Voyage: {VOYAGE}',
                   'Port of loading: THLCH / LAEM CHABANG','ETD: 03 DEC 2026 02:00 ICT',
                   f'Complete container list for booking {booking_ref}; {len(truth)} units. Scope is this booking only.','']
    for row in truth:
        item=booking[row['id']]
        booking_lines.append(f"{row['id']}; POD {item['pod']}; Vessel {item['vessel']}; Status {item['status']}")
    booking_text='\n'.join(booking_lines)+'\n'
    si_rows=[]
    for row in observed:
        rid=row['id']
        flags=si_flags.get(rid,{})
        authoritative=next(target for target in truth if target['id']==rid)
        commodity='EMPTY CONTAINER' if authoritative['empty'] else ('FROZEN FRUIT' if authoritative['reefer_setpoint_c'] is not None and authoritative['reefer_setpoint_c']<0 else 'CHILLED FRUIT') if authoritative['reefer'] else ('PACKAGED PAINT' if authoritative['dg'] and authoritative['dg']['un_number']=='1263' else 'ENVIRONMENTALLY HAZARDOUS LIQUID IN PACKAGES') if authoritative['dg'] else 'CARTONS OF MACHINE PARTS'
        si_rows.append([rid,row['iso_type'],row['pol'],'EMPTY' if row['empty'] else 'LADEN',booking_ref,commodity,
                        si_temperature[rid],f'{si_gross[rid]:.0f} kg','' if rid==truth[5]['id'] else f"{row['tare_kg']:.0f} kg",
                        '' if 'mp' not in flags else 'YES' if flags['mp'] else 'NO',
                        '' if 'lq' not in flags else 'YES' if flags['lq'] else 'NO'])
        # Construction checks precede every freeze: physical columns must bind
        # to the same business meaning as their independent form headers.
        cells=si_rows[-1]
        assert len(cells)==len(si_headers)==11
        assert cells[4]==booking_ref and cells[5]==commodity
        assert cells[6]==si_temperature[rid] and cells[7].endswith(' kg')
        assert cells[8]=='' if rid==truth[5]['id'] else cells[8].endswith(' kg')
    if 'duplicate' in trap_names:
        si_rows.append(deepcopy(si_rows[9+trap_names.index('duplicate')]))
    si=csv_form('SHIPPING INSTRUCTIONS',f'Booking {booking_ref}; issued 02/12/2569',si_headers,si_rows)
    vgm_rows=[]
    for i,row in enumerate(truth):
        if row['id'] not in vgm: continue
        mass=vgm[row['id']]
        display=f'{mass/1000:.3f} MT' if i%4==1 else f'{mass:.0f} kg'
        vgm_rows.append([row['id'],display,'1','N. Chai','02/12/2569'])
    vgm_text=csv_form('VERIFIED GROSS MASS DECLARATION',f'Booking {booking_ref}; gross mass includes tare; shipper authorised signature below',vgm_headers,vgm_rows)
    dgd_lines=[LABEL,'MULTIMODAL DANGEROUS GOODS DECLARATION',f'Booking reference: {booking_ref}',f'Vessel {VESSEL} / Voyage {VOYAGE}',
               f'COMPLETE DANGEROUS GOODS LIST FOR BOOKING {booking_ref}.',
               'Only the containers listed below carry dangerous goods. All other containers on this booking are non-DG.',
               'Shipper: Synthetic Eastern Pack Ltd; signed N. Chai; 02 DEC 2026','']
    for rid,entry in dg_source.items():
        for j,item in enumerate([entry]+entry['additional']):
            dgd_lines.append(f"{rid}; Item {j+1}; UN {item['un_number']}; Class {item['imdg_class']}; PG {item['packing_group']}; Marine pollutant {'YES' if item['marine_pollutant'] else 'NO'}; Limited quantity {'YES' if item['limited_quantity'] else 'NO'}; 24 fibreboard boxes")
    dgd='\n'.join(dgd_lines)+'\n'
    rf_rows=[[r['id'],'YES',f"{r['reefer_setpoint_c']:g} C"]for r in truth if r['reefer']]
    rf_headers=['Powered equipment no.','Reefer connection required','Thermostat requested'] if number%2 else ['Operating refrigerated unit','Onboard plug service','Temperature to maintain']
    reefer=csv_form('OPERATING REEFER LIST',f'COMPLETE OPERATING REEFER LIST FOR BOOKING {booking_ref}. Only listed containers require power; all other booked containers are non-operating.',rf_headers,rf_rows)
    oog=f'{LABEL}\nOUT OF GAUGE DECLARATION\nBooking reference: {booking_ref}\nCOMPLETE OOG LIST FOR BOOKING {booking_ref}.\nThere are no OOG containers on this booking. All booked containers are in gauge.\nSigned N. Chai; 02 DEC 2026\n'
    eir=[LABEL,'EQUIPMENT INTERCHANGE RECEIPTS / LAEM CHABANG',f'Booking reference: {booking_ref}','Gate-in 02/12/2569 19:40 ICT','']
    auxiliary={}
    for i,row in enumerate(truth):
        seal=f'SYN{number:03d}{i:04d}'
        eir.append(f"{row['id']}; Seal {seal}; Gate IN; interchange accepted; driver D-{i+1:03d}")
        auxiliary[row['id']]={'seal':seal,'commodity':si_rows[i][5]}
    email=f'{LABEL}\nFrom: Mali <export@example.invalid>\nTo: Load desk <plan@example.invalid>\nSubject: FW: {booking_ref} / {VESSEL} {VOYAGE}\nวันที่ 2 ธันวาคม 2569 เวลา 20.10 น.\n\nพี่คะ ส่งใบจอง ใบชั่ง และบัญชีตู้เย็นชุดล่าสุดให้แล้วค่ะ บัญชีสินค้าอันตรายครอบคลุมใบจองนี้ทั้งชุด ส่วนใบรับตู้เข้าท่าอยู่ท้ายอีเมล คนขับฝากรูปใบนับตู้กะค่ำมาด้วย รบกวนจัดชุดเอกสารให้ทันเรือออกพรุ่งนี้นะคะ\n\nมาลี\n\n----- Forwarded message -----\nFrom: Somsak <gate@example.invalid>\nSent: 2/12/2569 19.55\nเรื่อง: ใบรับตู้\nตรวจหมายเลขตู้กับซีลครบแล้วครับ รถคันสุดท้ายออกประตูสามตอนทุ่มครึ่ง\n'
    records=[('booking','booking_confirmation.txt',booking_text,'text/plain',True),
             ('shipping_instructions','shipping_instructions.csv',si,'text/csv',False),
             ('vgm','vgm_declaration.csv',vgm_text,'text/csv',False),
             ('dg_declaration','dg_declaration.txt',dgd,'text/plain',True),
             ('reefer_list','reefer_list.csv',reefer,'text/csv',True),
             ('oog_list','oog_list.txt',oog,'text/plain',True),
             ('gate_slip','eir_gate_slips.txt','\n'.join(eir)+'\n','text/plain',False),
             ('email','forwarding_email.txt',email,'text/plain',False)]
    for i,(_,_,text) in enumerate(amendments): records.append(('amendment',f'amendment_{i+1}.txt',text,'text/plain',False))
    documents=[]
    for role,name,text,mime,complete in records:
        (out/name).write_text(text)
        item={'id':f'{label}-{name.rsplit(".",1)[0]}','path':str((out/name).relative_to(ROOT)),
              'mime_type':mime,'text':text,'synthetic':True,'role':role,'booking_ref':booking_ref,'intake_policy':8}
        if complete: item['complete_booking_list']=True
        documents.append(item)
    tally_cells=tally(out/'tally_sheet.png',truth,tally_values,number)
    documents.append({'id':label+'-tally_sheet','path':str((out/'tally_sheet.png').relative_to(ROOT)),
                      'mime_type':'image/png','synthetic':True,'role':'tally','booking_ref':booking_ref,'intake_policy':8})
    expected={'synthetic':True,'label':'Independently authored synthetic ground truth; not model output',
              'case_label':label,'source_map':str(source.relative_to(ROOT)),'source_map_sha256':sha256_file(source),
              'base_instance_sha256':sha256_json(base),'rows':truth,'traps':traps,'answers':answers,
              'clean_control_ids':[r['id']for r in truth if r['id']not in {t['row_id']for t in traps}],
              'tally_cells':tally_cells,'booking_ref':booking_ref,'auxiliary_metadata':auxiliary,
              'derived_fields':{truth[5]['id']:['tare_kg']},
              'derivation_labels':{truth[5]['id']:{'tare_kg':'Synthetic representative ISO45G1 tare 3900 kg, only because no tare is stated; not a plate measurement.'}},
              'caption':f'{len(truth)} synthetic booked boxes on a selected map section; not a whole ship or 195-box test.',
              'business_fields':['id','iso_type','pol','pod','gross_mass_kg','tare_kg','empty','reefer','reefer_setpoint_c','dg','oog'],
              'base_amendments':['Selected twelve original yard-backed load IDs; ship and yard retained',
                                  'Synthetic cargo business facts replaced by the declared booking/shipper forms',
                                  'Rounded whole-ten-kg VGMs; explicit form authority and complete booking-scoped lists',
                                  'No plate rating inferred; declared intake ceiling retained; one absent tare is a labelled representative approximation']}
    assert all(bool(r['dg'])==(r['id']in dg_source)for r in truth)
    assert len({t['row_id']for t in traps})<=len(truth)//3
    dump(out/'base_instance.json',base)
    dump(out/'documents.json',documents)
    dump(out/'expected.json',expected)
    return {'split':label,'rows':len(truth),'traps':len(traps),'trap_rows':len({t['row_id']for t in traps}),
            'clean_controls':len(expected['clean_control_ids']),'source_map':str(source.relative_to(ROOT)),
            'source_map_sha256':sha256_file(source),'files':{p.name:sha256_file(p)for p in sorted(out.iterdir())if p.is_file()}}


def main():
    wait_for_slot()
    OUT.mkdir(parents=True,exist_ok=True)
    manifest=OUT/'blind_freeze.json'
    if manifest.exists(): raise SystemExit('Blind v8 fixtures are frozen; regeneration forbidden')
    allowed=[ROOT/line for line in (ROOT/'experiments/mvp/pipeline/maps/dev.txt').read_text().splitlines()if line and not line.startswith('#')]
    # Intake has never used these pipeline development maps; none is sealed.
    blind_sources=[allowed[i]for i in (24,26,28,30,31)]
    variants=[
      ['Booked freight equipment','ISO equipment type','Place of shipment code','Equipment status empty/full','Reservation identifier','Cargo goods description','Temperature requested','Shipper gross copy','Container self-weight','Pollutant mark from SI','Limited quantity as shipper'],
      ['Cargo unit number','Equipment ISO designation','Departure terminal UNLOCODE','Shipment laden/empty','Forwarding booking ID','Goods declaration','Requested thermostat','Declared mass incl equipment','Unit tare shown','Marine pollutant stated by shipper','LQ stated by shipper'],
      ['Intermodal equipment reference','Container type specification','Origin loadport code','State of loading','Ocean reservation','Cargo commodity','Temperature for carriage','Shipper gross weight copy','Equipment tare weight','MP indication on instructions','LQ indication on instructions'],
      ['Transport equipment no','ISO dimension/type code','Loadport UN code','Full/empty indicator','Shipment booking no','Description for manifest','Refrigeration instruction','Equipment gross declared','Empty equipment mass','Sea pollutant declaration','Limited quantity declaration'],
      ['Shipping unit serial','Size-type equipment code','Loading point UNLOCODE','Cargo load state','Booking confirmation ref','Cargo description supplied','Set temperature instruction','Declared whole-unit mass','Tare according to shipper','Pollutant indication copy','LQ instruction copy'],
    ]
    vgms=[['Gross mass certificate unit','Verified gross mass total','Method used','Authorised shipper signatory','Date of weighing declaration'],
          ['Equipment weighed for shipment','Certified weight incl tare','Method declaration','VGM signed by','Certificate local date'],
          ['Container covered by VGM','Verified equipment gross','Weighing procedure','Authorised weight signatory','VGM declaration date'],
          ['Freight unit on certificate','Certified CTU gross weight','SOLAS method used','Shipper weight signature','Date issued locally'],
          ['Unit under gross verification','VGM certified gross','Weigh method applied','Signed for the shipper','Declaration effective date']]
    trap_sets=[['image_pod','declared_vs_vgm','wrong_vessel'],
               ['dg_mp_conflict','dg_lq_conflict','amendment_pod'],
               ['fahrenheit','reefer_iso','missing_vgm'],
               ['unknown_un','wrong_pol','duplicate'],
               ['cod','temperature_range','empty_gross']]
    cases=[build(source,f'blind_{i+1:02d}',41+i,variants[i],vgms[i],trap_sets[i])for i,source in enumerate(blind_sources)]
    freeze={'schema_version':'stowlm.intake-authority-freeze/8','cohort':'authority_v8_blind','evaluation_split':'blind',
            'synthetic':True,'frozen_utc':datetime.now(timezone.utc).isoformat(),'frozen_before_tuning':True,
            'frozen_before_scoring':True,'independent_fixture_author':True,'total_boxes':sum(c['rows']for c in cases),
            'cases':cases,'source_list':'experiments/mvp/pipeline/maps/dev.txt',
            'generator_sha256':sha256_file(Path(__file__)),
            'label':'Five independently authored twelve-box synthetic source-authority cases; development maps, never sealed maps; problem prevalence 25% of boxes.',
            'discarded_unscored_construction_freeze':{'path':'../authority_v8_unscored_construction_error/blind_freeze.json','sha256':'be5b4bdfeed3fb2be4c727c5bb76397ac29245ba2b232c98106c5d2318ddd6c4','reason':'Header/cell order construction error found on PUBLIC development before any model read or score; original bytes retained.'}}
    dump(manifest,freeze)
    (OUT/'blind_freeze.sha256').write_text(sha256_file(manifest)+'  blind_freeze.json\n')
    public_si=['Container','ISO','POL','Empty','Booking reference','Commodity','Setpoint','Gross mass','Tare kg','Marine pollutant','Limited quantity']
    public_vgm=['Container Number','VGM','Method','Signed by','Date']
    public=[build(allowed[8],'development_01',101,public_si,public_vgm,['image_pod','declared_vs_vgm','amendment_pod'],public=True),
            build(allowed[10],'development_02',102,['CNTR NO','TYPE','POL','Load condition','Booking','Goods','Setting','Declared gross','Tare','MP','LQ'],public_vgm,['dg_mp_conflict','fahrenheit','wrong_pol'],public=True),
            build(allowed[12],'demo',103,public_si,public_vgm,['image_pod','declared_vs_vgm','amendment_pod'],public=True)]
    dump(OUT/'development_manifest.json',{'synthetic':True,'cases':public,'blind_freeze_sha256':sha256_file(manifest)})
    print(json.dumps({'blind_manifest':str(manifest.relative_to(ROOT)),'sha256':sha256_file(manifest),
                      'cases':len(cases),'boxes':freeze['total_boxes'],'public_cases':[case['split']for case in public]}),flush=True)


if __name__=='__main__': main()

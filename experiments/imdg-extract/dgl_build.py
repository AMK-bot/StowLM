"""Rebuild the curated IMDG sample and reproducible US HMT cross-check.

Facts are transcribed from the cited 42-24 pages; this is not a PDF parser.
Run from project root: uv run python experiments/imdg-extract/dgl_build.py.
"""
import json,re,xml.etree.ElementTree as ET
from pathlib import Path
OUT=Path('src/stowage/rules/imdg/data/dgl.json')
entries=[]
def add(un,name,cl,pg,cat,page,seg='',stow='',sub='',sp='',compat=None,note=None):
    for packing,category in zip(pg.split('/') if pg else [None],cat.split('/')):
        entries.append(dict(id=f'UN{un:04d}-{packing or "none"}',un_number=un,proper_shipping_name=name,hazard_class=cl,subsidiary_risks=sub.split(),subsidiary_hazard_note=note,compatibility_group=compat,packing_group=packing,stowage_category=category,stowage_codes=stow.split(),segregation_codes=seg.split(),special_provisions=sp.split(),source=dict(document='MSC.556(108), IMDG 42-24',pdf_page=page,table='3.2 Dangerous Goods List',row=f'UN {un:04d}; PG {packing or "not assigned"}',columns=['1','2','3','4','5','6','16a','16b'])))
add(4,'AMMONIUM PICRATE dry or wetted with less than 10% water, by mass','1.1','','04',581,'SGG2 SG27 SG31','SW1',compat='D')
add(7,'CARTRIDGES FOR WEAPONS with bursting charge','1.2','','03',581,stow='SW1',compat='F')
add(10,'AMMUNITION, INCENDIARY with or without burster, expelling charge or propelling charge','1.3','','03',581,stow='SW1',compat='G')
add(12,'CARTRIDGES FOR WEAPONS, INERT PROJECTILE or CARTRIDGES, SMALL ARMS','1.4','','01',581,stow='SW1',sp='364',compat='S')
add(331,'EXPLOSIVE, BLASTING, TYPE B (AGENT, BLASTING, TYPE B)','1.5','','03',590,'SG34','SW1',compat='D')
add(336,'FIREWORKS','1.4','','02',590,stow='SW1',compat='G')
add(486,'ARTICLES, EXPLOSIVE, EXTREMELY INSENSITIVE (ARTICLES, EEI)','1.6','','03',596,stow='SW1',compat='N')
add(1005,'AMMONIA, ANHYDROUS','2.3','','D',598,'SGG18 SG35 SG46','SW2','8','23 379')
add(1006,'ARGON, COMPRESSED','2.2','','A',598,sp='378 392 406')
add(1017,'CHLORINE','2.3','','D',598,'SG6 SG19','SW2','5.1 8')
add(1057,'LIGHTERS or LIGHTER REFILLS containing flammable gas','2.1','','B',600,stow='SW2',sp='201')
add(1090,'ACETONE','3','II','E',602)
add(1202,'GAS OIL or DIESEL FUEL or HEATING OIL, LIGHT','3','III','A',607)
add(1263,'PAINT (including paint, lacquer, enamel, stain, shellac, varnish, polish, liquid filler and liquid lacquer base) or PAINT RELATED MATERIAL (including paint thinning or reducing compound)','3','I/II/III','E/B/A',610,sp='163 367')
# Variant-specific column 6 is retained rather than applied to the entire UN number.
entries[-1]['special_provisions']=['163','223','367','955']
add(1325,'FLAMMABLE SOLID, ORGANIC, N.O.S.','4.1','II/III','B/B',614,'SG72',sp='274')
entries[-1]['special_provisions']=['223','274']
add(1333,'CERIUM, slabs, ingots or rods','4.1','II','A',614,'SG17 SG25 SG26','H1')
add(1361,'CARBON, animal or vegetable origin','4.2','II/III','A/A',616,stow='SW1 SW27 H2',sp='978')
add(1381,'PHOSPHORUS, WHITE or YELLOW, DRY or UNDER WATER or IN SOLUTION','4.2','I','E',617,sub='6.1')
add(1402,'CALCIUM CARBIDE','4.3','I/II','B/B',619,'SG26 SG35','H1')
add(1428,'SODIUM','4.3','I','D',620,'SG26 SG35','H1')
add(1689,'SODIUM CYANIDE, SOLID','6.1','I','B',635,'SGG6 SG35')
add(1748,'CALCIUM HYPOCHLORITE, DRY or CALCIUM HYPOCHLORITE MIXTURE, DRY with more than 39% available chlorine (8.8% available oxygen)','5.1','II/III','D/D',639,'SGG8 SG35 SG38 SG49 SG53 SG60','SW1 SW11',sp='314')
entries[-1]['special_provisions']=['316']
add(1789,'HYDROCHLORIC ACID','8','II/III','C/C',642,'SGG1 SG36 SG49')
entries[-1]['special_provisions']=['223']
add(1845,'CARBON DIOXIDE, SOLID (DRY ICE)','9','','C',647,stow='SW2')
add(1978,'PROPANE','2.1','','E',652,stow='SW2',sp='392')
add(1993,'FLAMMABLE LIQUID, N.O.S.','3','I/II/III','E/B/A',653,sp='274')
entries[-1]['special_provisions']=['223','274','955']
add(2211,'POLYMERIC BEADS, EXPANDABLE, evolving flammable vapour','9','III','E',660,'SG5 SG14','SW1 SW6',sp='382 965')
add(2794,'BATTERIES, WET, FILLED WITH ACID, electric storage','8','','A',693,'SGG1 SG36 SG49','SW16',sp='295')
add(2810,'TOXIC LIQUID, ORGANIC, N.O.S.','6.1','I/II/III','B/B/A',694,stow='SW2',sp='274')
entries[-3]['special_provisions']=['274','315']
entries[-1]['special_provisions']=['223','274']
add(2814,'INFECTIOUS SUBSTANCE, AFFECTING HUMANS','6.2','','E',694,'SG50','SW2 H1 H5',sp='318 341')
add(2880,'CALCIUM HYPOCHLORITE, HYDRATED or CALCIUM HYPOCHLORITE, HYDRATED MIXTURE with not less than 5.5% but not more than 16% water','5.1','II/III','D/D',698,'SGG8 SG35 SG38 SG49 SG53 SG60','SW1 SW11',sp='314 322')
entries[-1]['special_provisions']=['223','314']
add(2910,'RADIOACTIVE MATERIAL, EXCEPTED PACKAGE - LIMITED QUANTITY OF MATERIAL','7','','A',699,sp='290 368',note='See SP290; subsidiary hazard depends on contents, not an unconditional empty list.')
add(2915,'RADIOACTIVE MATERIAL, TYPE A PACKAGE, non-special form, non fissile or fissile-excepted','7','','A',699,stow='SW20 SW21',sp='172 317 325',note='See SP172; subsidiary hazard depends on contents, not an unconditional empty list.')
add(3077,'ENVIRONMENTALLY HAZARDOUS SUBSTANCE, SOLID, N.O.S.','9','III','A',709,stow='SW23',sp='274 335 375 966 967 969')
add(3082,'ENVIRONMENTALLY HAZARDOUS SUBSTANCE, LIQUID, N.O.S.','9','III','A',709,sp='274 335 375 969')
add(3105,'ORGANIC PEROXIDE TYPE D, LIQUID','5.2','','D',712,'SG35 SG36 SG72','SW1',sp='122 274')
add(3109,'ORGANIC PEROXIDE TYPE F, LIQUID','5.2','','D',712,'SG35 SG36 SG72','SW1',sp='122 274')
add(3166,'VEHICLE, FLAMMABLE GAS POWERED or VEHICLE, FLAMMABLE LIQUID POWERED or VEHICLE, FUEL CELL, FLAMMABLE GAS POWERED or VEHICLE, FUEL CELL, FLAMMABLE LIQUID POWERED','9','','A',717,sp='356 388 961 962')
add(3373,'BIOLOGICAL SUBSTANCE, CATEGORY B','6.2','','C',736,stow='SW2 SW18',sp='319 341')
add(3480,'LITHIUM ION BATTERIES (including lithium ion polymer batteries)','9','','A',746,stow='SW19',sp='188 230 310 348 376 377 384 387')
add(3481,'LITHIUM ION BATTERIES CONTAINED IN EQUIPMENT or LITHIUM ION BATTERIES PACKED WITH EQUIPMENT (including lithium ion polymer batteries)','9','','A',746,stow='SW19',sp='188 230 310 348 360 376 377 384 387 390')
provisions=[]
for code,page,meaning in [
 ('SW1',484,'Protected from sources of heat.'),('SW2',484,'Clear of living quarters.'),
 ('SW6',484,'When under deck, mechanical ventilation must meet SOLAS II-2/19 (II-2/54) requirements for flammable liquids with flashpoint below 23 degrees C closed cup.'),
 ('SW11',485,'Shade cargo transport units from direct sunlight; arrange packages to allow adequate air circulation throughout the cargo.'),
 ('SW16',485,'Unit loads in open cargo transport units use stowage category B.'),
 ('SW18',485,'Category A applies when transported in accordance with P650.'),
 ('SW19',485,'Batteries transported under special provisions 376 or 377 use category C unless on a short international voyage.'),
 ('SW20',485,'Uranyl nitrate hexahydrate solution uses stowage category D.'),
 ('SW21',485,'Pyrophoric uranium metal and pyrophoric thorium metal use stowage category D.'),
 ('SW23',485,'For BK3 bulk containers consult 7.6.2.12 and 7.7.3.9.'),
 ('SW27',485,'For special stowage provisions consult 7.6.2.7.2.1.'),
 ('H1',485,'Keep as dry as reasonably practicable.'),
 ('H2',485,'Keep as cool as reasonably practicable.'),
 ('H5',485,'Minimize handling of packaging or large packaging; inform the appropriate public health or veterinary authority if people or animals may have been exposed.')]:
 provisions.append(dict(code=code,meaning=meaning,source=dict(document='MSC.556(108), IMDG 42-24',pdf_page=page,table='7.1.5' if code.startswith('SW') else '7.1.6',row=code,column='Description')))
root=ET.parse('data/raw/ecfr-172-101/172.101.xml').getroot()
cfr=[]; last=['']*4
for i,tr in enumerate(root.iter('TR'),1):
 cells=[' '.join(''.join(c.itertext()).split()) for c in tr.findall('TD')]
 if len(cells)!=14: continue
 if not cells[1] and not cells[3]: cells[:4]=last
 elif re.fullmatch(r'UN\d{4}',cells[3]): last=cells[:4]
 else: last=['']*4
 if re.fullmatch(r'UN\d{4}',cells[3]):
  cfr.append(dict(un_number=int(cells[3][2:]),proper_shipping_name=cells[1],hazard_class=cells[2],packing_group=cells[4] or None,labels=cells[5],stowage_category=cells[12],vessel_provisions=cells[13],source=dict(document='49 CFR 172.101, 2026-10-23 snapshot',xml_row=i,columns=['2','3','4','5','6','10A','10B'])))
for e in entries:
 matches=[r for r in cfr if r['un_number']==e['un_number']]
 samepg=[r for r in matches if r['packing_group']==e['packing_group']]
 def equivalent_class(r): return r['hazard_class']==e['hazard_class']+(e['compatibility_group'] or '')
 exact=[r for r in samepg if equivalent_class(r)]
 differences=[]
 if not matches: differences.append('UN number absent from US HMT')
 elif not samepg: differences.append('packing group differs or US uses a different variant')
 elif not exact: differences.append('primary class/division differs')
 if exact and not any(r['stowage_category']==e['stowage_category'] for r in exact): differences.append('vessel stowage category differs')
 # US labels are a useful subsidiary check but not a complete replacement for IMDG column 4.
 if exact and e['subsidiary_hazard_note'] is None:
  expected=set(e['subsidiary_risks'])
  def subsidiary(r):
   labels=set(re.findall(r'\d+(?:\.\d+)?',r['labels']))
   primary=e['hazard_class']
   return labels-{primary,'1' if primary.startswith('1.') else primary}
  if not any(subsidiary(r)==expected for r in exact): differences.append('subsidiary hazards differ from US label column (labels are not IMDG column 4)')
 e['cfr_cross_check']=dict(status='differences' if differences else 'matched',differences=differences,authority='IMDG 42-24 wins for this model',rows=matches)
OUT.write_text(json.dumps(dict(schema_version=1,edition='IMDG 42-24',table_name='imdg_42_24.dangerous_goods_list_3_2',provisions_table_name='imdg_42_24.stowage_and_handling_provisions',sample_rationale='53 entries / 41 UN numbers cover every class/division and PG I/II/III plus unassigned PG, named common container cargo, explosives compatibility groups, multiple subsidiary risks and conditional SW/H/SG provisions. Includes all PG variants for the generator follow-up UN set. Curated sample, not the complete DGL.',entries=entries,provisions=provisions),indent=2)+'\n')
print(len(entries),'entries;',len({e['un_number'] for e in entries}),'UN numbers')
for e in entries:
 if e['cfr_cross_check']['differences']: print(e['id'], '; '.join(e['cfr_cross_check']['differences']))

from pathlib import Path
import hashlib,json
sources={
'iacs':{
'ur-l5rev4.pdf':'https://iacs.s3.af-south-1.amazonaws.com/wp-content/uploads/2022/02/17101147/ur-l5rev4.pdf',
'ur-s1rev7.pdf':'https://iacs.s3.af-south-1.amazonaws.com/wp-content/uploads/2022/05/18113352/ur-s1rev7.pdf',
'ur-s11a.pdf':'https://iacs.s3.af-south-1.amazonaws.com/wp-content/uploads/2022/05/18115515/ur-s11a.pdf',
'rec48rev1.pdf':'https://iacs.s3.af-south-1.amazonaws.com/wp-content/uploads/2022/05/20100040/rec48rev1.pdf',
'UR-Implementation-status-2026.pdf':'https://iacs.s3.af-south-1.amazonaws.com/wp-content/uploads/2026/10/19130651/UR-Implementation-status-2026.pdf',
'URS-2024.pdf':'https://iacs.s3.af-south-1.amazonaws.com/wp-content/uploads/2024/09/17130516/URS.pdf'},
'irs':{
'guidelines-on-container-securing_july-2025.pdf':'https://www.irclass.org/media/7895/guidelines-on-container-securing_july-2025_new.pdf',
'main_rules_july_2026.pdf':'https://www.irclass.org/media/8639/main_rules_july_2026-edition.pdf',
'mr-rcn-1-sept-2026.pdf':'https://www.irclass.org/media/8771/mr-rcn-1-sept-2026.pdf'}}
for name, mapping in sources.items():
 root=Path('data/raw')/name
 records=[]
 for pdf,url in mapping.items():
  for filename in [pdf, str(Path(pdf).with_suffix('.txt'))]:
   path=root/filename
   records.append({'file':filename,'url':url,'retrieved':'2026-10-27','licence':f'{name.upper()} copyright; publicly downloadable; no open-data reuse licence identified','sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'derived_from':pdf if filename.endswith('.txt') else None})
 (root/'MANIFEST').write_text(json.dumps(records,indent=2)+'\n')
 (root/'SHA256SUMS').write_text(''.join(f"{r['sha256']}  {r['file']}\n" for r in records))
 print(name,len(records),'payloads')

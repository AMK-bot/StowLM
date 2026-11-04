import re,sys
from pathlib import Path
s=Path('data/raw/imdg-42-24/msc556-108.txt').read_text()
for page,body in re.findall(r'=== PDF PAGE (\d+) ===(.*?)(?==== PDF PAGE|\Z)',s,re.S):
 if not 581<=int(page)<=752: continue
 for term in sys.argv[1:]:
  pattern=r'\s*'.join(re.escape(c) for c in term)
  for m in re.finditer(pattern,body,re.I):
   print('\nPAGE',page,'TERM',term, '\n',body[max(0,m.start()-12):m.start()+1000])

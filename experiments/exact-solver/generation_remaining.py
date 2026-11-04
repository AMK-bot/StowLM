"""Resume frozen train/validation generation with one bounded child at a time."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--directory',type=Path,required=True)
p.add_argument('--small-offset',type=int,default=1)
p.add_argument('--tiny-offset',type=int,default=1)
p.add_argument('--cap',type=float,default=300.)
a=p.parse_args()
selection=json.loads((a.directory/'selection.json').read_text())['entries']
for rung,offset in [('small',a.small_offset),('tiny',a.tiny_offset)]:
    count=sum(e['rung']==rung for e in selection)
    for index in range(offset,count):
        subprocess.run([sys.executable,str(Path(__file__).with_name('bounded_generation.py')),
            '--cap',str(a.cap),'--directory',str(a.directory),'--rung',rung,
            '--offset',str(index),'--count','1'],check=True)

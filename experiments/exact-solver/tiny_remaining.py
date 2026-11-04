"""Sequential independent tiny-oracle and paired runs on a frozen cohort."""
import argparse
from pathlib import Path
import subprocess
import sys
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--directory',required=True)
p.add_argument('--offset',type=int,default=1)
p.add_argument('--count',type=int,default=2)
a=p.parse_args();script=str(Path(__file__).with_name('current_compare.py'))
for index in range(a.offset,a.offset+a.count):
    common=['--directory',a.directory,'--rung','tiny','--offset',str(index),'--count','1','--c2-off']
    subprocess.run([sys.executable,script,'oracle',*common,'--oracle-limit','10','--max-nodes','100000'],check=True)
    subprocess.run([sys.executable,script,'run',*common,'--time-limit','120','--return-grace','2','--validation-limit','60'],check=True)

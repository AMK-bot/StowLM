"""Plot source-coordinate comparison; no numerical solves or data fitting."""
import json,os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR','/private/tmp/stowage-kcs-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
HERE=Path(__file__).resolve().parent;OUT=HERE/'followup'
d=json.loads((HERE/'iges-upper19-results.json').read_text());rs=d['rows']
x=np.array([r['heel_deg'] for r in rs])
fig,axes=plt.subplots(1,2,figsize=(10.5,4.2),layout='constrained')
for ax,q in zip(axes,['kn','gz']):
 ref=np.array([r[f'reference_{q}_m'] for r in rs])
 tol=np.array([r[f'l5_diagnostic_{q}_tolerance_m'] for r in rs])
 ax.fill_between(x,ref-tol,ref+tol,color='#aaa',alpha=.25)
 for key,col,style,label in [('native16','#b34820','--','Original 16 m closure'),('vertical19','#4b77a1',':','Uniform vertical 19 m'),('iges_upper','#27815e','-','Published IGES upper, cap 19 m')]:
  ax.plot(x,[r[key][f'{q}_m'] for r in rs],color=col,ls=style,lw=2,label=label)
 ax.plot(x,ref,'ko',ms=3,label='Independent published curve')
 ax.set(xlabel='Heel (degrees)',ylabel=q.upper()+' (m)',xlim=(20,50))
 ax.grid(alpha=.2)
axes[0].legend(loc='upper left',fontsize=7)
fig.suptitle('KCS: matched loading and source-derived upper geometry',fontsize=12)
fig.supxlabel('Volume 51,765 m³; KG 13 m; zero trim. Grey band: L5 diagnostic bar. No height/KG fitting.',fontsize=8)
fig.savefig(OUT/'reference-envelopes.png',dpi=180);fig.savefig(OUT/'reference-envelopes.svg')

"""Plot the archived source comparison; requires optional matplotlib only."""
import json
import os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR','/private/tmp/stowage-kcs-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
HERE=Path(__file__).resolve().parent
r=json.loads((HERE/'comparison.json').read_text())
c=next(c for c in r['cases'] if c['case']=='keramati2026')
fig,axes=plt.subplots(1,2,figsize=(10,4.3),layout='constrained')
for ax,kind,label in zip(axes,['kn','gz'],['KN (m)','GZ (m)']):
    rows=[row for row in c['comparisons'] if row['quantity']==kind]
    x=np.array([row['heel_deg'] for row in rows]); ref=np.array([row['reference_m'] for row in rows])
    own=np.array([row['compared_production_m'] for row in rows])
    tol=np.array([row['l5_diagnostic_tolerance_m'] for row in rows])
    ax.fill_between(x,ref-tol,ref+tol,color='#b8b8b8',alpha=.45,label='L5 numerical bar (diagnostic)')
    ax.plot(x,ref,'k-',lw=2,label='Published KCS, depth 19 m')
    ax.plot(x,own,'o-',color='#b34820',markersize=3,lw=1.6,label='Our offset loft, sealed at 16 m')
    ax.axhline(0,color='#888',lw=.5)
    ax.set(xlabel='Heel (degrees)',ylabel=label,xlim=(0,60))
    ax.grid(alpha=.2)
axes[0].legend(loc='upper left',fontsize=7)
fig.suptitle('KCS at volume 51,765 m³, KG 13 m, zero trim',fontsize=12)
fig.supxlabel('Source: Keramati et al., arXiv:2610.12870v1 Fig. 8 (preprint); unmatched upper envelopes',fontsize=8)
fig.savefig(HERE/'comparison.png',dpi=180)
fig.savefig(HERE/'comparison.svg')

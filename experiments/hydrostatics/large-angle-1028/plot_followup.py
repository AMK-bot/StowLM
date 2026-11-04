"""Static figures for the envelope follow-up (no physics calculations)."""
import json,os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR','/private/tmp/stowage-kcs-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
HERE=Path(__file__).resolve().parent; OUT=HERE/'followup'
fig,axes=plt.subplots(1,3,figsize=(13,4),layout='constrained')
styles={'baseline':('#222','Current envelope'),'sealed_cover_0p6':('#176d9b','Conditional sealed cover +0.6 m'),
        'forecastle_2m':('#b4741d','Hypothetical forecastle +2 m'),
        'no_coaming_buoyancy':('#a84754','No existing coaming buoyancy')}
for ax,name in zip(axes,['feeder_500','feeder_800','feeder_1700']):
 d=json.loads((OUT/f'{name}-C1.json').read_text())
 for row in d['rows']:
  if row['variant'] not in styles:continue
  color,label=styles[row['variant']]
  x=np.array([r['heel_deg'] for r in row['sealed_curve']]); y=np.array([r['gz_m'] for r in row['sealed_curve']])
  flood=row['flooding_deg']; xx=np.r_[x[x<flood],flood]; yy=np.interp(xx,x,y)
  ax.plot(xx,yy,color=color,lw=1.8,label=label)
  xx=np.r_[flood,x[x>flood]]; yy=np.interp(xx,x,y)
  ax.plot(xx,yy,color=color,lw=1.1,ls=':')
 base=next(r for r in d['rows'] if r['variant']=='baseline')
 ax.axvline(base['flooding_deg'],color='#888',ls='--',lw=.8)
 ax.axhline(0,color='#888',lw=.5)
 ax.set(title=f"{name.replace('feeder_','Feeder ')} C1",xlabel='Heel (degrees)',xlim=(0,50),ylabel='Corrected GZ (m)')
 ax.grid(alpha=.2)
axes[0].legend(fontsize=7,loc='lower center')
fig.suptitle('Loaded manual C1: envelope sensitivities, weights and openings held fixed',fontsize=12)
fig.supxlabel('Dotted branches are sealed-envelope continuations after flooding; excluded from regulatory areas',fontsize=8)
fig.savefig(OUT/'feeder-curves.png',dpi=170);fig.savefig(OUT/'feeder-curves.svg')

# Reference like-with-like data: schemas are recorded rather than inferred here.
print('saved',OUT/'feeder-curves.png')

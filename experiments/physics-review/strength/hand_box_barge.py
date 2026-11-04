"""Physics review 3.2: closed-form box barge cases vs integrate_strength.

Run: UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/physics-review/strength/hand_box_barge.py
Case A: uniform weight, level -> SF=BM=0.
Case B: uniform buoyancy, midship point P -> BM_mid = -P L/8 (sag), SF jumps +/-P/2.
Case C: box barge, uniform hull w0 plus point P at x=0.9L -> trimmed (trapezoidal) buoyancy
        q(x)=a+b x with a L + b L^2/2 = W, a L^2/2 + b L^3/3 = W*LCG. Closed-form SF/BM, end closure.
Case D: same as C but a deliberate 0.05% moment error in buoyancy: show affine correction vs raw.
"""
import numpy as np
from stowage.checker.strength import integrate_strength, G

def grid(L, n):
    e = np.linspace(0, L, n+1); x=[]; w=[]
    for a,b in zip(e,e[1:]):
        m,h=(a+b)/2,(b-a)/2; x+= [m-h/np.sqrt(3), m+h/np.sqrt(3)]; w+=[h,h]
    return np.array(x), np.array(w)

L=100.; x,w=grid(L,40); st=np.linspace(0,L,201)
# A
c=integrate_strength(x,w,np.full_like(x,1e4*G),[(0,L,1e6)],[],st,lpp_m=L)
print('A uniform: max|SF|=%.2e N max|BM|=%.2e N.m'%(abs(c.shear_n).max(),abs(c.bending_nm).max()))
# B
P=5e5
c=integrate_strength(x,w,np.full_like(x,P*G/L),[],[(50,P)],st,lpp_m=L)
print('B midship point: BM(50)=%.4e hand -PgL/8=%.4e'%(c.bending_nm[100],-P*G*L/8))
# C
w0=1e4; P=2e5; W=(w0*L+P)*G; lcg=(w0*L*L/2+P*0.9*L)/(w0*L+P)
A=np.array([[L,L*L/2],[L*L/2,L**3/3]]); a,b=np.linalg.solve(A,[W,W*lcg])
c=integrate_strength(x,w,a+b*x,[(0,L,w0*L)],[(0.9*L,P)],st,lpp_m=L)
sf=a*st+b*st**2/2-w0*G*st-P*G*(st>=0.9*L)
bm=-(a*st**2/2+b*st**3/6-w0*G*st**2/2-P*G*np.maximum(st-0.9*L,0))
print('C end load trimmed: raw end SF=%.2e N, raw end BM=%.2e N.m; max|SF-hand|=%.2e max|BM-hand|=%.2e; peak BM=%.4e (hog +)'%(
    c.raw_end_shear_n,c.raw_end_bending_nm,abs(c.shear_n-sf).max(),abs(c.bending_nm-bm).max(),c.bending_nm[np.argmax(abs(c.bending_nm))]))
# D: inject first-moment error eps*W*L into buoyancy (shift centroid by eps*L)
eps=5e-4
a2,b2=np.linalg.solve(A,[W,W*(lcg+eps*L)])
c2=integrate_strength(x,w,a2+b2*x,[(0,L,w0*L)],[(0.9*L,P)],st,lpp_m=L)
print('D 0.05%%L buoyancy shift: raw end BM=%.3e (=%.1e WL); corrected max|BM-hand C|=%.3e N.m = %.2f%% of peak'%(
    c2.raw_end_bending_nm,c2.raw_end_bending_nm/(W*L),abs(c2.bending_nm-bm).max(),100*abs(c2.bending_nm-bm).max()/abs(bm).max()))

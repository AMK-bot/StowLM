import sys, time
from stowage.io import load_instance
for p in sys.argv[1:]:
    t=time.monotonic()
    try:
        i=load_instance(p)
    except Exception as e:
        print(p, "LOAD FAIL", type(e).__name__, str(e)[:200]); continue
    lc = getattr(i,'load_list',None)
    fields = list(type(i).model_fields)
    print(p, i.id, "fields", fields if p==sys.argv[1] else "", f"{time.monotonic()-t:.1f}s")
    for f in ("load_list","containers","boxes","yard","onboard","ports"):
        v=getattr(i,f,None)
        if v is not None:
            try: print("  ",f,len(v))
            except TypeError: print("  ",f,type(v).__name__)

from pathlib import Path
import shutil
h=Path(__file__).resolve().parent;r=h.parents[4];v=h/'validation'
for name in ('data','params'):
    if (v/name).is_symlink():(v/name).unlink()
shutil.copytree(r/'params',v/'params',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
shutil.copytree(r/'data/raw/kcs',v/'data/raw/kcs',dirs_exist_ok=True)

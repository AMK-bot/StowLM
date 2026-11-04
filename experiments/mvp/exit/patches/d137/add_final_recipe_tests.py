from pathlib import Path
h=Path(__file__).resolve().parent;p=h/'package_tests.py';s=p.read_text();needle="parts=[];targets={}"
extra='''source=(h/'final-recipe-tests/test_single_finisher.py').read_text()
start=source.index('PATH=') if 'PATH=' in source else -1
# The final-recipe lane exposes a small loader; convert only that staged loader.
if start>=0:
    end=source.index('\\n\\n',start)
    source=source[:start]+'from llm.exit import final_recipe as F\\n'+source[end:]
(t/'test_d139_single_finisher.py').write_text(source)
'''
# Actual lane structure is handled below after inspecting it.
p.write_text(s)

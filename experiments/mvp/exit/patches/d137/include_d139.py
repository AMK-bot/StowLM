from pathlib import Path
p=Path(__file__).with_name('package_tests.py');s=p.read_text();needle='parts=[];targets={}'
extra="""source=(h/'final-recipe-tests/test_single_finisher.py').read_text()
start=source.index('PATH=');end=source.index('\\n\\n@pytest.fixture',start)
source=source[:start]+'from llm.exit import final_recipe as F\\n'+source[end:]
(t/'test_d139_single_finisher.py').write_text(source)
"""
s=s.replace(needle,extra+needle);p.write_text(s)

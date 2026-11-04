from pathlib import Path
from pypdf import PdfReader
for folder in ('iacs', 'irs'):
    for path in sorted((Path('data/raw') / folder).glob('*.pdf')):
        reader = PdfReader(path)
        text = '\n\n'.join(f'=== PDF PAGE {i+1} ===\n' + (page.extract_text() or '') for i, page in enumerate(reader.pages))
        path.with_suffix('.txt').write_text(text)
        print(path, len(reader.pages), len(text))

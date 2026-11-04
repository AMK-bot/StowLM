"""Extract untrusted public IMO PDFs without executing their content."""
from pathlib import Path
from pypdf import PdfReader
for folder in ('imdg-42-24', 'is-code'):
    for pdf in Path('data/raw', folder).glob('*.pdf'):
        reader = PdfReader(pdf)
        output = '\n'.join(f'\n=== PDF PAGE {n} ===\n{page.extract_text() or ""}' for n, page in enumerate(reader.pages, 1))
        pdf.with_suffix('.txt').write_text(output)
        print(pdf, len(reader.pages), 'pages')

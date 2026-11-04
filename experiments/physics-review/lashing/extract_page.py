"""Extract single PDF pages (1-based) to separate PDFs for visual checking."""
import sys
from pypdf import PdfReader, PdfWriter
src, out, *pages = sys.argv[1:]
r = PdfReader(src)
for p in pages:
    w = PdfWriter(); w.add_page(r.pages[int(p)-1]); w.write(f"{out}/p{p}.pdf")

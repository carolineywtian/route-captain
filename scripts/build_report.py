"""Build the report and slides: embed the screenshots so each is one self-contained file, then print a PDF.

    .venv/bin/python scripts/build_report.py

Edit report/source.html (it points at report/img/*.jpg). This writes:
- report/report.html  with every screenshot embedded, so it shows them wherever it is opened;
- report/report.pdf   letter-size, printed with Google Chrome if it is installed;
- slides.html         the reveal.js slide deck (from report/slides_source.html), self-contained.
"""
from __future__ import annotations

import base64
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPORT = Path(__file__).resolve().parent.parent / "report"
CHROME = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "", shutil.which("chrome") or "",
]


def _uri(rel: str) -> str:
    path = REPORT / rel
    if not path.exists():
        sys.exit(f"Missing image: {path}")
    mime = {".png": "image/png", ".svg": "image/svg+xml"}.get(path.suffix.lower(), "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def embed(html: str) -> str:
    """Replace every img/... reference (in src="", url(...) or a quoted string) with the image itself."""
    return re.sub(r'(?<=[(\'"])(img/[\w.-]+\.(?:svg|png|jpe?g))(?=[)\'"])', lambda m: _uri(m.group(1)), html)


def build_slides(report_source: str):
    """slides.html (reveal.js from a CDN) with the screenshots and the report's agent diagram built in."""
    src = REPORT / "slides_source.html"
    if not src.exists():
        return
    diagram = re.search(r'<svg class="diagram".*?</svg>', report_source, re.DOTALL).group(0)
    html = embed(src.read_text(encoding="utf-8").replace("<!--DIAGRAM-->", diagram))
    out = REPORT.parent / "slides.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out.relative_to(REPORT.parent)} ({out.stat().st_size / 1e6:.1f} MB, "
          f"{html.count('<section')} slides, screenshots and diagram embedded)")


def main():
    build_slides((REPORT / "source.html").read_text(encoding="utf-8"))
    html = embed((REPORT / "source.html").read_text(encoding="utf-8"))
    out = REPORT / "report.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out.relative_to(REPORT.parent)} ({out.stat().st_size / 1e6:.1f} MB, screenshots embedded)")
    chrome = next((c for c in CHROME if c and Path(c).exists()), None)
    if not chrome:
        print("Chrome not found: open report/report.html and print it to PDF instead.")
        return
    pdf = REPORT / "report.pdf"
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", out.as_uri()], check=True, capture_output=True)
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes()))
    print(f"wrote {pdf.relative_to(REPORT.parent)} ({pages} pages)")


if __name__ == "__main__":
    main()

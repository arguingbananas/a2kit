#!/usr/bin/env python3
"""Build PDF copies of the docs:  python3 docs/make_pdfs.py

Markdown -> HTML (pandoc, GitHub-style heading ids) -> PDF (headless Chrome).
Needs pandoc and google-chrome (or chromium) on PATH. Writes INSTALL.pdf, QUICKSTART.pdf and
MANUAL.pdf next to the markdown files. Links between the documents become relative
'open that PDF' links (keep the PDFs in one folder); in-document links stay clickable.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = [("INSTALL", "a2kit Installation Guide"), ("QUICKSTART", "a2kit Quick Start"), ("MANUAL", "a2kit User Manual")]

CSS = """
@page { size: Letter; margin: 0.8in 0.75in 0.9in 0.75in;
        @bottom-center { content: counter(page) " / " counter(pages); font: 9pt Helvetica, Arial, sans-serif; color: #667; }
        @bottom-left   { content: "a2kit"; font: 9pt Helvetica, Arial, sans-serif; color: #667; } }
html { font-family: "Helvetica Neue", Helvetica, Arial, "DejaVu Sans", sans-serif; font-size: 10.5pt; line-height: 1.45; color: #1c2630; }
body { max-width: none; margin: 0; }
h1 { font-size: 24pt; border-bottom: 2px solid #2f6fb0; padding-bottom: 6px; margin-top: 0; }
h2 { font-size: 16pt; color: #2f6fb0; border-bottom: 1px solid #d5dde5; padding-bottom: 3px; margin-top: 1.6em; break-after: avoid; }
h3 { font-size: 12.5pt; margin-top: 1.3em; break-after: avoid; }
h1, h2, h3, h4 { line-height: 1.2; }
a { color: #2f6fb0; text-decoration: none; }
code { font-family: Menlo, Consolas, "DejaVu Sans Mono", monospace; font-size: 9pt; background: #eef1f4; padding: 1px 4px; border-radius: 3px; }
pre { background: #f4f6f8; border: 1px solid #d5dde5; border-radius: 5px; padding: 8px 10px; white-space: pre-wrap; overflow-wrap: anywhere; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.8pt; }
/* pandoc's print CSS gives wrapped code a hanging indent; undo it */
pre > code.sourceCode > span { display: inline !important; text-indent: 0 !important; padding-left: 0 !important; }
table { border-collapse: collapse; width: 100%; margin: 0.7em 0; font-size: 9.6pt; }
th { background: #e6f0fa; text-align: left; }
th, td { border: 1px solid #c9d3dd; padding: 4px 7px; vertical-align: top; }
tr { break-inside: avoid; }
td code, th code { font-size: 8.6pt; overflow-wrap: anywhere; }
blockquote { margin: 0.8em 0; padding: 6px 12px; background: #fff8dc; border-left: 4px solid #c9a400; }
blockquote p { margin: 0.3em 0; }
img { max-width: 100%; display: block; margin: 0.8em auto; }
hr { border: 0; border-top: 1px solid #d5dde5; margin: 1.5em 0; }
li { margin: 0.15em 0; }
"""


def chrome():
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return name
    sys.exit("need google-chrome or chromium on PATH")


def relativise_links(path):
    """Chrome writes links to the other PDFs as absolute file:// URLs for the machine
    that built them, which are dead anywhere else. Turn them into relative
    'go to file' actions, so they work when the PDFs sit in the same folder."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject
    writer = PdfWriter(clone_from=PdfReader(path))
    for page in writer.pages:
        for ref in page.get("/Annots") or []:
            a = ref.get_object()
            act = a.get("/A")
            uri = act.get("/URI") if act is not None else None
            if a.get("/Subtype") == "/Link" and uri and str(uri).startswith("file://") and str(uri).endswith(".pdf"):
                a[NameObject("/A")] = DictionaryObject({
                    NameObject("/S"): NameObject("/GoToR"),
                    NameObject("/F"): TextStringObject(os.path.basename(str(uri))),
                    NameObject("/D"): ArrayObject([NumberObject(0), NameObject("/Fit")]),
                })
    with open(path, "wb") as f:
        writer.write(f)


def main():
    if not shutil.which("pandoc"):
        sys.exit("need pandoc on PATH")
    browser = chrome()
    tmp = tempfile.mkdtemp()
    css = os.path.join(tmp, "doc.css")
    open(css, "w").write(CSS)
    for stem, title in DOCS:
        text = open(os.path.join(HERE, stem + ".md"), encoding="utf-8").read()
        # links to another doc become links to its PDF; links into this doc stay in-document
        def relink(m):
            other, frag = m.group(1), m.group(2) or ""
            return "(%s)" % (frag if other == stem and frag else other + ".pdf")
        text = re.sub(r"\((QUICKSTART|MANUAL|INSTALL)\.md(#[\w-]+)?\)", relink, text)
        text = re.sub(r"\[([^\]]+)\]\(workflow\.svg\)", r"\1 (in the docs folder)", text)
        md = os.path.join(HERE, "." + stem + ".pdfbuild.md")      # next to the images so relative paths work
        html = os.path.join(HERE, "." + stem + ".pdfbuild.html")
        try:
            open(md, "w", encoding="utf-8").write(text)
            subprocess.run(["pandoc", md, "-f", "gfm", "-t", "html5", "-s", "--embed-resources", "--css", css,
                            "--metadata", "pagetitle=" + title, "--resource-path", HERE, "-o", html], check=True)
            out = os.path.join(HERE, stem + ".pdf")
            subprocess.run([browser, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                            "--print-to-pdf=" + out, "file://" + html], check=True, capture_output=True)
            relativise_links(out)
            print("wrote", out, "(%d KB)" % (os.path.getsize(out) // 1024))
        finally:
            for f in (md, html):
                if os.path.exists(f):
                    os.remove(f)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

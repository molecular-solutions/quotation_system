"""Fidelity gate: the engine fed the template's own sample must render identically."""
import io
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.services.docgen import TEMPLATE_PATH, render_quote, template_sample
from app.services.pdf import docx_to_pdf

pytestmark = pytest.mark.skipif(not shutil.which("soffice") or not shutil.which("pdftoppm"),
                                reason="needs LibreOffice and poppler")


def pages(pdf_bytes: bytes, tmp_path: Path, tag: str, dpi=80) -> list[np.ndarray]:
    pdf = tmp_path / f"{tag}.pdf"
    pdf.write_bytes(pdf_bytes)
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(tmp_path / tag)], check=True)
    return [np.asarray(Image.open(p).convert("L")) for p in sorted(tmp_path.glob(f"{tag}-*.png"))]


PRICE_COL_X = 460  # px at 80 dpi: UNIT PRICE / AMOUNT columns start here on the pricing page


def test_template_sample_roundtrips_pixel_identical(tmp_path):
    """Only the price cells may differ: the template splits '₹ ' and 'x' into two runs, the
    engine writes one value into the first run (so the 'x' glyph shifts by a pixel)."""
    original = pages(docx_to_pdf(TEMPLATE_PATH.read_bytes()), tmp_path, "orig")
    generated = pages(docx_to_pdf(render_quote(template_sample(), reuse_template_rows=True)), tmp_path, "gen")
    assert len(original) == len(generated)
    for i, (a, b) in enumerate(zip(original, generated), start=1):
        assert a.shape == b.shape
        diff = a != b
        if i <= 2:
            diff[:, PRICE_COL_X:] = False
        assert int(diff.sum()) == 0, f"page {i}: {int(diff.sum())} pixels differ outside the price columns"


def test_output_has_no_duplicate_paragraph_ids(tmp_path):
    import re
    import zipfile
    q = template_sample()
    q.lines = q.lines * 3  # forces cloned rows
    xml = zipfile.ZipFile(io.BytesIO(render_quote(q))).read("word/document.xml").decode()
    ids = re.findall(r'w14:paraId="([0-9A-F]+)"', xml)
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("address,email", [
    ("Department of BioPhysics, JSS, Beng", "anantha@example.com"),   # short: used to leave the whole header in column 1
    ("Dept", ""),
    ("Department of Biophysics, All India Institute of Medical Sciences, Ansari Nagar, New Delhi, 110029, India, "
     "with a long extra line", "a@b.in"),
])
def test_header_and_pricing_table_share_page_one_whatever_the_address(tmp_path, address, email):
    """The template's 2-column header only split at the e-mail for the sample address; the table must stay on page 1."""
    q = template_sample()
    q.customer_address, q.customer_email, q.lines = address, email, q.lines[:3]
    pdf = tmp_path / "q.pdf"
    pdf.write_bytes(docx_to_pdf(render_quote(q)))
    text = subprocess.run(["pdftotext", "-f", "1", "-l", "1", "-layout", str(pdf), "-"], capture_output=True, text=True,
                          check=True).stdout
    for needle in ("QUOTATION", "PRICING INFORMATION", "PRODUCT NAME", "TOTAL AMOUNT INCLUDING GST"):
        assert needle in text, f"{needle!r} not on page 1"


# Where the Word-made reference PDF (MS-AB-174) puts the header lines on page 1, in points from the top-left.
REFERENCE_HEADER = {  # first word of the line -> (x, y)
    "QUOTATION": (19.9, 56.4), "QUOTE": (21.1, 113.4), "DATE": (21.1, 129.5), "TO": (21.1, 173.4),
    "Molecular": (350.5, 106.2), "No": (367.8, 120.4), "PRICING": (21.1, 311.0),
}


def test_header_matches_the_word_designed_reference(tmp_path):
    import re
    q = template_sample()
    pdf = tmp_path / "q.pdf"
    pdf.write_bytes(docx_to_pdf(render_quote(q)))
    html = subprocess.run(["pdftotext", "-f", "1", "-l", "1", "-bbox-layout", str(pdf), "-"],
                          capture_output=True, text=True, check=True).stdout
    first = {}
    for m in re.finditer(r'<line xMin="([\d.]+)" yMin="([\d.]+)"[^>]*>\s*<word[^>]*>([^<]+)</word>', html):
        first.setdefault(m.group(3), (float(m.group(1)), float(m.group(2))))
    for word, (x, y) in REFERENCE_HEADER.items():
        gx, gy = first[word]
        assert abs(gx - x) < 2 and abs(gy - y) < 2, f"{word}: at ({gx}, {gy}), reference ({x}, {y})"


def test_header_has_banner_logo_and_no_column_flow(tmp_path):
    import zipfile
    xml = zipfile.ZipFile(io.BytesIO(render_quote(template_sample()))).read("word/document.xml").decode()
    assert "<wp:anchor" in xml and "Header banner" in xml        # the blue swoosh
    assert 'w:num="2"' not in xml                                # the old 2-column flow in the header is gone


def test_address_lines_break_where_the_customer_record_does(tmp_path):
    import zipfile
    q = template_sample()
    q.customer_address = "Department of Biophysics, AIIMS\nAnsari Nagar, New Delhi"
    xml = zipfile.ZipFile(io.BytesIO(render_quote(q))).read("word/document.xml").decode()
    assert "AIIMS</w:t><w:br/><w:t" in xml.replace(' xml:space="preserve"', "")

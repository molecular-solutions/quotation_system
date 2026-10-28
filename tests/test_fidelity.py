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


def _words(pdf_path, page):
    """{word: [(x, y), ...]} for every word on a page, in points from the top-left."""
    import re
    html = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-bbox", str(pdf_path), "-"],
                          capture_output=True, text=True, check=True).stdout
    out = {}
    for m in re.finditer(r'<word xMin="([\d.]+)" yMin="([\d.]+)"[^>]*>([^<]+)</word>', html):
        out.setdefault(m.group(3), []).append((float(m.group(1)), float(m.group(2))))
    return out


def test_header_follows_the_purchase_order_layout(tmp_path):
    """TO block on the left and the details box on the right, level with each other; no company address under the
    logo and no Bill To / Deliver To; the company details live in the footer."""
    q = template_sample()
    q.customer_name, q.customer_address, q.customer_email = "Patsnap Pte Ltd", "75 High Street\n#04-00\nSingapore 179435", "k@x.in"
    q.number, q.license_type, q.license_duration = "MS-CA-26-058", "Commercial License", "1 Year"
    pdf = tmp_path / "q.pdf"
    pdf.write_bytes(docx_to_pdf(render_quote(q)))
    w = _words(pdf, 1)
    (to_x, to_y), (box_x, box_y) = w["TO"][0], w["Quote"][0]
    assert to_x < 40 and box_x > 330 and abs(box_y - to_y) < 25, "TO block left, details box right, side by side"
    assert w["Number"][0][0] > box_x and w["Date"][0][0] > box_x            # labels: Quote Number / Quote Date
    assert w["License"][0][0] >= box_x - 1 and w["Duration"][0][0] > box_x       # License Type / License Duration
    assert w["Patsnap"][0][1] > to_y and w["Singapore"][0][1] > w["Patsnap"][0][1]   # name, then the address, under the bar
    for gone in ("BILL", "DELIVER", "Deliver", "Bill"):
        assert gone not in w
    # the company address is only in the footer now: every occurrence is near the bottom of the page
    assert all(y > 740 for _, y in w["Kodipalya,"]) and w["Kodipalya,"]
    assert all(y > 740 for _, y in w["sales@molecularsolutions.co.in"])
    text = subprocess.run(["pdftotext", "-f", "1", "-l", "1", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    assert "rraghu@" not in text and "1 Year" in text and "Commercial License" in text


def test_every_page_has_the_footer(tmp_path):
    pdf = tmp_path / "q.pdf"
    pdf.write_bytes(docx_to_pdf(render_quote(template_sample())))
    n = int(subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout.split("Pages:")[1].split()[0])
    assert n >= 4
    for page in range(1, n + 1):
        w = _words(pdf, page)
        assert w.get("molecularsolutions.co.in") and "+91-99000" in w, f"page {page} has no footer"


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

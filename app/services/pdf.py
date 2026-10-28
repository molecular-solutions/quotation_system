"""DOCX -> PDF through headless LibreOffice (needs Calibri-metric font Carlito installed)."""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


class PdfUnavailable(RuntimeError):
    pass


_EMBED = re.compile(rb"<w:embed(?:Regular|Bold|Italic|BoldItalic)\b[^>]*/>")


def _without_embedded_fonts(docx_bytes: bytes) -> bytes:
    """LibreOffice nondeterministically picks the template's embedded Noto Sans Symbols
    (bullets render as boxes some runs). Dropping the embed references for the PDF pass
    makes it use the installed font every time; the .docx deliverable is untouched."""
    src = zipfile.ZipFile(io.BytesIO(docx_bytes))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "word/fontTable.xml":
                data = _EMBED.sub(b"", data)
            dst.writestr(item, data)
    return out.getvalue()


# Lossless images at their native resolution: the logo stays crisp instead of being JPEG-compressed to 300 dpi.
PDF_FILTER = ('pdf:writer_pdf_Export:{"UseLosslessCompression":{"type":"boolean","value":"true"},'
              '"ReduceImageResolution":{"type":"boolean","value":"false"}}')


def docx_to_pdf(docx_bytes: bytes, timeout: int = 90) -> bytes:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise PdfUnavailable("LibreOffice (soffice) is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / "quote.docx"
        src.write_bytes(_without_embedded_fonts(docx_bytes))
        # own profile dir per call so concurrent conversions don't lock each other
        profile = tmp_dir / "lo-profile"
        cmd = [soffice, f"-env:UserInstallation=file://{profile}", "--headless",
               "--convert-to", PDF_FILTER, "--outdir", str(tmp_dir), str(src)]
        res = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = tmp_dir / "quote.pdf"
        if res.returncode != 0 or not out.exists():
            raise PdfUnavailable(f"PDF conversion failed: {res.stderr.decode(errors='replace')[:300]}")
        return out.read_bytes()

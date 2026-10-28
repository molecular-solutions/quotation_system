"""Render a few synthetic quotes (3 / 20 lines, with discount) for eyeballing."""
import subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.docgen import DocLine, DocQuote, render_quote, template_sample
from app.services.pdf import docx_to_pdf

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
base = template_sample()
names = ["SeeSAR with YASARA Plugin", "HPSee-Chemical Space Docking (128 Cores)", "infiniSee – Three Search Modes",
         "XFEP (Free Energy Perturbation) & XMolGen (20,000 Xunits valid for 3 years only)", "Retrosynthesis Modules"]
def lines(n):
    return [DocLine(names[i % 5] + (f" #{i}" if i >= 5 else ""), "Local" if i % 2 else "Cloud", str(i % 4 + 1),
                    f"{i % 3 + 1} Years", "₹ 85,000.00", f"₹ {85000*(i%4+1)*(i%3+1):,.2f}") for i in range(n)]
def make(n, discount):
    q = DocQuote(**{**base.__dict__})
    q.number, q.customer_name, q.license_type = "MS-AB-175", "Dr. Priya Rao", "Commercial License"
    q.lines = lines(n)
    q.summary = ([("LIST TOTAL", "₹ 10,00,000.00"), ("DISCOUNT 10%", "- ₹ 1,00,000.00")] if discount else []) + [
        ("SUB TOTAL", "₹ 9,00,000.00"), ("CGST 9%", "₹ 81,000.00"), ("SGST 9%", "₹ 81,000.00"),
        ("TOTAL AMOUNT INCLUDING GST", "₹ 10,62,000.00")]
    q.quoted_blocks = {"commander", "yasara"}
    return q
for tag, n, d in (("three", 3, False), ("twenty", 20, True)):
    pdf = docx_to_pdf(render_quote(make(n, d)))
    (out / f"{tag}.pdf").write_bytes(pdf)
    subprocess.run(["pdftoppm", "-r", "60", "-png", str(out / f"{tag}.pdf"), str(out / tag)], check=True)
print(sorted(p.name for p in out.glob("*.png")))

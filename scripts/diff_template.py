"""Render template vs engine output for the template's own sample and report where pixels differ."""
import subprocess, sys
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.docgen import TEMPLATE_PATH, render_quote, template_sample
from app.services.pdf import docx_to_pdf

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
def render(tag, docx_bytes):
    (out / f"{tag}.docx").write_bytes(docx_bytes)
    (out / f"{tag}.pdf").write_bytes(docx_to_pdf(docx_bytes))
    subprocess.run(["pdftoppm", "-r", "80", "-png", str(out / f"{tag}.pdf"), str(out / tag)], check=True)
    return [np.asarray(Image.open(p).convert("L")) for p in sorted(out.glob(f"{tag}-*.png"))]
a = render("orig", TEMPLATE_PATH.read_bytes()); b = render("gen", render_quote(template_sample(), reuse_template_rows=True))
print("pages", len(a), len(b))
for i, (x, y) in enumerate(zip(a, b), 1):
    d = x != y
    if d.any():
        ys, xs = np.where(d)
        print(f"page {i}: {d.sum()} px, bbox x {xs.min()}-{xs.max()} y {ys.min()}-{ys.max()}")
        Image.fromarray((~d * 255).astype("uint8")).save(out / f"diff-{i}.png")

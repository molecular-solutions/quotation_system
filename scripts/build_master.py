"""Build templates/master_quotation.docx from reference/Quotation_Template.docx.

The shared template is a PDF-to-Word conversion whose top section is a fragile 2-column flow. This one-off script
rebuilds only that top section to match the Word-made design (reference PDF): banner swoosh, QUOTATION title, quote
number/date, logo + company block on the right, TO block, navy table header. Everything below (terms, catalog,
pricing rows) is the template's own content, untouched.

    python scripts/build_master.py

It always starts from the reference file, so running it again gives the same result."""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import docx
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {
    "w": W, "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
NSDECL = " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items())
NAVY, BLACK, BLUE, LINK = "001F5F", "000000", "0000FF", "0462C1"

# Vertical rhythm, in points, tuned so the page matches the reference PDF (see tests/test_fidelity.py).
GAP = dict(title=29.2, quote_no=11.2, date=1.5, to=29.2, name=4.1, address=6.0, email=0.0, license=0.0,
           logo=0.0, company=6.9, company_addr=0.0, company_mail=0.0, pricing=31.0, pricing_after=19.6)
TWIP = 20


def tw(pt: float) -> str:
    return str(round(pt * TWIP))


def run(text="", *, sz=24, bold=False, color=BLACK, underline=False, tab=False) -> str:
    rpr = (f'<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri" w:eastAsia="Calibri"/>'
           f'{"<w:b/><w:bCs/>" if bold else ""}<w:color w:val="{color}"/><w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>'
           f'{"<w:u w:val=" + chr(34) + "single" + chr(34) + "/>" if underline else ""}')
    body = "<w:tab/>" if tab else f'<w:t xml:space="preserve">{text}</w:t>'
    return f"<w:r><w:rPr>{rpr}</w:rPr>{body}</w:r>"


def para(runs: str, *, before=0.0, after=0.0, left=0.0, right=0.0, jc="left", tabs=(), line=None) -> str:
    tab_xml = "".join(f'<w:tab w:val="left" w:pos="{tw(t)}"/>' for t in tabs)
    ln = f' w:line="{line}" w:lineRule="auto"' if line else ' w:line="240" w:lineRule="auto"'
    return (f'<w:p {NSDECL}><w:pPr>{"<w:tabs>" + tab_xml + "</w:tabs>" if tabs else ""}'
            f'<w:spacing w:before="{tw(before)}" w:after="{tw(after)}"{ln}/>'
            f'<w:ind w:left="{tw(left)}" w:right="{tw(right)}" w:firstLine="0"/><w:jc w:val="{jc}"/></w:pPr>{runs}</w:p>')


def el(xml: str):
    return etree.fromstring(xml)


def swoosh_anchor(rid: str, cx: int, cy: int) -> str:
    return f'''<w:r {NSDECL}><w:drawing><wp:anchor distT="0" distB="0" distL="0" distR="0" simplePos="0" relativeHeight="1"
 behindDoc="1" locked="0" layoutInCell="1" allowOverlap="1"><wp:simplePos x="0" y="0"/>
 <wp:positionH relativeFrom="page"><wp:posOffset>0</wp:posOffset></wp:positionH>
 <wp:positionV relativeFrom="page"><wp:posOffset>0</wp:posOffset></wp:positionV>
 <wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/>
 <wp:docPr id="900" name="Header banner" descr=""/><wp:cNvGraphicFramePr/>
 <a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>
 <pic:nvPicPr><pic:cNvPr id="900" name="header_swoosh.png"/><pic:cNvPicPr/></pic:nvPicPr>
 <pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
 <pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
 </pic:pic></a:graphicData></a:graphic></wp:anchor></w:drawing></w:r>'''


def cell(width_tw: int, paras: list[str]) -> str:
    return (f'<w:tc><w:tcPr><w:tcW w:w="{width_tw}" w:type="dxa"/><w:tcMar><w:top w:w="0" w:type="dxa"/>'
            f'<w:left w:w="0" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tcMar>'
            f'<w:vAlign w:val="top"/></w:tcPr>{"".join(paras)}</w:tc>')


def build_header(logo_run_xml: str, left_w: int, right_w: int):
    g = GAP
    left = [
        para(run("QUOTATION", sz=75, bold=True, color=NAVY), before=g["title"], left=1.0),
        para(run("QUOTE NO", bold=True, color=NAVY) + run(tab=True) + run("MS-AB-174", bold=True, color=NAVY),
             before=g["quote_no"], left=1.9, tabs=(88.9,)),
        para(run("DATE", bold=True, color=NAVY) + run(tab=True) + run("19/10/2026", color=NAVY),
             before=g["date"], left=1.9, tabs=(88.9,)),
        para(run("TO", bold=True, color=NAVY), before=g["to"], left=1.9),
        para(run("Dr. Adam,", bold=True), before=g["name"], left=2.4),
        para(run("Department of Biophysics, AIIMS, Ansari Nagar, New Delhi – 110029"),
             before=g["address"], left=2.4, right=133.0, line=275),
        para(run("adam@example.com", color=BLUE, underline=True), before=g["email"], left=2.4, line=275),
        para(run("Academic License"), before=g["license"], left=2.4, line=275),
    ]
    right = [
        para(logo_run_xml, before=g["logo"], jc="right", right=2.7),
        para(run("Molecular Solutions Software Private Limited", bold=True, color=NAVY),
             before=g["company"], jc="right", right=2.7),
        para(run("No 110, West Village, Kodipalya, Bangalore-560060", sz=20, color=NAVY),
             before=g["company_addr"], jc="right", right=2.7),
        para(run("Email: ", sz=20, color=NAVY) + run("rraghu@molecularsolutions.co.in", sz=20, color=LINK, underline=True),
             before=g["company_mail"], jc="right", right=2.7),
    ]
    tbl = (f'<w:tbl {NSDECL}><w:tblPr><w:tblW w:w="{left_w + right_w}" w:type="dxa"/><w:tblInd w:w="0" w:type="dxa"/>'
           f'<w:tblBorders><w:top w:val="nil"/><w:left w:val="nil"/><w:bottom w:val="nil"/><w:right w:val="nil"/>'
           f'<w:insideH w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders><w:tblLayout w:type="fixed"/>'
           f'<w:tblCellMar><w:left w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tblCellMar>'
           f'<w:tblLook w:val="0000"/></w:tblPr><w:tblGrid><w:gridCol w:w="{left_w}"/><w:gridCol w:w="{right_w}"/></w:tblGrid>'
           f'<w:tr><w:trPr><w:cantSplit/></w:trPr>{cell(left_w, left)}{cell(right_w, right)}</w:tr></w:tbl>')
    return el(tbl)


# ── pricing table: navy header like the reference, columns wide enough for their titles ───────────────────
HEADERS = ["S.NO", "PRODUCT NAME", "DEPLOYMENT", "USERS", "DURATION", "UNIT PRICE", "AMOUNT (INR)"]
WIDTHS = [600, 3220, 1425, 1635, 1100, 1425, 1830]  # sums to the table's 11235 twips (was 570/3285/…/1035/…/1860)


def w_(tag):
    return f"{{{W}}}{tag}"


def restyle_pricing_table(tbl) -> None:
    grid = tbl.find(w_("tblGrid")).findall(w_("gridCol"))
    assert [int(float(g.get(w_("w")))) for g in grid[:7]] == [570, 3285, 1425, 1635, 1035, 1425, 1860], "table changed"
    for g, width in zip(grid, WIDTHS):
        g.set(w_("w"), str(width))
    for tr in tbl.findall(w_("tr")):
        col = 0
        for tc in tr.findall(w_("tc")):
            span_el = tc.find(w_("tcPr") + "/" + w_("gridSpan"))
            span = int(span_el.get(w_("val"))) if span_el is not None else 1
            tcw = tc.find(w_("tcPr") + "/" + w_("tcW"))
            if tcw is not None:
                tcw.set(w_("w"), str(sum(WIDTHS[col:col + span])))
            col += span
    for tc, text in zip(tbl.find(w_("tr")).findall(w_("tc")), HEADERS):
        tcpr = tc.find(w_("tcPr"))
        shd = tcpr.find(w_("shd"))
        shd.set(w_("fill"), NAVY)
        mar = tcpr.find(w_("tcMar"))
        for side in ("left", "right"):
            mar.find(w_(side)).set(w_("w"), "80")
        paras = tc.findall(w_("p"))
        for extra in paras[1:]:
            tc.remove(extra)
        p = paras[0]
        runs = p.findall(w_("r"))
        keep = runs[0]
        for r in runs[1:]:
            p.remove(r)
        for t in keep.findall(w_("t")):
            keep.remove(t)
        rpr = keep.find(w_("rPr"))
        for tag in ("shd", "rtl"):
            for x in rpr.findall(w_(tag)):
                rpr.remove(x)
        etree.SubElement(keep, w_("t")).text = text
        ppr = p.find(w_("pPr"))
        ppr.find(w_("jc")).set(w_("val"), "left")
        ppr.find(w_("ind")).set(w_("right"), "0")


def main() -> None:
    src = ROOT / "reference" / "Quotation_Template.docx"
    out = ROOT / "templates" / "master_quotation.docx"
    document = docx.Document(str(src))
    body = document.element.body
    kids = list(body)
    assert "".join(kids[0].itertext()).startswith("QUOTATION") and kids[11].tag == w_("tbl"), "unexpected template"

    # logo: the new, sharper file replaces the template's picture (same frame, so nothing moves)
    from PIL import Image
    import io
    logo = Image.open(ROOT / "templates" / "logo_source.png").convert("RGBA")
    ratio = 1695450 / 915670
    canvas = Image.new("RGBA", (round(logo.height * ratio), logo.height), (255, 255, 255, 0))
    canvas.paste(logo, ((canvas.width - logo.width) // 2, 0))
    buf = io.BytesIO()
    canvas.save(buf, "PNG", optimize=True)
    for rel in document.part.rels.values():
        if rel.reltype.endswith("/image") and rel.target_part.partname.endswith("image1.png"):
            rel.target_part._blob = buf.getvalue()

    logo_run = copy.deepcopy(kids[8].find(w_("r") + "/" + w_("drawing")).getparent())
    for rpr in logo_run.findall(w_("rPr")):
        logo_run.remove(rpr)
    logo_xml = etree.tostring(logo_run).decode()

    sw_path = ROOT / "templates" / "header_swoosh.png"
    rid, _ = document.part.get_or_add_image(str(sw_path))
    sw_w, sw_h = Image.open(sw_path).size
    cx, cy = round(sw_w / 600 * 914400), round(sw_h / 600 * 914400)

    anchor_p = el(f'<w:p {NSDECL}><w:pPr><w:spacing w:before="0" w:after="0" w:line="20" w:lineRule="exact"/>'
                  f'<w:rPr><w:sz w:val="2"/></w:rPr></w:pPr>{swoosh_anchor(rid, cx, cy)}</w:p>')
    header = build_header(logo_xml, left_w=6300, right_w=4890)
    pricing = el(para(run("PRICING INFORMATION", bold=True, color=NAVY, underline=True),
                      before=GAP["pricing"], after=GAP["pricing_after"], left=1.4))

    # keep "Terms & Conditions" together: if it does not fit under the (now taller) header it moves to page 2 whole
    for k in kids[14:20]:
        k.find(w_("pPr")).find(w_("keepNext")).set(w_("val"), "1")
    kids[21].find(w_("pPr")).find(w_("pageBreakBefore")).set(w_("val"), "1")  # the catalog starts on its own page, as in the reference
    for k in kids[:11]:
        body.remove(k)  # old header paragraphs, the column-flow section break (inside kids[9]) and the spacer
    kids[11].addprevious(anchor_p)
    kids[11].addprevious(header)
    kids[11].addprevious(pricing)
    restyle_pricing_table(kids[11])

    out.parent.mkdir(exist_ok=True)
    document.save(str(out))
    print("wrote", out)


if __name__ == "__main__":
    main()

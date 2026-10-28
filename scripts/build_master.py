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

BOX = "DCE6F2"                       # the light-blue details box
GAP = dict(title=29.2, spacer=27.0, name=6.0, pricing=24.0, pricing_after=19.6)
TWIP = 20
BOTTOM_MARGIN, FOOTER_DISTANCE = 1700, 420   # twips: room for the 3-column footer on every page


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


def shaded(runs: str, *, fill: str, left_ind: float, right_ind: float, before=0.0, after=0.0, tabs=(), line=None) -> str:
    """A paragraph with a filled background. The border in the same colour pads the text 4pt inside the fill, and
    consecutive paragraphs with the same fill join into one block (the details box)."""
    bd = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="{sp}" w:color="{fill}"/>' for s, sp in
                 (("top", 3), ("left", 4), ("bottom", 3), ("right", 4)))
    tab_xml = "".join(f'<w:tab w:val="{kind}" w:pos="{tw(t)}"/>' for kind, t in tabs)
    ln = f' w:line="{line}" w:lineRule="auto"' if line else ' w:line="240" w:lineRule="auto"'
    return (f'<w:p {NSDECL}><w:pPr><w:pBdr>{bd}</w:pBdr><w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>'
            f'{"<w:tabs>" + tab_xml + "</w:tabs>" if tabs else ""}<w:spacing w:before="{tw(before)}" w:after="{tw(after)}"{ln}/>'
            f'<w:ind w:left="{tw(left_ind)}" w:right="{tw(right_ind)}" w:firstLine="0"/></w:pPr>{runs}</w:p>')


PAD = 4.5                            # points of border + space around shaded text
TO_W, BOX_W = 4060, 4160             # widths in twips of the TO bar and of the details box (as in the purchase order)


def build_header(logo_run_xml: str, left_w: int, right_w: int):
    """Like the purchase order: title and logo on top; below, the TO block on the left and the details box
    (quote number, date, licence type and duration) on the right. Row 2's paragraphs are filled by docgen."""
    g = GAP
    spacer = lambda: para("", before=0, after=0, line=None).replace('w:line="240" w:lineRule="auto"', f'w:line="{round(g["spacer"] * 20)}" w:lineRule="exact"')
    title = [para(run("QUOTATION", sz=75, bold=True, color=NAVY), before=g["title"], left=1.0)]
    logo = [para(logo_run_xml, jc="right", right=2.7)]
    to_ind_r = (left_w - TO_W) / TWIP + PAD
    left = [
        spacer(),
        shaded(run("TO", sz=22, bold=True, color="FFFFFF"), fill=NAVY, left_ind=PAD, right_ind=to_ind_r, before=0, after=0),
        para(run("Dr. Adam", sz=22, bold=True), before=g["name"], left=0.0, right=(left_w - TO_W) / TWIP),
        para(run("Department of Biophysics, AIIMS, Ansari Nagar, New Delhi – 110029", sz=21),
             before=2.0, left=0.0, right=(left_w - TO_W) / TWIP, line=324),
        para(run("adam@example.com", sz=21, color=BLUE, underline=True), before=0, left=0.0, line=324),
    ]
    box_left = (right_w - BOX_W) / TWIP + PAD
    tab_pos = (right_w - BOX_W) / TWIP + (BOX_W / TWIP) - PAD     # right edge of the box's text
    rows = [("Quote Number", "MS-AB-174", True), ("Quote Date", "19/10/2026", True),
            ("License Type", "Academic License", False), ("License Duration", "3 Years", False)]
    right = [spacer()] + [
        shaded(run(label, sz=22, bold=b, color=NAVY) + run(tab=True) + run(value, sz=22, bold=b, color=NAVY),
               fill=BOX, left_ind=box_left, right_ind=PAD, before=1.8, after=1.8, tabs=(("right", tab_pos),))
        for label, value, b in rows]
    grid = f'<w:gridCol w:w="{left_w}"/><w:gridCol w:w="{right_w}"/>'
    tbl = (f'<w:tbl {NSDECL}><w:tblPr><w:tblW w:w="{left_w + right_w}" w:type="dxa"/><w:tblInd w:w="0" w:type="dxa"/>'
           f'<w:tblBorders><w:top w:val="nil"/><w:left w:val="nil"/><w:bottom w:val="nil"/><w:right w:val="nil"/>'
           f'<w:insideH w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders><w:tblLayout w:type="fixed"/>'
           f'<w:tblCellMar><w:left w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tblCellMar>'
           f'<w:tblLook w:val="0000"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>'
           f'<w:tr><w:trPr><w:cantSplit/></w:trPr>{cell(left_w, title)}{cell(right_w, logo)}</w:tr>'
           f'<w:tr><w:trPr><w:cantSplit/></w:trPr>{cell(left_w, left)}{cell(right_w, right)}</w:tr></w:tbl>')
    return el(tbl)


# ── footer: company details in three columns, like the purchase order ───────────────────────────────────
FOOT_COLS = [3700, 4050, 3431]
FOOT = dict(name="Molecular Solutions Software Pvt Ltd", web="molecularsolutions.co.in",
            addr=("110, West Village, Kodipalya, Bengaluru,", "Karnataka-560060, India"),
            email="sales@molecularsolutions.co.in", phone="+91-99000 90011")


def icon_png(path: Path, glyph: str) -> None:
    """A small navy symbol (envelope, telephone) from DejaVu Sans, drawn at 4x for a crisp print."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 200)
    img = Image.new("RGBA", (240, 240), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    box = d.textbbox((0, 0), glyph, font=font)
    d.text(((240 - (box[2] - box[0])) / 2 - box[0], (240 - (box[3] - box[1])) / 2 - box[1]), glyph, font=font, fill="#" + NAVY)
    img.save(path, "PNG")


def inline_icon(rid: str, n: int, size_emu=125000) -> str:
    return (f'<w:r {NSDECL}><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{size_emu}" cy="{size_emu}"/>'
            f'<wp:docPr id="{950 + n}" name="icon{n}"/><wp:cNvGraphicFramePr/><a:graphic><a:graphicData '
            f'uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="{950 + n}" name="icon{n}.png"/>'
            f'<pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{size_emu}" cy="{size_emu}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            f'</pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')


def build_footer(mail_rid: str, phone_rid: str) -> list:
    f = FOOT
    sm = dict(sz=18)
    line = lambda runs, **kw: para(runs, before=kw.get("before", 0), after=0, left=kw.get("left", 0), line=250)
    rule = (f'<w:p {NSDECL}><w:pPr><w:pBdr><w:top w:val="single" w:sz="8" w:space="0" w:color="{NAVY}"/></w:pBdr>'
            f'<w:spacing w:before="0" w:after="160" w:line="20" w:lineRule="exact"/></w:pPr></w:p>')
    col1 = [line(run(f["name"], bold=True, **sm)), line(run(f["web"], color=NAVY, **sm))]
    col2 = [line(run(t, **sm), left=9) for t in f["addr"]]
    col3 = [line(inline_icon(mail_rid, 1) + run("  " + f["email"], color=NAVY, **sm), left=9),
            line(inline_icon(phone_rid, 2) + run("  " + f["phone"], color=NAVY, **sm), left=9)]
    bar = f'<w:left w:val="single" w:sz="6" w:space="0" w:color="{NAVY}"/>'

    def fcell(w, paras, divider):
        return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/><w:tcBorders>{bar if divider else ""}</w:tcBorders>'
                f'<w:tcMar><w:top w:w="0" w:type="dxa"/><w:left w:w="0" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/>'
                f'<w:right w:w="0" w:type="dxa"/></w:tcMar><w:vAlign w:val="center"/></w:tcPr>{"".join(paras)}</w:tc>')
    tbl = (f'<w:tbl {NSDECL}><w:tblPr><w:tblW w:w="{sum(FOOT_COLS)}" w:type="dxa"/><w:tblInd w:w="0" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
           f'<w:tblCellMar><w:left w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tblCellMar><w:tblLook w:val="0000"/></w:tblPr>'
           f'<w:tblGrid>{"".join(f"<w:gridCol w:w=" + chr(34) + str(w) + chr(34) + "/>" for w in FOOT_COLS)}</w:tblGrid>'
           f'<w:tr><w:trPr><w:cantSplit/></w:trPr>{fcell(FOOT_COLS[0], col1, False)}{fcell(FOOT_COLS[1], col2, True)}'
           f'{fcell(FOOT_COLS[2], col3, True)}</w:tr></w:tbl>')
    end = f'<w:p {NSDECL}><w:pPr><w:spacing w:before="0" w:after="0" w:line="20" w:lineRule="exact"/></w:pPr></w:p>'
    return [el(rule), el(tbl), el(end)]


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


def add_footer(document) -> None:
    """One footer for every section (the template has several, all continuous), and a bottom margin that holds it."""
    icons = ROOT / "templates"
    icon_png(icons / "icon_mail.png", "\u2709")
    icon_png(icons / "icon_phone.png", "\u260e")
    first = document.sections[0]
    footer = first.footer
    footer.is_linked_to_previous = False
    mail_rid, _ = footer.part.get_or_add_image(str(icons / "icon_mail.png"))
    phone_rid, _ = footer.part.get_or_add_image(str(icons / "icon_phone.png"))
    ftr = footer._element
    for child in list(ftr):
        ftr.remove(child)
    for x in build_footer(mail_rid, phone_rid):
        ftr.append(x)
    ref = first._sectPr.find(w_("footerReference"))
    for sect in document.element.body.iter(w_("sectPr")):
        if sect.find(w_("footerReference")) is None:
            sect.insert(0, copy.deepcopy(ref))
        pg = sect.find(w_("pgMar"))
        pg.set(w_("bottom"), str(BOTTOM_MARGIN))
        pg.set(w_("footer"), str(FOOTER_DISTANCE))


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
    header = build_header(logo_xml, left_w=5600, right_w=5635)
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

    add_footer(document)

    out.parent.mkdir(exist_ok=True)
    document.save(str(out))
    print("wrote", out)


if __name__ == "__main__":
    main()

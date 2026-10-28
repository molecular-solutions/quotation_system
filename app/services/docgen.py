"""Fill Quotation_Template.docx with quote data.

The template file is the layout: nothing is redrawn. We locate its dynamic spots by
structure (and assert the expected anchor text, so a changed template fails loudly),
rewrite their text inside the existing runs, reuse/clone its own table rows, and flip
catalog rows between red (quoted) and black (not quoted).
"""
from __future__ import annotations

import copy
import io
from dataclasses import dataclass, field
from pathlib import Path

import docx
from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "master_quotation.docx"

RED = "EE0000"
RED_SET = {"EE0000", "ED0000", "BE0000"}
BLACK = "000000"


def _w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


# ── view model ────────────────────────────────────────────────────────────
@dataclass
class DocLine:
    label: str
    deployment: str = ""
    users: str = ""
    duration: str = ""
    unit_price: str = ""
    amount: str = ""
    complimentary: bool = False
    complimentary_note: str = "**Complimentary Renewed Annually**"
    description: str = ""  # printed under the product name, in smaller type


@dataclass
class DocQuote:
    number: str
    date: str
    customer_name: str
    customer_address: str
    customer_email: str
    license_type: str
    lines: list[DocLine]
    # (label, value) rows under the table; the last one is the grand total.
    summary: list[tuple[str, str]]
    validity_date: str
    payment_terms: str
    license_term: str
    license_duration: str = ""   # "3 Years", shown in the details box
    quoted_blocks: set[str] = field(default_factory=set)
    terms: list[dict] | None = None  # None / the default list = keep the template's own Terms & Conditions


# ── template map: catalog blocks ──────────────────────────────────────────
# key -> ("row", body index of table, row index[, columns]) | ("paras", first body idx, last body idx)
CATALOG_BLOCKS: dict[str, tuple] = {
    "commander": ("row", 15, 1),
    "seesar_csd": ("row", 15, 2),
    "xmolgen": ("row", 15, 3),
    "infinisee": ("row", 15, 4),
    "xfep": ("row", 15, 5),
    "admet": ("row", 15, 6),
    "gastroplus": ("row", 15, 7),
    "pkpd": ("row", 15, 8),
    "chemairs": ("row", 15, 9),
    "yasara": ("row", 18, 0),
    "eln": ("paras", 21, 25),
    "nanome": ("row", 27, 0),
    "formulation": ("row", 27, 1),
    "kvantify": ("row", 30, 0, (0, 1, 2, 3)),  # its segment label ("Kvantify") is red in the template too
}
CATALOG_COLS = (1, 2, 3)  # name, deployment, description (column 0 = segment stays black)

# anchors: body index -> text the template must start with there
ANCHORS = {
    1: "QUOTATION", 2: "PRICING", 3: None,
    6: "TERMS", 7: "• Validity", 9: "• Payment", 11: "• License Term", 13: "OUR SOFTWARE",
}
# The header (body[1]) is a 2 x 2 table. Row 1: title | logo. Row 2 (the paragraphs below; each starts with a spacer):
#   left  = TO bar, customer name, address, e-mail          right = the details box (label, tab, value per row)
HEADER_LEFT = {"to": 1, "name": 2, "address": 3, "email": 4}
HEADER_RIGHT = {"number": 1, "date": 2, "license": 3, "duration": 4}


# ── low-level helpers ─────────────────────────────────────────────────────
def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(_w("t")))


def _set_run_text(run, text: str) -> None:
    """Replace a run's text keeping its formatting; '\\n' becomes a line break."""
    for child in list(run):
        if child.tag != _w("rPr"):
            run.remove(child)
    for i, part in enumerate(text.split("\n")):
        if i:
            etree.SubElement(run, _w("br"))
        t = etree.SubElement(run, _w("t"))
        t.text = part
        t.set(f"{{{XML_NS}}}space", "preserve")


def _set_para_runs(p, mapping: dict[int, str]) -> None:
    runs = p.findall(_w("r"))
    for i, text in mapping.items():
        _set_run_text(runs[i], text)


def _set_cell_text(tc, text: str) -> None:
    """Replace a cell's text keeping the first run's formatting; '\\n' = new paragraph."""
    paras = tc.findall(_w("p"))
    lines = text.split("\n")
    while len(paras) < len(lines):
        clone = copy.deepcopy(paras[-1])
        paras[-1].addnext(clone)
        paras.append(clone)
    for extra in paras[len(lines):]:
        tc.remove(extra)
    paras = paras[: len(lines)]
    for p, line in zip(paras, lines):
        runs = p.findall(_w("r"))
        if not runs:
            runs = [etree.SubElement(p, _w("r"))]
        for extra in runs[1:]:
            p.remove(extra)
        _set_run_text(runs[0], line)


def _add_description(tc, description: str) -> None:
    """Put the product's description in the cell under its name: same face, 1.5 pt smaller."""
    description = (description or "").strip()
    if not description:
        return
    first = tc.findall(_w("p"))[-1]
    p = copy.deepcopy(first)
    for r in p.findall(_w("r"))[1:]:
        p.remove(r)
    run = p.find(_w("r"))
    _set_run_text(run, description)
    rpr = run.find(_w("rPr"))
    if rpr is not None:
        for tag in ("sz", "szCs"):
            sz = rpr.find(_w(tag))
            if sz is not None:
                sz.set(_w("val"), f"{max(float(sz.get(_w('val'))) - 3, 14):g}")
        for tag in ("b", "bCs"):
            for x in rpr.findall(_w(tag)):
                rpr.remove(x)
    ppr = p.find(_w("pPr"))
    sp = ppr.find(_w("spacing")) if ppr is not None else None
    if sp is not None:
        sp.set(_w("before"), "20")
    first.addnext(p)


def cell_text(tc) -> str:
    """Inverse of _set_cell_text (used to build the template's own sample)."""
    return "\n".join(_text(p) for p in tc.findall(_w("p")))


def _cells(tr):
    return tr.findall(_w("tc"))


def _check_anchors(body) -> None:
    for idx, prefix in ANCHORS.items():
        el = body[idx]
        if prefix is None:
            assert el.tag == _w("tbl"), f"template changed: body[{idx}] is not a table"
        else:
            got = _text(el).strip()
            assert got.startswith(prefix), f"template changed: body[{idx}] = {got[:30]!r}"


# ── catalog colouring ─────────────────────────────────────────────────────
def _block_runs(body, spec):
    if spec[0] == "row":
        tr = body[spec[1]].findall(_w("tr"))[spec[2]]
        cells = _cells(tr)
        for c in (spec[3] if len(spec) > 3 else CATALOG_COLS):
            yield from cells[c].iter(_w("r"))
    else:
        for i in range(spec[1], spec[2] + 1):
            yield from body[i].iter(_w("r"))


def _run_color(run) -> str | None:
    rpr = run.find(_w("rPr"))
    col = rpr.find(_w("color")) if rpr is not None else None
    return col.get(_w("val")).upper() if col is not None else None


def _set_run_color(run, value: str) -> None:
    rpr = run.find(_w("rPr"))
    if rpr is None:
        rpr = etree.SubElement(run, _w("rPr"))
        run.insert(0, rpr)
    col = rpr.find(_w("color"))
    if col is None:
        col = etree.SubElement(rpr, _w("color"))
    col.set(_w("val"), value)


def block_is_red(body, key: str) -> bool:
    return any(_run_color(r) in RED_SET for r in _block_runs(body, CATALOG_BLOCKS[key]) if _text(r).strip())


def _apply_catalog(body, quoted: set[str]) -> None:
    unknown = quoted - CATALOG_BLOCKS.keys()
    assert not unknown, f"unknown catalog blocks: {unknown}"
    for key, spec in CATALOG_BLOCKS.items():
        red_now = block_is_red(body, key)
        if key in quoted and not red_now:
            for r in _block_runs(body, spec):
                if _text(r).strip() and _run_color(r) in (None, BLACK):
                    _set_run_color(r, RED)
        elif key not in quoted and red_now:
            for r in _block_runs(body, spec):
                if _run_color(r) in RED_SET:
                    _set_run_color(r, BLACK)


# ── terms & conditions ────────────────────────────────────────────────────
TERMS_FIRST, TERMS_LAST = 7, 12   # body indexes of the template's six bullet paragraphs


_RPR_ORDER = ["rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline", "shadow",
              "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing", "w", "kern",
              "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs",
              "em", "lang", "eastAsianLayout", "specVanish", "oMath"]


def _rpr_set(rpr, tag: str, **attrs):
    """Add or replace one run-property element at its place in Word's required order."""
    for x in rpr.findall(_w(tag)):
        rpr.remove(x)
    el = etree.Element(_w(tag))
    for k, v in attrs.items():
        el.set(_w(k), v)
    later = _RPR_ORDER[_RPR_ORDER.index(tag) + 1:]
    nxt = next((c for c in rpr if etree.QName(c).localname in later), None)
    if nxt is not None:
        nxt.addprevious(el)
    else:
        rpr.append(el)


def _terms_run(base_rpr, seg: dict):
    r = etree.Element(_w("r"))
    rpr = copy.deepcopy(base_rpr)
    r.append(rpr)
    for tag, on in (("b", seg.get("bold")), ("bCs", seg.get("bold")), ("i", seg.get("italic")), ("iCs", seg.get("italic"))):
        for x in rpr.findall(_w(tag)):
            rpr.remove(x)
        if on:
            _rpr_set(rpr, tag)
    for x in rpr.findall(_w("highlight")):
        rpr.remove(x)
    if seg.get("highlight"):
        _rpr_set(rpr, "highlight", val="yellow")
    if seg.get("link"):
        _rpr_set(rpr, "color", val="0000FF")
    etree.SubElement(r, _w("t")).text = seg["text"]
    r[-1].set(f"{{{XML_NS}}}space", "preserve")
    return r


def _apply_terms(kids, q: DocQuote) -> bool:
    """Replace the six template bullets by the configured points. Returns False when the default is in use
    (then the template's own paragraphs are kept and filled in, which keeps the layout identical)."""
    from app.services import terms as terms_svc
    if terms_svc.is_default(q.terms):
        return False
    protos = {"first": kids[7], "bullet": kids[8], "indent": kids[10]}
    bullet_rpr = kids[8].findall(_w("r"))[0].find(_w("rPr"))  # the "• " glyph has its own (symbols) font
    base_rpr = kids[8].findall(_w("r"))[1].find(_w("rPr"))    # body text: Calibri, size, colour
    values = {"validity_date": q.validity_date, "payment_terms": q.payment_terms, "license_term": q.license_term}
    new = []
    for n, item in enumerate(terms_svc.clean(q.terms)):
        proto = protos["first"] if n == 0 and item["style"] == "bullet" else protos[item["style"]]
        p = etree.Element(_w("p"))
        p.append(copy.deepcopy(proto.find(_w("pPr"))))
        p.append(_terms_run(bullet_rpr, {"text": "• "}))
        for seg in terms_svc.parse(item["text"], values):
            p.append(_terms_run(base_rpr, seg))
        new.append(p)
    for el in new:
        kids[TERMS_FIRST].addprevious(el)
    for i in range(TERMS_FIRST, TERMS_LAST + 1):
        kids[i].getparent().remove(kids[i])
    return True


# ── header ────────────────────────────────────────────────────────────────
def _header_paras(header_tbl) -> dict:
    left, right = header_tbl.findall(_w("tr"))[1].findall(_w("tc"))
    lp, rp = left.findall(_w("p")), right.findall(_w("p"))
    return {**{k: lp[i] for k, i in HEADER_LEFT.items()}, **{k: rp[i] for k, i in HEADER_RIGHT.items()}}


def _fill_header(header_tbl, q: DocQuote) -> None:
    p = _header_paras(header_tbl)
    _set_para_runs(p["number"], {2: q.number})
    _set_para_runs(p["date"], {2: q.date})
    _set_para_runs(p["license"], {2: q.license_type})
    _set_para_runs(p["duration"], {2: q.license_duration})
    _set_para_runs(p["name"], {0: q.customer_name})
    _set_para_runs(p["address"], {0: q.customer_address})
    if q.customer_email.strip():
        _set_para_runs(p["email"], {0: q.customer_email})
    else:
        p["email"].getparent().remove(p["email"])
    if not q.customer_address.strip():
        p["address"].getparent().remove(p["address"])


# ── pricing table ─────────────────────────────────────────────────────────
def _fill_pricing_table(tbl, q: DocQuote, reuse_template_rows: bool) -> None:
    rows = tbl.findall(_w("tr"))
    header, normal, comp = rows[0], rows[1:11], rows[11]
    sub_first, sub_mid, sub_last = rows[12], rows[13], rows[14]
    # Every line is cloned from one clean prototype so rows look uniform (the template's own
    # ten sample rows differ by a few twips and row 6 centres its USERS cell).
    proto_normal = copy.deepcopy(normal[2])
    proto_comp = copy.deepcopy(comp)

    spare_normal, spare_comp = (list(normal), [comp]) if reuse_template_rows else ([], [])
    placed: list = []
    for n, line in enumerate(q.lines, start=1):
        if line.complimentary:
            tr = spare_comp.pop(0) if spare_comp else copy.deepcopy(proto_comp)
            c = _cells(tr)
            for cell, val in zip(c[:4], (str(n), line.label, line.deployment, line.users)):
                _set_cell_text(cell, val)
            _add_description(c[1], line.description)
            _set_cell_text(c[4], line.complimentary_note)
        else:
            tr = spare_normal.pop(0) if spare_normal else copy.deepcopy(proto_normal)
            vals = (str(n), line.label, line.deployment, line.users,
                    line.duration, line.unit_price, line.amount)
            for cell, val in zip(_cells(tr), vals):
                _set_cell_text(cell, val)
            _add_description(_cells(tr)[1], line.description)
        placed.append(tr)

    for tr in rows[1:11] + [comp]:
        if tr.getparent() is not None:
            tbl.remove(tr)
    anchor = header
    for tr in placed:
        anchor.addnext(tr)
        anchor = tr

    # summary rows: first/last use the template's own first/last summary rows,
    # middle ones the template's middle row; each original row is reused once.
    for tr in (sub_first, sub_mid, sub_last):
        tbl.remove(tr)
    protos = {"first": sub_first, "mid": sub_mid, "last": sub_last}
    unused = set(protos)
    n = len(q.summary)
    for i, (label, value) in enumerate(q.summary):
        kind = "last" if i == n - 1 else "first" if i == 0 else "mid"
        tr = protos[kind] if kind in unused else copy.deepcopy(protos[kind])
        unused.discard(kind)
        c = _cells(tr)
        _set_cell_text(c[0], label)
        _set_cell_text(c[1], value)
        anchor.addnext(tr)
        anchor = tr


W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"


def _strip_duplicate_ids(body) -> None:
    """Cloned rows/paragraphs repeat w14:paraId / w14:textId. Both are optional, and Word is
    stricter about duplicates than LibreOffice, so drop them everywhere."""
    for el in body.iter():
        for attr in (f"{{{W14_NS}}}paraId", f"{{{W14_NS}}}textId"):
            if attr in el.attrib:
                del el.attrib[attr]


# ── public API ────────────────────────────────────────────────────────────
def render_quote(q: DocQuote, template_path: Path = TEMPLATE_PATH, *,
                 reuse_template_rows: bool = False) -> bytes:
    """Return the finished quotation as .docx bytes.

    reuse_template_rows=True fills the template's own sample rows in place; only the fidelity
    gate uses it, to prove the fill mechanism is lossless."""
    document = docx.Document(str(template_path))
    body = document.element.body
    kids = list(body)  # fixed indexes; later edits don't shift this list
    _check_anchors(kids)

    _fill_header(kids[1], q)
    _fill_pricing_table(kids[3], q, reuse_template_rows)

    if not _apply_terms(kids, q):
        _set_para_runs(kids[7], {2: f"{q.validity_date} "})
        _set_para_runs(kids[9], {2: q.payment_terms})
        _set_para_runs(kids[11], {2: f"{q.license_term} "})

    _apply_catalog(kids, q.quoted_blocks)
    _strip_duplicate_ids(body)

    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def template_sample(template_path: Path = TEMPLATE_PATH) -> DocQuote:
    """A DocQuote that reproduces the template's own sample text (for the fidelity gate)."""
    document = docx.Document(str(template_path))
    kids = list(document.element.body)
    hp = _header_paras(kids[1])
    run_text = lambda p, i: _text(p.findall(_w("r"))[i])
    runs = lambda i: [_text(r) for r in kids[i].findall(_w("r"))]
    rows = kids[3].findall(_w("tr"))
    lines = []
    for tr in rows[1:12]:
        c = [cell_text(x) for x in _cells(tr)]
        if len(c) == 5:  # complimentary row (last cell spans 3 columns)
            lines.append(DocLine(c[1], c[2], c[3], complimentary=True, complimentary_note=c[4]))
        else:
            lines.append(DocLine(*c[1:7]))
    summary = [tuple(cell_text(x) for x in _cells(tr)) for tr in rows[12:15]]
    return DocQuote(
        number=run_text(hp["number"], 2),
        date=run_text(hp["date"], 2),
        customer_name=run_text(hp["name"], 0),
        customer_address=run_text(hp["address"], 0),
        customer_email=run_text(hp["email"], 0),
        license_type=run_text(hp["license"], 2),
        license_duration=run_text(hp["duration"], 2),
        lines=lines,
        summary=summary,
        validity_date=runs(7)[2].rstrip(),
        payment_terms=runs(9)[2],
        license_term=runs(11)[2].rstrip(),
        quoted_blocks={k for k in CATALOG_BLOCKS if block_is_red(kids, k)},
    )

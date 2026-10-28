from __future__ import annotations

import re
import threading
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import config
from app.auth import current_user
from app.database import get_db
from app.models import GST_MODES, Customer, Quote, User
from app.services import mail
from app.services import quotes as svc
from app.services import settings as app_settings
from app.services.docgen import render_quote
from app.services.pdf import PdfUnavailable, docx_to_pdf
from app.services.pricing import LineIn, compute, parse_users
from app.services.quotes import LineInput, QuoteError, QuoteInput

router = APIRouter(prefix="/api/quotes", dependencies=[Depends(current_user)])
_pdf_slots = threading.BoundedSemaphore(2)  # LibreOffice is heavy; at most 2 conversions at once
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def f(x):
    return float(x) if isinstance(x, Decimal) else x


def quote_json(db: Session, q: Quote, with_lines: bool = True) -> dict:
    base = {"id": q.id, "number": q.number, "revision": q.revision, "date": q.date.isoformat(),
            "valid_until": q.valid_until.isoformat(), "status": q.status, "tier": q.tier,
            "customer_name": q.customer_name, "total": f(q.total), "prepared_by": q.prepared_by,
            "created_by": q.created_by.name if q.created_by else "", "created_by_id": q.created_by_id,
            "updated_at": q.updated_at.isoformat(sep=" ", timespec="minutes"),
            "emailed_at": q.emailed_at.isoformat(sep=" ", timespec="minutes") if q.emailed_at else None}
    if not with_lines:
        return base
    cust = db.get(Customer, q.customer_id) if q.customer_id else None
    customer = ({"id": cust.id, "name": cust.name, "department": cust.department, "institution": cust.institution,
                 "address": cust.address, "email": cust.email, "phone": cust.phone} if cust else
                {"id": None, "name": q.customer_name, "department": "", "institution": "",
                 "address": q.customer_address, "email": q.customer_email, "phone": ""})
    t = compute([LineIn(l.unit_price, l.users_label, l.years, l.complimentary, l.amount) for l in q.lines],
                q.discount_pct, q.gst_mode, q.gst_pct)
    return base | {
        "customer": customer, "discount_pct": f(q.discount_pct), "gst_mode": q.gst_mode, "gst_pct": f(q.gst_pct),
        "payment_terms": q.payment_terms,
        "lines": [{"product_id": l.product_id, "label": l.label, "deployment": l.deployment,
                   "users_label": l.users_label, "years": l.years, "unit_price": f(l.unit_price),
                   "amount": f(l.amount), "amount_pinned": svc.amount_pinned(l), "complimentary": l.complimentary,
                   "description": l.description} for l in q.lines],
        "totals": totals_json(t),
    }


def totals_json(t) -> dict:
    return {"list_total": f(t.list_total), "discount_amount": f(t.discount_amount), "subtotal": f(t.subtotal),
            "tax_rows": [{"label": a, "amount": f(b)} for a, b in t.tax_rows], "total": f(t.total)}


def get_quote(db: Session, quote_id: int, user: User) -> Quote:
    """A user sees only their own quotations; an administrator sees all. Others' quotes look like they don't exist."""
    q = db.get(Quote, quote_id)
    if q is None or (user.role != "admin" and q.created_by_id != user.id):
        raise HTTPException(404, "quote not found")
    return q


def stamp_owner(db: Session, q: Quote, user: User, owner: User | None = None) -> None:
    """Record who made the quote and default 'prepared by' to their name."""
    q.created_by_id = (owner or user).id
    q.prepared_by = q.prepared_by or (owner or user).name
    db.commit()


def guarded(fn, *a):
    try:
        return fn(*a)
    except QuoteError as e:
        raise HTTPException(422, {"message": str(e)})


@router.get("")
def list_quotes(q: str = "", status: str = "", db: Session = Depends(get_db), user: User = Depends(current_user)):
    """The signed-in user's own quotations (administrators see everyone's in the admin panel)."""
    stmt = select(Quote).where(Quote.created_by_id == user.id).order_by(Quote.id.desc()).limit(300)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Quote.number.ilike(like), Quote.customer_name.ilike(like),
                              Quote.customer_address.ilike(like)))
    if status:
        stmt = stmt.where(Quote.status == status)
    return [quote_json(db, x, with_lines=False) for x in db.scalars(stmt)]


class CalcIn(BaseModel):
    tier: str = "Academic"
    discount_pct: Decimal = Decimal(0)
    gst_mode: str = "IGST"
    gst_pct: Decimal | None = None
    lines: list[LineInput] = []


@router.post("/calc")
def calc(body: CalcIn, db: Session = Depends(get_db)):
    """Live summary for the builder. Tolerant: a bad line is flagged, the rest still adds up."""
    if body.gst_mode not in GST_MODES or not (0 <= body.discount_pct <= 100):
        raise HTTPException(422, {"message": "invalid discount or GST mode"})
    gst_pct = body.gst_pct if body.gst_pct is not None else Decimal(app_settings.get(db, "gst_pct"))
    lines_out, ins = [], []
    for ln in body.lines:
        err, r = None, None
        try:
            r = svc.resolve_lines(db, [ln], body.tier)[0]
        except QuoteError as e:
            err = str(e).split(": ", 1)[-1]
        if r is None:
            lines_out.append({"error": err, "amount": 0, "unit_price": 0})
            ins.append(LineIn(Decimal(0), "1", ln.years))
            continue
        ins.append(LineIn(r.unit_price, r.users_label, r.years, r.complimentary, r.amount))
        lines_out.append({"label": r.label, "deployment": r.deployment, "users_label": r.users_label,
                          "years": r.years, "unit_price": f(r.unit_price), "complimentary": r.complimentary,
                          "qty": f(parse_users(r.users_label)), "error": None})
    t = compute(ins, body.discount_pct, body.gst_mode, gst_pct)
    for lo, to in zip(lines_out, t.lines):
        lo["amount"] = f(to.amount)
    return {"lines": lines_out, "totals": totals_json(t)}


def _render_files(q: Quote, fmt: str) -> tuple[bytes, str, str]:
    doc = render_quote(svc.to_doc_quote(q))
    if fmt == "docx":
        return doc, DOCX, "docx"
    try:
        with _pdf_slots:
            return docx_to_pdf(doc), "application/pdf", "pdf"
    except PdfUnavailable as e:
        raise HTTPException(503, {"message": f"PDF is unavailable on this server ({e}). Download the Word file instead."})


def _filename(q: Quote, ext: str) -> str:
    who = re.sub(r"[^A-Za-z0-9]+", "_", q.customer_name).strip("_")[:40] or "customer"
    return f"{q.number}_{who}_{q.date.isoformat()}.{ext}"


def _respond(q: Quote, fmt: str, inline: bool = False) -> Response:
    if fmt not in ("docx", "pdf"):
        raise HTTPException(404, "format must be docx or pdf")
    data, mime, ext = _render_files(q, fmt)
    disp = "inline" if inline else "attachment"
    return Response(data, media_type=mime, headers={"Content-Disposition": f'{disp}; filename="{_filename(q, ext)}"'})


@router.post("/preview.{fmt}")
def preview(fmt: str, body: QuoteInput, db: Session = Depends(get_db)):
    """Render an unsaved quote. Nothing is stored and no quote number is consumed."""
    import datetime as dt
    q = Quote(number="DRAFT", seq=0, date=body.date or dt.date.today(), valid_until=dt.date.today(),
              customer_name="x")
    db.add(q)
    try:
        guarded(svc._fill, db, q, body, False)
        if body.tier == "Commercial" and (body.number or "").strip():
            q.number = guarded(svc.clean_number, body.number)
        q.terms = app_settings.terms_snapshot(db)
        return _respond(q, fmt, inline=True)
    finally:
        db.rollback()


@router.post("", status_code=201)
def create(body: QuoteInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    try:
        q = guarded(svc.create_quote, db, body)
    except HTTPException:
        db.rollback()
        raise
    stamp_owner(db, q, user)
    return quote_json(db, q)


@router.get("/{quote_id}")
def read(quote_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return quote_json(db, get_quote(db, quote_id, user))


@router.put("/{quote_id}")
def update(quote_id: int, body: QuoteInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = get_quote(db, quote_id, user)
    try:
        guarded(svc.update_quote, db, q, body)
    except HTTPException:
        db.rollback()
        raise
    if not q.prepared_by:
        q.prepared_by = q.created_by.name if q.created_by else user.name
        db.commit()
    return quote_json(db, q)


class StatusIn(BaseModel):
    status: str


@router.patch("/{quote_id}/status")
def status(quote_id: int, body: StatusIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return quote_json(db, guarded(svc.set_status, db, get_quote(db, quote_id, user), body.status), with_lines=False)


@router.post("/{quote_id}/revise", status_code=201)
def revise(quote_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    old = get_quote(db, quote_id, user)
    new = svc.revise(db, old)
    stamp_owner(db, new, user, owner=old.created_by)  # a revision stays with the quote's owner
    return quote_json(db, new)


@router.post("/{quote_id}/duplicate", status_code=201)
def duplicate(quote_id: int, body: dict | None = None, db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    """Copy a quote. A Commercial copy needs its number typed: {"number": "MS-CA-26-059"}."""
    new = guarded(svc.duplicate, db, get_quote(db, quote_id, user), (body or {}).get("number"))
    new.prepared_by = ""
    stamp_owner(db, new, user)  # a copy belongs to whoever made it
    return quote_json(db, new)


@router.post("/{quote_id}/email")
def email_quote(quote_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """E-mail the saved quotation (PDF) to the recipients saved in Settings."""
    import datetime as dt
    q = get_quote(db, quote_id, user)
    to = mail.get_recipients(db)
    if not mail.configured():
        raise HTTPException(503, {"message": "E-mail is not set up on this server yet. Ask the administrator."})
    if not to:
        raise HTTPException(422, {"message": "No e-mail recipients are saved. An administrator can add them in Settings."})
    pdf, _, _ = _render_files(q, "pdf")
    tax_note = "inc. GST"
    try:
        mail.send_quote(q.number, q.customer_name, q.date.strftime("%d/%m/%Y"), q.valid_until.strftime("%d/%m/%Y"),
                        f"INR {q.total:,.2f}", tax_note, pdf, _filename(q, "pdf"), to, user.name)
    except mail.MailError as e:
        raise HTTPException(502, {"message": str(e)})
    q.emailed_at = dt.datetime.now()
    db.commit()
    return {"sent_to": to, "emailed_at": q.emailed_at.isoformat(sep=" ", timespec="minutes")}


@router.delete("/{quote_id}")
def delete(quote_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = get_quote(db, quote_id, user)
    if q.status != "Draft":
        raise HTTPException(409, "only Draft quotes can be deleted")
    db.delete(q)
    db.commit()
    return {"ok": True}


@router.get("/{quote_id}/{fmt}")
def download(quote_id: int, fmt: str, inline: bool = False, db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    return _respond(get_quote(db, quote_id, user), fmt, inline)

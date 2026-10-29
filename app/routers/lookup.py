"""Read-mostly endpoints the quote builder needs: products, customers, packages, settings."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import current_user, require_admin
from app.database import get_db
from app.models import GST_MODES, TIERS, Customer, Package, Product
from app.services import mail
from app.services import settings as app_settings
from app.services.tables import CATALOG_LABELS

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])


def _prices(p: Product) -> dict:
    """{"Academic": {"1": 42000.0, "2": 78000.0, ...}, "Commercial": {...}}: annual price by number of users."""
    out: dict[str, dict[str, float]] = {}
    for pr in sorted(p.prices, key=lambda x: x.users):
        out.setdefault(pr.tier, {})[str(pr.users)] = float(pr.annual_price)
    return out


def _prices3(p: Product) -> dict:
    """The price of a 3-year quote, same shape as _prices."""
    out: dict[str, dict[str, float]] = {}
    for pr in sorted(p.prices, key=lambda x: x.users):
        if pr.three_year_price is not None:
            out.setdefault(pr.tier, {})[str(pr.users)] = float(pr.three_year_price)
    return out


def product_json(p: Product) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description, "label": p.line_label or p.name, "vendor": p.vendor,
            "segment": p.segment, "deployment": p.deployment, "default_users": p.default_users,
            "price_basis": p.price_basis, "active": p.active,
            "prices": _prices(p), "prices3": _prices3(p)}


@router.get("/config")
def config_():
    return {"tiers": TIERS, "gst_modes": GST_MODES}


@router.get("/products")
def products(q: str = "", include_inactive: bool = False, db: Session = Depends(get_db)):
    stmt = select(Product).order_by(Product.name)
    if not include_inactive:
        stmt = stmt.where(Product.active.is_(True))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.vendor.ilike(like),
                              Product.line_label.ilike(like)))
    return [product_json(p) for p in db.scalars(stmt)]


@router.get("/customers")
def customers(q: str = "", db: Session = Depends(get_db)):
    stmt = select(Customer).order_by(Customer.name).limit(50)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Customer.name.ilike(like), Customer.institution.ilike(like),
                              Customer.department.ilike(like), Customer.email.ilike(like)))
    return [{"id": c.id, "name": c.name, "department": c.department, "institution": c.institution,
             "address": c.address, "email": c.email, "phone": c.phone, "tier": c.tier} for c in db.scalars(stmt)]


@router.get("/packages")
def packages(db: Session = Depends(get_db)):
    return [{"id": pk.id, "name": pk.name,
             "items": [{"product_id": i.product_id, "users": i.users, "years": i.years} for i in pk.items
                       if i.product.active]}
            for pk in db.scalars(select(Package).order_by(Package.name))]


@router.get("/blocks")
def blocks():
    return [{"key": k, "label": v} for k, v in CATALOG_LABELS.items()]


def _next_seq(db: Session) -> int:
    from app.models import Counter
    row = db.get(Counter, "quote_seq")
    return row.value + 1 if row else int(app_settings.get(db, "seq_next"))


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    values = app_settings.get_all(db)
    values["seq_next"] = str(_next_seq(db))  # live value, not the stale stored default
    return {"values": values, "labels": app_settings.LABELS}


@router.put("/settings", dependencies=[Depends(require_admin)])
def put_settings(values: dict[str, str], db: Session = Depends(get_db)):
    from sqlalchemy import func
    from app.models import Counter, Quote
    try:
        values = dict(values)
        if "number_format" in values and "{seq}" not in values["number_format"]:
            raise ValueError("number format must contain {seq}")
        if "seq_next" in values:
            raw = values.pop("seq_next")
            if not raw.isdigit():
                raise ValueError("next sequence number must be a whole number")
            new = int(raw)
            if new != _next_seq(db):
                used = db.scalar(select(func.max(Quote.seq))) or 0
                if new <= used:
                    raise ValueError(f"next number must be higher than the last one used ({used})")
                row = db.get(Counter, "quote_seq")
                if row is None:
                    db.add(Counter(name="quote_seq", value=new - 1))
                else:
                    row.value = new - 1
        app_settings.put_many(db, values)
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    return {"ok": True}


@router.get("/mail")
def mail_status(db: Session = Depends(get_db)):
    """Whether the Email button can work, and who it will send to."""
    return {"configured": mail.configured(), "recipients": mail.get_recipients(db)}


@router.put("/mail/recipients", dependencies=[Depends(require_admin)])
def put_mail_recipients(body: dict, db: Session = Depends(get_db)):
    try:
        items = mail.put_recipients(db, body.get("recipients"))
        db.commit()
    except mail.MailError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    return {"recipients": items}


@router.post("/mail/test")
def mail_test(user=Depends(require_admin)):
    """Send a one-line test message to the signed-in administrator."""
    try:
        mail.send_test(user.email)
    except mail.MailError as e:
        raise HTTPException(502, str(e))
    return {"sent_to": user.email}


@router.get("/settings/terms")
def get_terms(db: Session = Depends(get_db)):
    from app.services import terms as terms_svc
    return {"items": app_settings.get_terms(db), "default": terms_svc.DEFAULT_TERMS,
            "tokens": list(terms_svc.TOKENS), "styles": list(terms_svc.STYLES)}


@router.put("/settings/terms", dependencies=[Depends(require_admin)])
def put_terms(body: dict, db: Session = Depends(get_db)):
    from app.services import terms as terms_svc
    try:
        items = app_settings.put_terms(db, body.get("items"))
        db.commit()
    except terms_svc.TermsError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    return {"items": items}

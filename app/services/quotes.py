"""Create / update / revise quotes, freeze snapshots, and map a quote to the document model."""
from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (Counter, Customer, GST_MODES, STATUSES, TIERS, Product, Quote, QuoteLine,
                        format_address)
from app.services import settings as app_settings
from app.services.docgen import DocLine, DocQuote
from app.services.pricing import LineIn, Totals, compute, rupees, unit_from_total


# ── request models ────────────────────────────────────────────────────────
class CustomerIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    department: str = ""
    institution: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""


class LineInput(BaseModel):
    product_id: int | None = None
    label: str = ""             # blank -> product's quote label / name
    deployment: str = ""        # blank -> product default
    users_label: str = ""       # blank -> product default users
    years: int = Field(default=3, ge=1, le=20)
    unit_price: Decimal | None = Field(default=None, ge=0)  # blank -> product price for the tier
    amount: Decimal | None = Field(default=None, ge=0)      # a typed line total; the unit price is worked out from it
    complimentary: bool | None = None
    description: str | None = None  # printed under the name; None -> the product's description

    @field_validator("users_label", "label", "deployment")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class QuoteInput(BaseModel):
    customer: CustomerIn
    tier: str = "Academic"
    date: dt.date | None = None
    valid_until: dt.date | None = None
    discount_pct: Decimal = Field(default=Decimal(0), ge=0, le=100)
    gst_mode: str = "IGST"
    gst_pct: Decimal | None = None
    payment_terms: str = ""
    prepared_by: str = ""
    number: str | None = None   # Commercial quotes only: the full number, typed by the user ("MS-" + the rest)
    lines: list[LineInput] = Field(default_factory=list)

    @field_validator("tier")
    @classmethod
    def _tier(cls, v):
        if v not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}")
        return v

    @field_validator("gst_mode")
    @classmethod
    def _gst(cls, v):
        if v not in GST_MODES:
            raise ValueError(f"gst_mode must be one of {GST_MODES}")
        return v


class QuoteError(ValueError):
    pass


# ── resolving input lines against the catalog ────────────────────────────
class ResolvedLine(BaseModel):
    product_id: int | None
    label: str
    deployment: str
    users_label: str
    years: int
    unit_price: Decimal
    amount: Decimal | None = None   # set when the user typed the line total
    complimentary: bool
    block_keys: list[str]
    description: str = ""


def resolve_lines(db: Session, lines: list[LineInput], tier: str) -> list[ResolvedLine]:
    out = []
    for n, ln in enumerate(lines, start=1):
        prod = db.get(Product, ln.product_id) if ln.product_id else None
        if ln.product_id and prod is None:
            raise QuoteError(f"line {n}: product no longer exists")
        if prod is None and not ln.label:
            raise QuoteError(f"line {n}: choose a product or type a name")
        comp = ln.complimentary if ln.complimentary is not None else bool(prod and prod.price_basis == "complimentary")
        price = ln.unit_price
        amount = None if comp else ln.amount
        if amount is not None:
            try:
                price = unit_from_total(amount, ln.years)
            except ValueError as e:
                raise QuoteError(f"line {n}: {e}")
        if price is None and prod is not None:
            users = ln.users_label or prod.default_users
            total = prod.term_price(tier, users, ln.years)       # the price of the whole term: 1 year, or the 3-year price
            if total is None and not comp:
                what = f"for {users} user{'' if str(users).strip() == '1' else 's'} " if prod.price_basis != "per_license_year" else ""
                term = "" if ln.years == 1 else f"for {ln.years} years "
                raise QuoteError(f"line {n}: {prod.name} has no {tier} price {what}{term}– add it in Prices, or type the price")
            if total is not None and ln.years != 1 and not comp:
                amount = total                                    # a multi-year price is the total, not annual x years
                price = unit_from_total(total, ln.years)
            else:
                price = total
        out.append(ResolvedLine(
            product_id=prod.id if prod else None,
            label=ln.label or (prod.line_label or prod.name if prod else ""),
            deployment=ln.deployment or (prod.deployment if prod else ""),
            users_label=ln.users_label or (prod.default_users if prod else "1"),
            years=ln.years,
            unit_price=Decimal(price or 0),
            amount=amount,
            complimentary=comp,
            block_keys=prod.block_keys if prod else [],
            description=(ln.description if ln.description is not None else (prod.description if prod else "")).strip(),
        ))
    return out


def totals_for(resolved: list[ResolvedLine], discount_pct, gst_mode, gst_pct) -> Totals:
    return compute([LineIn(r.unit_price, r.users_label, r.years, r.complimentary, r.amount) for r in resolved],
                   discount_pct, gst_mode, gst_pct)


# ── numbering ─────────────────────────────────────────────────────────────
def ensure_counter(db: Session) -> None:
    """Create the global counter row if missing. Safe if several requests race to do it."""
    first = int(app_settings.get_all(db)["seq_next"]) - 1
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    db.execute(insert(Counter).values(name="quote_seq", value=first).on_conflict_do_nothing(index_elements=["name"]))


NUMBER_PREFIX = "MS-"
_REVISION = re.compile(r"-R\d+$")
_SUFFIX = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)*$")


def base_number(number: str) -> str:
    return _REVISION.sub("", number)


def clean_number(raw: str | None) -> str:
    """A typed Commercial number, e.g. 'MS-CA-26-058'. 'MS-' is fixed; the rest is the user's."""
    n = (raw or "").strip().upper()
    suffix = n[len(NUMBER_PREFIX):] if n.startswith(NUMBER_PREFIX) else n
    if not suffix:
        raise QuoteError("enter the quote number, e.g. MS-CA-26-058")
    if not _SUFFIX.match(suffix) or len(suffix) > 36:
        raise QuoteError("the quote number can only use letters, digits and hyphens, e.g. MS-CA-26-058")
    if _REVISION.search(suffix):
        raise QuoteError("a quote number can't end in -R and a digit (that is how revisions are named)")
    return NUMBER_PREFIX + suffix


def manual_number(db: Session, raw: str | None, current_id: int | None = None) -> str:
    n = clean_number(raw)
    other = db.scalar(select(Quote.id).where(Quote.number == n))
    if other is not None and other != current_id:
        raise QuoteError(f"{n} is already used by another quotation")
    return n


def next_number(db: Session, tier: str) -> tuple[str, int]:
    """Next number from the one global sequence, allocated inside the caller's transaction.

    The UPDATE takes the database's write lock on the counter row, so a second request waits until the first commits
    and then gets the following number; if the quote fails to save, the rollback gives the number back (no gaps)."""
    cfg = app_settings.get_all(db)
    ensure_counter(db)
    db.execute(update(Counter).where(Counter.name == "quote_seq").values(value=Counter.value + 1))
    seq = db.execute(select(Counter.value).where(Counter.name == "quote_seq")).scalar_one()
    number = cfg["number_format"].format(code=cfg["code_Academic"], seq=seq)
    return number, seq


# ── persistence ───────────────────────────────────────────────────────────
def _save_customer(db: Session, c: CustomerIn, tier: str) -> Customer:
    cust = db.get(Customer, c.id) if c.id else None
    if cust is None:
        cust = Customer(name=c.name)
        db.add(cust)
    for f in ("name", "department", "institution", "address", "email", "phone"):
        setattr(cust, f, getattr(c, f))
    cust.tier = tier
    db.flush()
    return cust


def _fill(db: Session, q: Quote, data: QuoteInput, save_customer: bool = True) -> None:
    cfg = app_settings.get_all(db)
    gst_pct = data.gst_pct if data.gst_pct is not None else Decimal(cfg["gst_pct"])
    resolved = resolve_lines(db, data.lines, data.tier)
    if not resolved:
        raise QuoteError("add at least one product")
    t = totals_for(resolved, data.discount_pct, data.gst_mode, gst_pct)

    if save_customer:
        cust = _save_customer(db, data.customer, data.tier)
        q.customer_id, q.customer_name = cust.id, cust.name
        q.customer_address, q.customer_email = cust.address_block, cust.email
    else:  # copies keep the frozen text and never touch the saved customer record
        c = data.customer
        block = format_address(c.department, c.institution, c.address)
        q.customer_id, q.customer_name, q.customer_address, q.customer_email = c.id, c.name, block, c.email
    q.tier = data.tier
    q.date = data.date or q.date or dt.date.today()
    q.valid_until = data.valid_until or (q.date + dt.timedelta(days=int(cfg["validity_days"])))
    q.discount_pct, q.gst_mode, q.gst_pct = data.discount_pct, data.gst_mode, gst_pct
    q.payment_terms = data.payment_terms.strip() or cfg["payment_terms"]
    q.prepared_by = data.prepared_by.strip()
    q.updated_at = dt.datetime.now()
    q.list_total, q.discount_amount, q.subtotal = t.list_total, t.discount_amount, t.subtotal
    q.tax_amount, q.total = t.tax_amount, t.total
    q.lines[:] = [
        QuoteLine(sort=i, product_id=r.product_id, label=r.label, deployment=r.deployment,
                  users_label=r.users_label, qty=o.qty, years=r.years, unit_price=r.unit_price,
                  amount=o.amount, complimentary=r.complimentary, block_keys=",".join(r.block_keys),
                  description=r.description)
        for i, (r, o) in enumerate(zip(resolved, t.lines))
    ]


def create_quote(db: Session, data: QuoteInput) -> Quote:
    if data.tier == "Commercial":  # typed by the user; never takes a number from the Academic sequence
        number, seq = manual_number(db, data.number), 0
    else:
        number, seq = next_number(db, data.tier)
    q = Quote(number=number, seq=seq, date=data.date or dt.date.today(), valid_until=dt.date.today(),
              customer_name="x")
    db.add(q)
    _fill(db, q, data)
    q.terms = app_settings.terms_snapshot(db)  # later edits of the Terms never change this quotation
    try:
        db.commit()
    except IntegrityError:  # two people typed the same number at once
        db.rollback()
        raise QuoteError(f"{number} is already used by another quotation")
    return q


def update_quote(db: Session, q: Quote, data: QuoteInput) -> Quote:
    _fill(db, q, data)
    # A Commercial number can be corrected until the quote has revisions (their names are built from it).
    if data.tier == "Commercial" and q.revision == 0 and data.number and data.number.strip():
        n = manual_number(db, data.number, q.id)
        if n != q.number:
            if db.scalar(select(func.count()).select_from(Quote).where(Quote.number.like(q.number + "-R%"))):
                raise QuoteError("this quote has revisions, so its number can't change")
            q.number = n
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise QuoteError("that quote number is already used by another quotation")
    return q


def set_status(db: Session, q: Quote, status: str) -> Quote:
    if status not in STATUSES:
        raise QuoteError(f"status must be one of {STATUSES}")
    q.status = status
    db.commit()
    return q


def amount_pinned(l: QuoteLine) -> bool:
    """True when the line total was typed rather than worked out from the unit price."""
    if l.complimentary:
        return False
    return l.amount != compute([LineIn(l.unit_price, l.users_label, l.years)], 0).lines[0].amount


def as_input(q: Quote) -> QuoteInput:
    """A stored quote as editable input (frozen values become explicit overrides)."""
    return QuoteInput(
        customer=CustomerIn(id=q.customer_id, name=q.customer_name, address=q.customer_address, email=q.customer_email),
        tier=q.tier, date=q.date, valid_until=q.valid_until, discount_pct=q.discount_pct, gst_mode=q.gst_mode,
        gst_pct=q.gst_pct, payment_terms=q.payment_terms, prepared_by=q.prepared_by,
        lines=[LineInput(product_id=l.product_id, label=l.label, deployment=l.deployment, users_label=l.users_label,
                         years=l.years, unit_price=l.unit_price, complimentary=l.complimentary,
                         amount=l.amount if amount_pinned(l) else None,
                         description=l.description) for l in q.lines],
    )


def revise(db: Session, q: Quote) -> Quote:
    base = base_number(q.number)
    n = db.scalar(select(func.max(Quote.revision)).where(
        (Quote.number == base) | Quote.number.like(base + "-R%"))) + 1
    new = Quote(number=f"{base}-R{n}", seq=q.seq, revision=n, date=dt.date.today(),
                valid_until=dt.date.today(), customer_name="x")
    db.add(new)
    data = as_input(q)
    data.date = dt.date.today()
    data.valid_until = None
    _fill_frozen(db, new, q, data)
    new.terms = app_settings.terms_snapshot(db)  # a new revision is a new issue: current terms
    db.commit()
    return new


def duplicate(db: Session, q: Quote, number: str | None = None) -> Quote:
    if q.tier == "Commercial":
        number, seq = manual_number(db, number), 0
    else:
        number, seq = next_number(db, q.tier)
    new = Quote(number=number, seq=seq, date=dt.date.today(), valid_until=dt.date.today(), customer_name="x")
    db.add(new)
    data = as_input(q)
    data.date, data.valid_until = dt.date.today(), None
    _fill_frozen(db, new, q, data)
    new.terms = app_settings.terms_snapshot(db)
    db.commit()
    return new


def _fill_frozen(db: Session, new: Quote, old: Quote, data: QuoteInput) -> None:
    """Copy keeping the old quote's customer text and line prices exactly (no re-pricing)."""
    _fill(db, new, data, save_customer=False)
    for nl, ol in zip(new.lines, old.lines):
        nl.block_keys = ol.block_keys


# ── document mapping ──────────────────────────────────────────────────────
def _years_text(y: int) -> str:
    return f"{y} Year" + ("s" if y != 1 else "")


def _pct(p) -> str:
    return f"{Decimal(p).normalize():f}"


def to_doc_quote(q: Quote) -> DocQuote:
    t = compute([LineIn(l.unit_price, l.users_label, l.years, l.complimentary, l.amount) for l in q.lines],
                q.discount_pct, q.gst_mode, q.gst_pct)
    summary: list[tuple[str, str]] = []
    if q.discount_amount > 0:
        summary += [("LIST TOTAL", rupees(t.list_total)),
                    ("DISCOUNT", rupees(-t.discount_amount))]  # the percentage is not printed
    # no separate GST rows: the last row is the price, always including GST
    summary.append(("TOTAL AMOUNT INC. GST", rupees(t.total)))

    years = {l.years for l in q.lines if not l.complimentary} or {q.lines[0].years}
    if len(years) == 1:
        y = next(iter(years))
        license_term = f"{y} year{'s' if y != 1 else ''} from date of activation."
        license_duration = _years_text(y)
    else:
        license_term = "As per the duration stated against each product, from date of activation."
        license_duration = "As per product"
    blocks = {k for l in q.lines for k in l.block_keys.split(",") if k}
    return DocQuote(
        number=q.number,
        date=q.date.strftime("%d/%m/%Y"),
        customer_name=q.customer_name,
        customer_address=q.customer_address,
        customer_email=q.customer_email,
        license_type=f"{q.tier} License",
        lines=[DocLine(l.label, l.deployment, l.users_label, _years_text(l.years), rupees(l.unit_price),
                       rupees(l.amount), l.complimentary, description=l.description) for l in q.lines],
        summary=summary,
        validity_date=f"{q.valid_until.day} {q.valid_until.strftime('%b %Y')}",
        payment_terms=q.payment_terms,
        license_term=license_term,
        license_duration=license_duration,
        quoted_blocks=blocks,
        terms=json.loads(q.terms) if q.terms else None,
    )

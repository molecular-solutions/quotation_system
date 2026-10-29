"""Prices by number of users: validation, the Prices page's save, and csv/xlsx upload + export.

A price row is the TOTAL for that many users for one year (not a per-user price) and there is no formula between
rows. It can also carry the price of a 3-year quote (the total for 3 years, which is not 3 x the annual price). A licence-style product (Floating, Group, ...) has one price, stored at 1 user. Past MAX_USERS the user types
the price on the quotation."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import TIERS, ImportLog, Product, ProductPrice
from app.services.tables import norm

MAX_USERS = 25
MAX_FILE_ROWS = 20000


class PriceError(ValueError):
    pass


def priced(p: Product) -> bool:
    return p.price_basis != "complimentary"


def max_users(p: Product) -> int:
    return 1 if p.price_basis == "per_license_year" else MAX_USERS


def parse_price(raw) -> Decimal | None:
    """None for a blank cell; ValueError for junk or a negative number."""
    s = "" if raw is None else re.sub(r"[₹,\s]|rs\.?|inr", "", str(raw), flags=re.I)
    if not s:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError("not a number")
    if d < 0:
        raise ValueError("must not be negative")
    return d.quantize(Decimal("0.01"))


def parse_users(raw) -> int:
    s = str(raw).strip()
    if not re.fullmatch(r"\d+(\.0+)?", s):
        raise ValueError("users must be a whole number")
    n = int(float(s))
    if n < 1:
        raise ValueError("users must be at least 1")
    return n


def check_users(p: Product, users: int) -> None:
    if not priced(p):
        raise ValueError("a complimentary product has no prices")
    if users > max_users(p):
        raise ValueError("a licence has a single price (1 user)" if max_users(p) == 1
                         else f"prices go up to {MAX_USERS} users; type the price on the quotation for more")


def warnings_for(p: Product, prices: dict[int, Decimal]) -> list[str]:
    """Things worth a second look (not errors): a hole in the list, or a price that doesn't rise with users."""
    out: list[str] = []
    if not prices:
        return out
    top = max(prices)
    gaps = [n for n in range(1, top + 1) if n not in prices]
    if gaps and max_users(p) > 1:
        out.append("no price for " + ", ".join(map(str, gaps[:8])) + (" …" if len(gaps) > 8 else "") + " user(s)")
    prev = None
    for n in sorted(prices):
        if prev is not None and prices[n] <= prices[prev]:
            out.append(f"{n} users costs no more than {prev} user(s)")
        prev = n
    return out


# ── reading / writing the stored prices ───────────────────────────────────
def tier_prices(p: Product) -> dict[str, dict[int, Decimal]]:
    out: dict[str, dict[int, Decimal]] = {t: {} for t in TIERS}
    for pr in p.prices:
        out.setdefault(pr.tier, {})[pr.users] = pr.annual_price
    return out


def summary(db: Session) -> list[dict]:
    """One row per priceable product for the list on the left of the Prices page."""
    rows = []
    for p in db.scalars(select(Product).where(Product.active.is_(True)).order_by(func.lower(Product.vendor), func.lower(Product.name))):
        if not priced(p):
            continue
        tp = tier_prices(p)
        want = max_users(p)
        rows.append({"id": p.id, "name": p.name, "vendor": p.vendor, "price_basis": p.price_basis, "max_users": want,
                     "counts": {t: len(tp[t]) for t in TIERS},
                     "complete": {t: all(n in tp[t] for n in range(1, want + 1)) for t in TIERS}})
    return rows


def tier_prices3(p: Product) -> dict[str, dict[int, Decimal]]:
    """The 3-year prices, same shape as tier_prices (only the user counts that have one)."""
    out: dict[str, dict[int, Decimal]] = {t: {} for t in TIERS}
    for pr in p.prices:
        if pr.three_year_price is not None:
            out.setdefault(pr.tier, {})[pr.users] = pr.three_year_price
    return out


def detail(p: Product) -> dict:
    tp, tp3 = tier_prices(p), tier_prices3(p)
    return {"id": p.id, "name": p.name, "vendor": p.vendor, "price_basis": p.price_basis, "max_users": max_users(p),
            "prices": {t: {str(n): float(v) for n, v in sorted(tp[t].items())} for t in TIERS},
            "prices3": {t: {str(n): float(v) for n, v in sorted(tp3[t].items())} for t in TIERS},
            "warnings": {t: warnings_for(p, tp[t]) for t in TIERS}}


def set_tier(db: Session, p: Product, tier: str, raw: dict, raw3: dict | None = None) -> dict[str, str]:
    """Replace one bundle's whole price list. Blank = no price for that user count. `raw3` is the 3-year price list
    (None = leave the stored 3-year prices alone). Returns {users: error} ("3y<users>" for a 3-year price) and stores
    nothing when there are any."""
    if tier not in TIERS:
        raise PriceError(f"tier must be one of {TIERS}")
    if not priced(p):
        raise PriceError("a complimentary product has no prices")
    clean: dict[int, Decimal] = {}
    errors: dict[str, str] = {}
    for k, v in raw.items():
        try:
            n = parse_users(k)
            check_users(p, n)
            price = parse_price(v)
        except ValueError as e:
            errors[str(k)] = str(e)
            continue
        if price is not None:
            clean[n] = price
    clean3: dict[int, Decimal] | None = None
    if raw3 is not None:
        clean3 = {}
        for k, v in raw3.items():
            try:
                n = parse_users(k)
                check_users(p, n)
                price = parse_price(v)
            except ValueError as e:
                errors[f"3y{k}"] = str(e)
                continue
            if price is not None:
                if n not in clean:
                    errors[f"3y{k}"] = "enter the 1-year price first"
                clean3[n] = price
    if errors:
        return errors
    have = {x.users: x for x in p.prices if x.tier == tier}
    for n, row in have.items():
        if n not in clean:
            p.prices.remove(row)
    for n, price in clean.items():
        three = clean3.get(n) if clean3 is not None else (have[n].three_year_price if n in have else None)
        if n in have:
            have[n].annual_price, have[n].three_year_price = price, three
        else:
            p.prices.append(ProductPrice(tier=tier, users=n, annual_price=price, three_year_price=three))
    db.flush()
    return {}


# ── upload ────────────────────────────────────────────────────────────────
@dataclass
class Row:
    n: int
    product: str
    tier: str
    users: int | None
    price: Decimal | None
    errors: list[str] = field(default_factory=list)
    product_id: int | None = None
    price3: Decimal | None = None       # the 3-year price; blank = leave the stored one alone


@dataclass
class Preview:
    filename: str
    rows: list[Row]

    @property
    def good(self): return [r for r in self.rows if not r.errors]
    @property
    def bad(self): return [r for r in self.rows if r.errors]


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _long_rows(grid: list[list[str]], tier_hint: str = "") -> list[Row]:
    head = [norm(h) for h in grid[0]]

    def col(*names):
        return next((i for i, h in enumerate(head) if h in names), None)
    ip, it, iu, ia = col("product name", "product", "name"), col("tier", "bundle", "type"), col("users", "number of users"), \
        col("annual price", "price", "annual")
    i3 = col("3 year price", "3 years price", "three year price", "price 3 years")
    if ip is None or iu is None or ia is None or (it is None and not tier_hint):
        raise PriceError("expected columns: Product Name, Tier, Users, Annual Price")
    out = []
    for n, r in enumerate(grid[1:], start=2):
        if not any(r):
            continue
        g = lambda i: r[i] if i is not None and i < len(r) else ""
        out.append(_row(n, g(ip), g(it) or tier_hint, g(iu), g(ia), g(i3)))
    return out


def _matrix_rows(grid: list[list[str]], tier: str, three_year: bool = False) -> list[Row]:
    head = grid[0]
    ip = next((i for i, h in enumerate(head) if norm(h) in ("product name", "product", "name")), None)
    if ip is None:
        raise PriceError(f"sheet {tier}: the first row needs a 'Product Name' column")
    users_cols = [(i, h) for i, h in enumerate(head) if re.fullmatch(r"\d+(\.0+)?", h or "")]
    out = []
    for n, r in enumerate(grid[1:], start=2):
        if ip >= len(r) or not r[ip]:
            continue
        for i, h in users_cols:
            if i < len(r) and r[i] != "":          # a blank cell means "no change", not "no price"
                out.append(_row(n, r[ip], tier, h, None, r[i]) if three_year else _row(n, r[ip], tier, h, r[i]))
    return out


def _row(n, product, tier, users, price, price3="") -> Row:
    """`price` is the annual price (required unless this is a 3-year-only row, price=None); `price3` is optional."""
    r = Row(n, str(product).strip(), "", None, None)
    t = next((x for x in TIERS if norm(x) == norm(tier)), None)
    if t is None:
        r.errors.append(f"tier must be one of {', '.join(TIERS)}")
    r.tier = t or str(tier)
    try:
        r.users = parse_users(users)
    except ValueError as e:
        r.errors.append(str(e))
    try:
        r.price3 = parse_price(price3)
    except ValueError as e:
        r.errors.append("3-year price: " + str(e))
    if price is None:                           # a row of a '<tier> 3 Years' sheet
        if r.price3 is None:
            r.errors.append("price is blank")
        return r
    try:
        r.price = parse_price(price)
        if r.price is None:
            r.errors.append("price is blank")
    except ValueError as e:
        r.errors.append(str(e))
    return r


def read_file(filename: str, content: bytes) -> list[Row]:
    name = filename.lower()
    if name.endswith((".csv", ".txt")):
        text = content.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        grid = [[_cell(c) for c in row] for row in csv.reader(io.StringIO(text), dialect)]
        grid = [r for r in grid if any(r)]
        if not grid:
            raise PriceError("the file is empty")
        return _long_rows(grid)
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as e:
            raise PriceError(f"cannot open this Excel file: {e}") from e
        rows: list[Row] = []
        for sheet in wb.sheetnames:
            grid = [[_cell(c) for c in row] for row in wb[sheet].iter_rows(values_only=True)]
            grid = [r for r in grid if any(r)]
            if not grid:
                continue
            tier = next((t for t in TIERS if norm(t) == norm(sheet)), None)
            tier3 = next((t for t in TIERS if norm(f"{t} 3 years") == norm(sheet)), None)
            head = [norm(h) for h in grid[0]]
            if "users" in head or "number of users" in head:
                rows += _long_rows(grid, tier or "")
            elif tier:
                rows += _matrix_rows(grid, tier)
            elif tier3:
                rows += _matrix_rows(grid, tier3, three_year=True)
        if not rows:
            raise PriceError("no prices found: name the sheets 'Academic' and 'Commercial' (a row per product, a "
                             "column per number of users; 'Academic 3 Years' / 'Commercial 3 Years' for 3-year prices), "
                             "or use columns Product Name, Tier, Users, Annual Price [, 3 Year Price]")
        return rows
    raise PriceError("unsupported file type: upload a .csv or .xlsx file")


def preview(db: Session, filename: str, content: bytes) -> Preview:
    rows = read_file(filename, content)
    if len(rows) > MAX_FILE_ROWS:
        raise PriceError(f"too many prices ({len(rows)}); the limit is {MAX_FILE_ROWS} per upload")
    products = {p.name.lower(): p for p in db.scalars(select(Product))}
    seen: set[tuple] = set()
    annual_in_file = {(r.product.lower(), r.tier, r.users) for r in rows if r.price is not None}
    for r in rows:
        p = products.get(r.product.lower())
        if p is None:
            r.errors.append("no product with this name (add it on the Data page first)")
            continue
        r.product_id = p.id
        if r.users is not None:
            try:
                check_users(p, r.users)
            except ValueError as e:
                r.errors.append(str(e))
            if r.price is None and r.price3 is not None and not r.errors:
                have = any(x.tier == r.tier and x.users == r.users for x in p.prices)
                if not have and (r.product.lower(), r.tier, r.users) not in annual_in_file:
                    r.errors.append("a 3-year price needs a 1-year price for the same product, bundle and users")
            key = (p.id, r.tier, r.users, r.price is None)
            if key in seen:
                r.errors.append("this product, bundle and user count appears more than once")
            seen.add(key)
    return Preview(filename, rows)


def commit(db: Session, prev: Preview) -> tuple[int, int]:
    existing = {(x.product_id, x.tier, x.users): x for x in db.scalars(select(ProductPrice))}
    ok = 0
    for r in sorted(prev.good, key=lambda r: r.price is None):      # 1-year prices first: a 3-year price hangs off one
        row = existing.get((r.product_id, r.tier, r.users))
        if r.price is not None:
            if row:
                row.annual_price = r.price
            else:
                row = ProductPrice(product_id=r.product_id, tier=r.tier, users=r.users, annual_price=r.price)
                db.add(row)
                existing[(r.product_id, r.tier, r.users)] = row
        if r.price3 is not None:
            row.three_year_price = r.price3
        ok += 1
    db.add(ImportLog(table_name="prices", filename=prev.filename[:200], rows_ok=ok, rows_failed=len(prev.bad)))
    db.commit()
    return ok, len(prev.bad)


# ── export ────────────────────────────────────────────────────────────────
def long_rows(db: Session) -> list[tuple]:
    q = (select(Product.name, ProductPrice.tier, ProductPrice.users, ProductPrice.annual_price, ProductPrice.three_year_price)
         .join(ProductPrice).order_by(func.lower(Product.name), ProductPrice.tier, ProductPrice.users))
    return [tuple(r) for r in db.execute(q)]


def export_csv(db: Session) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Product Name", "Tier", "Users", "Annual Price", "3 Year Price"])
    w.writerows((n, t, u, a, "" if a3 is None else a3) for n, t, u, a, a3 in long_rows(db))
    return buf.getvalue().encode("utf-8-sig")


def export_xlsx(db: Session) -> bytes:
    """One sheet per bundle (and one '<bundle> 3 Years' sheet for the 3-year prices): a row per product, a column per
    number of users (the shape upload also accepts)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    wb.remove(wb.active)
    head = PatternFill("solid", fgColor="001F5F")
    products = [p for p in db.scalars(select(Product).where(Product.active.is_(True))) if priced(p)]
    products.sort(key=lambda p: ((p.vendor or "~").lower(), p.name.lower()))
    for tier, three in [(t, False) for t in TIERS] + [(t, True) for t in TIERS]:
        ws = wb.create_sheet(f"{tier} 3 Years" if three else tier)
        ws.append(["Product Name", "Vendor"] + list(range(1, MAX_USERS + 1)))
        for c in ws[1]:
            c.font, c.fill, c.alignment = Font(bold=True, color="FFFFFF"), head, Alignment(horizontal="center")
        for p in products:
            tp = (tier_prices3(p) if three else tier_prices(p))[tier]
            ws.append([p.name, p.vendor] + [float(tp[n]) if n in tp else None for n in range(1, MAX_USERS + 1)])
        ws.freeze_panes = "C2"
        ws.column_dimensions["A"].width = 46
        ws.column_dimensions["B"].width = 24
        for i in range(3, MAX_USERS + 3):
            ws.column_dimensions[get_column_letter(i)].width = 11
        for row in ws.iter_rows(min_row=2, min_col=3):
            for c in row:
                c.number_format = "#,##0"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

"""All money maths lives here (Decimal, ROUND_HALF_UP). The browser never re-implements it."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

TWO = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(x).quantize(TWO, rounding=ROUND_HALF_UP)


def parse_users(label: str) -> Decimal:
    """'25' -> 25; 'Floating' / '1 Group License' / 'Concurrent' -> 1 (text still prints)."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", label or "")
    return Decimal(m.group(1)) if m else Decimal(1)


@dataclass
class LineIn:
    unit_price: Decimal
    users_label: str = "1"
    years: int = 1
    complimentary: bool = False
    amount: Decimal | None = None   # a typed line total: used as given instead of unit price x years


@dataclass
class LineOut:
    qty: Decimal
    amount: Decimal


@dataclass
class Totals:
    lines: list[LineOut]
    list_total: Decimal
    discount_amount: Decimal
    subtotal: Decimal
    tax_rows: list[tuple[str, Decimal]]  # [("IGST 18%", x)] or [("CGST 9%", x), ("SGST 9%", x)]
    tax_amount: Decimal
    total: Decimal


def _pct(p: Decimal) -> str:
    return f"{p.normalize():f}"


def compute(lines: list[LineIn], discount_pct, gst_mode: str = "IGST", gst_pct=18) -> Totals:
    """Every quote is priced including GST: the tax is added to the sub total and the total is what is printed."""
    discount_pct, gst_pct = Decimal(str(discount_pct)), Decimal(str(gst_pct))
    if not (Decimal(0) <= discount_pct <= Decimal(100)):
        raise ValueError("discount must be between 0 and 100")
    out: list[LineOut] = []
    for ln in lines:
        qty = parse_users(ln.users_label)
        if ln.complimentary:
            amount = Decimal(0)
        elif ln.amount is not None:
            amount = money(ln.amount)
        else:
            amount = money(Decimal(ln.unit_price) * ln.years)  # the unit price already covers all the users
        out.append(LineOut(qty, amount))
    list_total = sum((o.amount for o in out), Decimal(0))
    discount = money(list_total * discount_pct / 100)
    subtotal = list_total - discount
    if gst_mode == "CGST_SGST":
        half = gst_pct / 2
        each = money(subtotal * half / 100)
        tax_rows = [(f"CGST {_pct(half)}%", each), (f"SGST {_pct(half)}%", each)]
    else:
        tax_rows = [(f"IGST {_pct(gst_pct)}%", money(subtotal * gst_pct / 100))]
    tax = sum((v for _, v in tax_rows), Decimal(0))
    return Totals(out, money(list_total), discount, money(subtotal), tax_rows, tax, money(subtotal + tax))


def unit_from_total(amount, years: int) -> Decimal:
    """The annual price that a typed line total works out to (shown to the paisa)."""
    if years <= 0:
        raise ValueError("years must be above zero")
    return money(Decimal(amount) / years)


def inr(x) -> str:
    """Indian digit grouping: 1234567.5 -> '12,34,567.50'."""
    x = money(x)
    sign = "-" if x < 0 else ""
    whole, frac = f"{abs(x):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    if head:
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        whole = head + "," + tail
    return f"{sign}{whole}.{frac}"


def rupees(x) -> str:
    x = money(x)
    return f"{'- ' if x < 0 else ''}₹ {inr(abs(x))}"

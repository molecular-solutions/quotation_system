"""Make DUMMY per-user-count price tables for every product (placeholder data, not real prices).

    python scripts/dummy_prices.py products.json seed/

products.json: [{"id", "name", "vendor", "basis", "prices": {"Academic": 42000, "Commercial": 85000}}, ...]
Writes seed/dummy_user_prices.csv (one row per product/tier/users – the shape of the new table) and
seed/dummy_user_prices.xlsx (one sheet per tier, a row per product and a column per number of users – the shape
the Data page would edit). A product's real 1-user price is kept when it has one; the rest of the curve is invented:
irregular, strictly rising, and cheaper per user as the user count grows."""
import csv
import json
import random
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

MAX_USERS = 25
TIERS = ("Academic", "Commercial")


def rnd(x: float, step: int) -> int:
    return int(round(x / step) * step)


def curve(rng: random.Random, one_user: int) -> list[int]:
    """Price for 1..MAX_USERS users: a sub-linear trend with per-step noise, so no formula recovers it."""
    exp = rng.uniform(0.74, 0.90)
    step = 100 if one_user < 30000 else 500
    out = [one_user]
    for n in range(2, MAX_USERS + 1):
        p = rnd(one_user * n ** exp * rng.uniform(0.96, 1.04), step)
        out.append(max(p, out[-1] + step))          # always strictly more users -> strictly higher price
    return out


def build(products: list[dict]) -> list[dict]:
    rows = []
    for p in products:
        if p["basis"] == "complimentary":
            continue
        rng = random.Random(p["id"] * 7919)
        com1 = p["prices"].get("Commercial") or rnd(rng.uniform(15000, 120000), 500)
        aca1 = p["prices"].get("Academic") or rnd(com1 * rng.uniform(0.35, 0.55), 500)
        for tier, one in (("Academic", int(aca1)), ("Commercial", int(com1))):
            prices = [one] if p["basis"] == "per_license_year" else curve(rng, one)  # a licence has one price
            for n, price in enumerate(prices, 1):
                rows.append({"product": p["name"], "vendor": p["vendor"], "tier": tier, "users": n, "price": price})
    return rows


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Product Name", "Tier", "Users", "Annual Price"])
        w.writerows([r["product"], r["tier"], r["users"], r["price"]] for r in rows)


def write_xlsx(rows, path):
    wb = Workbook()
    wb.remove(wb.active)
    head = PatternFill("solid", fgColor="001F5F")
    for tier in TIERS:
        ws = wb.create_sheet(tier)
        ws.append(["Product Name", "Vendor"] + list(range(1, MAX_USERS + 1)))
        for c in ws[1]:
            c.font, c.fill, c.alignment = Font(bold=True, color="FFFFFF"), head, Alignment(horizontal="center")
        seen: dict[str, dict] = {}
        for r in rows:
            if r["tier"] == tier:
                seen.setdefault(r["product"], {"vendor": r["vendor"], "p": {}})["p"][r["users"]] = r["price"]
        for name, d in sorted(seen.items(), key=lambda kv: (kv[1]["vendor"] or "~", kv[0])):
            ws.append([name, d["vendor"]] + [d["p"].get(n) for n in range(1, MAX_USERS + 1)])
        ws.freeze_panes = "C2"
        ws.column_dimensions["A"].width = 46
        ws.column_dimensions["B"].width = 24
        for i in range(3, MAX_USERS + 3):
            ws.column_dimensions[get_column_letter(i)].width = 11
        for row in ws.iter_rows(min_row=2, min_col=3):
            for c in row:
                c.number_format = "#,##0"
    wb.save(path)


if __name__ == "__main__":
    products = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    rows = build(products)
    write_csv(rows, out / "dummy_user_prices.csv")
    write_xlsx(rows, out / "dummy_user_prices.xlsx")
    n = len({r["product"] for r in rows})
    print(f"{len(rows)} price rows for {n} products -> {out}/dummy_user_prices.csv, .xlsx")

"""Load the prices from the company's PRICING CHART workbook into the database, adding any product it lists that the
database does not have yet.

    python scripts/import_pricing.py "PRICING CHART - UPDATED 2026.xlsx"            # dry run: report only, nothing written
    python scripts/import_pricing.py "PRICING CHART - UPDATED 2026.xlsx" --apply    # write (SQLite is backed up first)
    python scripts/import_pricing.py chart.xlsx --apply --database-url sqlite:///path/to/quotes.db

By default it uses the app's own database (QUOTE_DATA_DIR / DATABASE_URL). Docker: copy the workbook into the container
(`docker compose cp chart.xlsx app:/tmp/chart.xlsx`), then `docker compose exec app python scripts/import_pricing.py /tmp/chart.xlsx`.

What it reads: the QUOTE PRICE (1 YEAR) column of every table in TABLES below, and the QUOTE PRICE (3 YEARS) column
where the table has one (a 3-year price is stored as it is, never as 3 x the annual one). These are the sheet's formula results, so the workbook must be saved by Excel/LibreOffice after calculating;
an empty cell or a #VALUE! means "no price". Each table is found by its header row, and the script stops when a
header is not where TABLES expects it, so a re-arranged sheet cannot be read into the wrong product.

What it writes, for every product the sheet prices: that product's Academic and Commercial price lists are REPLACED by
the sheet's (a user count the sheet has no price for ends up with none; type it on the quotation). Products the sheet
does not mention have their prices removed (blank, to be typed on the quotation), unless the sheet gave no prices at
all. Prices are never printed here.

To change which product a table feeds, edit its route/name in TABLES; a product name that is not in the database is
created from the table's `new` attributes."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
ACADEMIC, COMMERCIAL = "Academic", "Commercial"


# ── how the sheet maps to products ────────────────────────────────────────
@dataclass(frozen=True)
class Target:
    """Where one table row goes: a product and a number of users. `licence` products have a single price (stored
    at 1 user): the cores / Xunits / GPU / credits options and the 'Unlimited' rows."""
    product: str
    users: int = 1
    licence: bool = False
    users_label: str = "1"          # the Users text a newly created licence product starts with


@dataclass
class Table:
    sheet: str
    tier: str
    header_row: int                 # the row holding the column titles; data runs down from the next row
    key_col: str                    # column of the row label (users, cores, Xunits ...)
    price_col: str                  # column of the price to store
    key_title: str                  # text the key column's title must contain (layout guard)
    route: Callable[[str], Target]
    new: dict                       # attributes of the product when it has to be created
    price_title: str = "QUOTE PRICE (1 YEAR)"
    users_col: str | None = None    # take the user count from this column instead of the row label
    per_user: bool = False          # the sheet price is per user: store price x users
    price3_col: str | None = None   # column of the 3-year price, when the table has one
    price3_title: str = "QUOTE PRICE (3 YEAR"
    years: int = 1                  # 3 = the price column is a 3-year price (no 1-year column): stored as the 3-year
                                    # price, with price / 3 as the annual one

    @property
    def label(self) -> str:
        return f"{self.sheet}!{self.key_col}{self.header_row} ({self.tier})"


def whole(key: str) -> int:
    if not re.fullmatch(r"\d+", key):
        raise ValueError(f"expected a number, found '{key}'")
    return int(key)


def counted(name: str, **licences: tuple[str, str]) -> Callable[[str], Target]:
    """Rows are numbers of users of one product. A row such as 'Unlimited' or 'Additional' is a licence product of
    its own: counted("X", unlimited=("X - Unlimited Users", "Unlimited"))."""
    def route(key: str) -> Target:
        if key.lower() in licences:
            product, label = licences[key.lower()]
            return Target(product, licence=True, users_label=label)
        return Target(name, whole(key))
    return route


def licence(name_of: Callable[[str], str], users_label: str | Callable[[str], str] = "Floating") -> Callable[[str], Target]:
    """Every row is a different size of the same licence and becomes a product of its own."""
    return lambda key: Target(name_of(key), licence=True, users_label=users_label(key) if callable(users_label) else users_label)


def cores(base: str) -> Callable[[str], str]:
    return lambda key: (f"{base} (Infinite Cores)" if key.upper().startswith("INFIN") else f"{base} ({whole(key)} Cores)")


def pair(sheet: str, row: int, key_title: str, route, new: dict, left=("B", "E"), right=("H", "K"), **kw) -> list[Table]:
    """The Academic table with the Commercial one beside it on the same header row. Each side is (key column, 1-year
    price column[, 3-year price column])."""
    return [Table(sheet, tier, row, side[0], side[1], key_title, route, new,
                  price3_col=side[2] if len(side) > 2 else None, **kw)
            for tier, side in ((ACADEMIC, left), (COMMERCIAL, right))]


def attrs(vendor: str, segment: str, blocks: str = "", deployment: str = "Local") -> dict:
    return {"vendor": vendor, "segment": segment, "catalog_blocks": blocks, "deployment": deployment}


CHEMAIRS_MODULES = ["Retrosynthesis", "Condition Search", "Process Chemistry", "Forward Synthesis", "SA Score",
                    "Impurity Prediction", "Bayesian Optimisation", "Bundled Package"]   # top to bottom on the sheet
XTALPI = attrs("XtalPi", "Hit to Lead Transformation", deployment="Cloud")
BSI = attrs("BioSolveIT", "Ultra-High Throughput Virtual Screening", "seesar_csd")
YASARA = attrs("YASARA", "Molecular Modelling", "yasara")
KVANTIFY = attrs("Kvantify", "Kvantify", "kvantify")
PATSNAP = attrs("PatSnap", "Patent Analysis", deployment="Cloud")


def _xtalpi() -> list[Table]:
    out = []
    for tier, row in ((ACADEMIC, 20), (COMMERCIAL, 35)):
        out += [
            # the cloud version, priced by Xunits: 20,000 Xunits is the product that is already in the database
            Table("XTALPI", tier, row, "B", "F", "XUNITS", licence(
                lambda k: "XFEP & XMolGen" if whole(k) == 20000 else f"XFEP & XMolGen - {whole(k):,} Xunits"),
                  {**XTALPI, "catalog_blocks": "xfep, xmolgen"}, price_title="QUOTE PRICE"),
            # custom cloud: XFEP by GPUs, XMolGen by users
            Table("XTALPI", tier, row, "I", "M", "GPU", licence(lambda k: f"XFEP - Custom Cloud ({k})"),
                  {**XTALPI, "catalog_blocks": "xfep"}, price_title="QUOTE PRICE"),
            Table("XTALPI", tier, row + 7, "I", "M", "USERS", counted(
                "XMolGen - Custom Cloud", additional=("XMolGen - Custom Cloud (Additional User)", "1")),
                  {**XTALPI, "catalog_blocks": "xmolgen"}, price_title="QUOTE PRICE"),
        ]
    return out


def _chemairs() -> list[Table]:
    out = []
    for i, module in enumerate(CHEMAIRS_MODULES):     # eight tables, 14 rows apart
        out += pair("CHEMAIRS", 18 + 14 * i, "USER", counted(f"ChemAIRS - {module}"),
                    attrs("Chemical.AI", "Chemical Synthesis", "chemairs", "Cloud"), left=("B", "G", "H"), right=("J", "O"))
    return out


def _biosolveit() -> list[Table]:
    s = "BIOSOLVEIT"
    acad, comm = ("B", "E", "F"), ("H", "K")        # only the Academic tables have a 3-year column
    acad2, comm2 = ("M", "P", "Q"), ("S", "V")
    hpsee = lambda base, row: pair(s, row, "CORES", licence(cores(base)), BSI, acad, comm)  # noqa: E731
    plugin = "YASARA SeeSAR Plugin"
    existing = "YASARA SeeSAR Plugin (With Pre-Existing YASARA Structure License)"
    optibrium = attrs("Optibrium", "Lead Optimization")
    return [
        *pair(s, 20, "USERS", counted("SeeSAR"), BSI, acad, comm),
        Table(s, ACADEMIC, 33, "B", "E", "USERS", licence(  # special academic price for centres of excellence
            lambda k: f"SeeSAR - Academic Special Price (COE) - {whole(k)} Users", lambda k: f"{whole(k)} Users"), BSI,
              price3_col="F"),
        *pair(s, 20, "USERS", counted("InfiniSee"), attrs("BioSolveIT", "Ultra-High Throughput Virtual Screening", "infinisee"),
              acad2, comm2),
        *pair(s, 37, "USERS", counted("InfiniSee - All Three Search Modes"),
              attrs("BioSolveIT", "Ultra-High Throughput Virtual Screening", "infinisee"), acad2, comm2),
        *hpsee("HPSee - Chemical Space Docking", 44),
        *hpsee("HPSee - Remote Docking & Scoring", 56),
        *pair(s, 69, "USER", counted(plugin, unlimited=(f"{plugin} - Unlimited Users", "Unlimited")),
              {**BSI, "catalog_blocks": "seesar_csd, yasara"}, acad, comm),
        *pair(s, 77, "USER", counted(existing, unlimited=(f"{existing} - Unlimited Users", "Unlimited")),
              {**BSI, "catalog_blocks": "seesar_csd, yasara"}, acad, comm),
        *pair(s, 57, "USER", counted("Optibrium ADME/Tox Module - Node Locked"), optibrium, acad2, comm2),
        *pair(s, 66, "USER", counted("Optibrium ADME/Tox Module - Floating", unlimited=(
            "Optibrium ADME/Tox Module - Floating - Unlimited Users", "Unlimited")), optibrium, acad2, comm2),
    ]


def _patsnap() -> list[Table]:
    name = lambda k: f"PatSnap {k.title()}"  # noqa: E731
    return [
        # commercial: the price of the base users (1). academic: the sheet gives a price per user and the base is 3 users
        Table("PATSNAP", COMMERCIAL, 20, "B", "F", "PRODUCTS", lambda k: Target(name(k)), PATSNAP, users_col="C", price3_col="G"),
        Table("PATSNAP", ACADEMIC, 20, "I", "M", "PRODUCTS", lambda k: Target(name(k)), PATSNAP, users_col="J", per_user=True,
              price3_col="N"),
        Table("PATSNAP", COMMERCIAL, 35, "C", "F", "BASE USERS", licence(lambda k: f"PatSnap Eureka - {k.title()}", "1"), PATSNAP,
              price3_col="G"),
    ]


def _yasara() -> list[Table]:
    s = "YASARA"
    out = []
    # (product the Single User table feeds, product the Group Leader table feeds, header row of the Single User table)
    # Structure's Group Leader price goes to the existing "YASARA Structure" (sold as "1 Group License").
    for single, group, row, tiers in (
            ("YASARA Dynamics", "YASARA Dynamics - Group Leader", 16, "both"),
            ("YASARA Structure - Single User", "YASARA Structure", 33, "both"),
            ("YASARA Structure + NMR Model - Single User", "YASARA Structure + NMR Model - Group Leader", 49, "both"),
            ("YASARA Modeller", "YASARA Modeller - Group Leader", 65, "commercial")):
        for r, name in ((row, single), (row + 6, group)):
            if tiers == "both":
                out += pair(s, r, "USERS", counted(name), YASARA, left=("B", "E"), right=("G", "J"))
            else:
                out.append(Table(s, COMMERCIAL, r, "G", "J", "USERS", counted(name), YASARA))
    return out


def _kvantify() -> list[Table]:
    s = "KVANTIFY"
    return [
        *pair(s, 20, "USERS", counted("Koffee Unbinding Kinetics"), KVANTIFY),
        *pair(s, 34, "USERS", counted("Koffee Binding Affinity", unlimited=(
            "Koffee Binding Affinity - Unlimited Concurrent Usage", "Concurrent Usage")), KVANTIFY),
        *pair(s, 41, "USERS", counted("Qrunch Pro"), KVANTIFY),
        # academic only, and the sheet only has a 3-year price (years=3): stored as that, annual = a third of it
        Table(s, ACADEMIC, 48, "B", "E", "USERS", licence(lambda k: "Koffee Binding Affinity & Qrunch - Bundle Package",
              "Concurrent Usage"), KVANTIFY, price_title="QUOTE PRICE (3 YEARS)", years=3),
    ]


TABLES: list[Table] = _xtalpi() + _chemairs() + _biosolveit() + _patsnap() + _yasara() + _kvantify()


# ── reading the workbook ──────────────────────────────────────────────────
def read_workbook(path: Path) -> dict[str, list[tuple]]:
    """{sheet name: rows}, values only (cached formula results), like the app's own importer reads Excel files."""
    import io
    import zipfile

    import openpyxl
    # LibreOffice saves whole-row merges (<mergeCell ref="17:19"/>) that openpyxl cannot parse; merges do not change
    # any cell's value, so they are dropped from a copy held in memory.
    buf = io.BytesIO()
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.startswith("xl/worksheets/") and item.filename.endswith(".xml"):
                data = re.sub(rb"<mergeCells\b.*?</mergeCells>", b"", data, flags=re.S)
            dst.writestr(item, data)
    wb = openpyxl.load_workbook(buf, read_only=True, data_only=True)
    return {ws.title.strip().upper(): [tuple(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}


def _col(letter: str) -> int:
    from openpyxl.utils import column_index_from_string
    return column_index_from_string(letter) - 1


def cell(grid: list[tuple], col: str, row: int):
    r, c = row - 1, _col(col)
    return grid[r][c] if r < len(grid) and c < len(grid[r]) else None


def text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return " ".join(str(v).split())


@dataclass
class Row:
    product: str
    tier: str
    users: int
    price: Decimal | None           # the 1-year price; None = the cell is empty (or 0)
    price3: Decimal | None          # the 3-year price; None = the table has none, or the cell is empty
    licence: bool
    users_label: str
    new: dict
    where: str                      # cell it came from, for messages


@dataclass
class Extract:
    rows: list[Row] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)       # stop an --apply
    blanks: dict[str, list[str]] = field(default_factory=dict)   # table label -> price cells that are empty or 0
    blanks3: dict[str, list[str]] = field(default_factory=dict)  # the same for 3-year price cells
    tables_read: int = 0


def read_price(value, where: str, blanks: dict[str, list[str]], label: str, bad: list[str]) -> Decimal | None:
    """A price cell as money. An empty cell, or a formula that still adds up to 0, is no price (noted in `blanks`);
    a cell holding anything else that is not a number (#VALUE! ...) is noted in `bad`."""
    from app.services.prices import parse_price
    try:
        price = None if text(value) == "" else parse_price(text(value))
    except ValueError:
        bad.append(f"{where} shows '{text(value)}'")
        return None
    if not price:
        blanks.setdefault(label, []).append(where)
        return None
    return price


def extract(grids: dict[str, list[tuple]], tables: list[Table] | None = None) -> Extract:
    out = Extract()
    seen: dict[tuple, str] = {}
    for t in TABLES if tables is None else tables:
        grid = grids.get(t.sheet.upper())
        if grid is None:
            out.problems.append(f"{t.label}: the workbook has no sheet '{t.sheet}'")
            continue
        head = lambda col: text(cell(grid, col, t.header_row)).upper()  # noqa: E731
        expected = [(t.key_col, t.key_title), (t.price_col, t.price_title)] + ([(t.price3_col, t.price3_title)] if t.price3_col else [])
        wrong = [f"'{want}' in {col}{t.header_row} (found '{head(col)}')" for col, want in expected if want not in head(col)]
        if wrong:
            out.problems.append(f"{t.label}: layout changed. Expected " + " and ".join(wrong))
            continue
        out.tables_read += 1
        bad: list[str] = []         # price cells holding something that is not a number, e.g. #VALUE!
        r = t.header_row + 1
        while key := text(cell(grid, t.key_col, r)):
            where = f"{t.sheet}!{t.price_col}{r}"
            raw, raw3 = cell(grid, t.price_col, r), cell(grid, t.price3_col, r) if t.price3_col else None
            try:
                target = t.route(key)
                if t.users_col:
                    target = replace(target, users=whole(text(cell(grid, t.users_col, r))))
            except ValueError as e:
                out.problems.append(f"{where} ({t.tier}, '{key}'): {e}")
                r += 1
                continue
            price = read_price(raw, where, out.blanks, t.label, bad)
            price3 = read_price(raw3, f"{t.sheet}!{t.price3_col}{r}", out.blanks3, t.label, bad) if t.price3_col else None
            if price is not None and t.per_user:
                price, price3 = price * target.users, price3 * target.users if price3 is not None else None
            if t.years == 3 and price is not None:       # the sheet only has a 3-year price
                price, price3 = (price / 3).quantize(Decimal("0.01")), price
            if price is not None:
                dup = seen.setdefault((target.product.lower(), t.tier, target.users), where)
                if dup != where:
                    out.problems.append(f"{where}: {target.product}, {t.tier}, {target.users} user(s) is also given by {dup}")
                    price = price3 = None
            out.rows.append(Row(target.product, t.tier, target.users, price, price3, target.licence, target.users_label,
                                {**t.new}, where))
            r += 1
        if bad:
            out.problems.append(f"{t.label}: {len(bad)} price cell(s) are not numbers, first: {bad[0]}")
    return out


# ── writing to the database ───────────────────────────────────────────────
@dataclass
class Report:
    created: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    skipped: dict[str, list[str]] = field(default_factory=dict)   # reason -> rows the app cannot hold
    warnings: list[str] = field(default_factory=list)
    untouched: list[str] = field(default_factory=list)     # products in the sheet with no usable price
    stored: int = 0
    cleared: list[str] = field(default_factory=list)       # in the database, not in the sheet: prices removed


def ranges(nums) -> str:
    nums, out, i = sorted(nums), [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(out)


def _create(db, row: Row, problems: list[str]):
    from app.models import Product
    from app.services.tables import SPECS
    spec = SPECS["products"]
    raw = {**row.new, "name": row.product, "price_basis": "per_license_year" if row.licence else "per_user_year",
           "default_users": row.users_label if row.licence else "1"}
    clean, errors = spec.clean(db, raw, is_new=True)
    if errors:
        problems.append(f"cannot create product '{row.product}': {errors}")
        return None
    return db.get(Product, spec.upsert(db, clean))


def apply_rows(db, rows: list[Row], problems: list[str]) -> Report:
    from sqlalchemy import func, select
    from app.models import TIERS, Product
    from app.services.prices import check_users, priced, set_tier, tier_prices, tier_prices3

    rep = Report()
    by_product: dict[str, list[Row]] = {}
    for r in rows:
        by_product.setdefault(r.product, []).append(r)
    in_sheet = {n.lower() for n in by_product}
    for name in sorted(by_product, key=str.lower):
        group = by_product[name]
        p = db.scalar(select(Product).where(func.lower(Product.name) == name.lower()))
        is_new = p is None
        if is_new:
            p = _create(db, group[0], problems)
            if p is None:
                continue
        want: dict[str, dict[int, Decimal]] = {t: {} for t in TIERS}
        want3: dict[str, dict[int, Decimal]] = {t: {} for t in TIERS}
        for r in group:
            if r.price is None:
                if r.price3 is not None:
                    rep.skipped.setdefault("a 3-year price without a 1-year price", []).append(
                        f"{p.name} · {r.tier} · {r.users} user(s) · {r.where}")
                continue
            try:
                check_users(p, r.users)
            except ValueError as e:
                rep.skipped.setdefault(str(e), []).append(f"{p.name} · {r.tier} · {r.users} user(s) · {r.where}")
                continue
            want[r.tier][r.users] = r.price
            if r.price3 is not None:
                want3[r.tier][r.users] = r.price3
        kind = "one price" if p.price_basis == "per_license_year" else "by users"
        if not any(want.values()):
            if is_new:
                rep.created.append(f"{p.name}  [{kind}]  no prices")
            else:
                rep.untouched.append(p.name)
            continue
        said, counts = [], [0, 0, 0]
        for tier in TIERS:
            before, before3 = tier_prices(p)[tier], tier_prices3(p)[tier]
            for i, n in enumerate((sum(k not in before for k in want[tier]),
                                   sum(k in before and (before[k] != want[tier][k] or before3.get(k) != want3[tier].get(k))
                                       for k in want[tier]),
                                   sum(k not in want[tier] for k in before))):
                counts[i] += n
            errors = set_tier(db, p, tier, dict(want[tier]), dict(want3[tier]))
            assert not errors, errors
            rep.stored += len(want[tier])
            if want[tier] or before:
                said.append(f"{tier} {ranges(want[tier]) or 'none'}" + (f" [3-yr {ranges(want3[tier])}]" if want3[tier] else ""))
            prev = None
            for n in sorted(want[tier]):
                if prev is not None and want[tier][n] <= want[tier][prev]:
                    rep.warnings.append(f"{p.name} · {tier}: {n} users costs no more than {prev} user(s)")
                prev = n
        if is_new:
            rep.created.append(f"{p.name}  [{kind}]  " + "; ".join(said))
        else:
            rep.changes.append(f"{p.name}: " + "; ".join(said) + f"   (+{counts[0]} new, {counts[1]} changed, -{counts[2]} removed)")
    if rep.stored:      # an empty or unreadable sheet must never wipe the whole catalogue
        for p in db.scalars(select(Product).order_by(func.lower(Product.name))):
            if p.name.lower() not in in_sheet and priced(p) and p.prices:
                for tier in TIERS:
                    set_tier(db, p, tier, {}, {})
                rep.cleared.append(p.name)
    db.flush()
    return rep


# ── running ───────────────────────────────────────────────────────────────
def show(title: str, lines: list[str], limit: int = 0) -> None:
    if lines:
        print(f"\n{title} ({len(lines)})")
        for ln in lines[:limit or None]:
            print("  " + ln)
        if limit and len(lines) > limit:
            print(f"  … and {len(lines) - limit} more")


def import_workbook(db, path: Path, apply: bool = False, ignore_problems: bool = False) -> int:
    """Plan (and with apply=True, store) the prices. Returns the exit code: 1 when problems stopped it."""
    from app.models import ImportLog
    ext = extract(read_workbook(path))
    print(f"{path.name}: {ext.tables_read} of {len(TABLES)} tables found, "
          f"{sum(1 for r in ext.rows if r.price is not None)} prices read, {sum(map(len, ext.blanks.values()))} price cells empty or 0, "
          f"{sum(1 for r in ext.rows if r.price3 is not None)} 3-year prices read")
    problems = list(ext.problems)
    rep = apply_rows(db, ext.rows, problems)
    show("PROBLEMS (fix the sheet or TABLES)", problems)
    show("NEW PRODUCTS", rep.created)
    show("PRICES", rep.changes)
    for reason, lines in rep.skipped.items():
        show(f"NOT STORED: {reason}", lines)
    show("CHECK", rep.warnings)
    show("EMPTY OR 0 PRICE CELLS (no price stored)", [f"{label}: {len(c)}, e.g. {', '.join(c[:3])}" for label, c in ext.blanks.items()])
    show("EMPTY OR 0 3-YEAR PRICE CELLS (that quote is priced by hand)", [f"{label}: {len(c)}, e.g. {', '.join(c[:3])}" for label, c in ext.blanks3.items()])
    show("IN THE SHEET BUT WITHOUT ANY PRICE (left as they are)", rep.untouched)
    show("NOT IN THE SHEET: PRICES REMOVED (left blank)", rep.cleared)
    if problems and apply and not ignore_problems:
        db.rollback()
        print("\nNothing was written. Fix the problems above, or pass --ignore-problems to import the rest.")
        return 1
    if apply:
        db.add(ImportLog(table_name="prices", filename=path.name[:200], rows_ok=rep.stored, rows_failed=sum(map(len, rep.skipped.values()))))
        db.commit()
        print(f"\nDone: {rep.stored} prices stored, {len(rep.created)} products added.")
    else:
        db.rollback()
        print(f"\nDry run: {rep.stored} prices would be stored, {len(rep.created)} products added. Nothing was written; "
              "run again with --apply.")
    return 0


def backup_sqlite() -> None:
    from app import config
    if not config.DATABASE_URL.startswith("sqlite"):
        print("Not SQLite: take your own backup (pg_dump) before relying on this.")
        return
    src_path = Path(config.DATABASE_URL.removeprefix("sqlite:///"))
    if not src_path.exists():
        return
    dest_dir = config.DATA_DIR / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"quotes-before-pricing-{dt.datetime.now():%Y%m%d-%H%M%S}.db"
    src, dst = sqlite3.connect(src_path), sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close(); dst.close()
    print("Backup written:", dest)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Import the PRICING CHART workbook into the quotation database.")
    ap.add_argument("workbook", type=Path)
    ap.add_argument("--apply", action="store_true", help="write to the database (default: dry run, report only)")
    ap.add_argument("--ignore-problems", action="store_true", help="with --apply, import the tables that are fine")
    ap.add_argument("--database-url", help="default: the app's own database")
    args = ap.parse_args(argv)
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    sys.path.insert(0, str(ROOT))
    from sqlalchemy import inspect
    from app import migrations
    from app.database import SessionLocal, engine
    if args.apply:
        backup_sqlite()
        migrations.upgrade()
    elif not inspect(engine).has_table("products"):
        sys.exit("The database has no products table yet: start the app once or run scripts/seed.py first.")
    elif "three_year_price" not in {c["name"] for c in inspect(engine).get_columns("product_prices")}:
        sys.exit("The database has not been upgraded for 3-year prices yet: restart the app (it migrates on start), "
                 "then run this again.")
    with SessionLocal() as db:
        return import_workbook(db, args.workbook, args.apply, args.ignore_problems)


if __name__ == "__main__":
    sys.exit(main())

import importlib.util
import sys
from decimal import Decimal as D
from pathlib import Path

import openpyxl
import pytest

from app.models import Product, ProductPrice
from app.services.prices import tier_prices

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_pricing.py"
spec = importlib.util.spec_from_file_location("import_pricing", SCRIPT)
ip = importlib.util.module_from_spec(spec)
sys.modules["import_pricing"] = ip
spec.loader.exec_module(ip)

NEW = ip.attrs("Acme", "Testing", "seesar_csd")


def book(tmp_path, rows):
    """One sheet 'DEMO'; rows is {row number: {column letter: value}}."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Demo"
    for r, cols in rows.items():
        for c, v in cols.items():
            ws[f"{c}{r}"] = v
    path = tmp_path / "chart.xlsx"
    wb.save(path)
    return path


def product(db, name, basis="per_user_year", **prices):
    p = Product(name=name, price_basis=basis)
    p.prices = [ProductPrice(tier=t, users=u, annual_price=D(v)) for t, pr in prices.items() for u, v in pr.items()]
    db.add(p)
    db.flush()
    return p


def get(db, name):
    return db.query(Product).filter(Product.name == name).one()


@pytest.fixture()
def demo_tables(monkeypatch):
    tables = [
        *ip.pair("DEMO", 2, "USERS", ip.counted("Alpha", unlimited=("Alpha - Unlimited", "Unlimited")), NEW,
                 left=("B", "C"), right=("E", "F")),
        ip.Table("DEMO", ip.COMMERCIAL, 10, "B", "C", "CORES", ip.licence(ip.cores("Beta")), NEW),
    ]
    monkeypatch.setattr(ip, "TABLES", tables)
    return tables


DEMO = {
    2: {"B": "USERS", "C": "QUOTE PRICE (1 YEAR)", "E": "USERS", "F": "QUOTE PRICE (1 YEAR)"},
    3: {"B": 1, "C": 100.0, "E": 1, "F": 300.0},
    4: {"B": 2, "C": 190.5, "E": 2, "F": 0},                 # 0 = formula not filled in: no price
    5: {"B": 3, "C": 250, "E": 3},                           # blank
    6: {"B": 30, "C": 900, "E": 30, "F": 2000},              # beyond the 25 users the app stores
    7: {"B": "Unlimited", "C": 5000, "E": "Unlimited", "F": 9000},
    10: {"B": "CORES", "C": "QUOTE PRICE (1 YEAR)"},
    11: {"B": 32, "C": 1111.115},
    12: {"B": "INFINTE", "C": 2222},
}


def test_dry_run_changes_nothing(db, tmp_path, demo_tables):
    product(db, "Alpha", Academic={1: 1, 2: 2, 3: 3, 4: 4}, Commercial={1: 1, 2: 2})
    db.commit()
    assert ip.import_workbook(db, book(tmp_path, DEMO), apply=False) == 0
    assert db.query(Product).count() == 1 and tier_prices(get(db, "Alpha"))["Academic"] == {1: 1, 2: 2, 3: 3, 4: 4}


def test_replaces_each_price_list_and_creates_missing_products(db, tmp_path, demo_tables):
    product(db, "Alpha", Academic={1: 1, 2: 2, 3: 3, 4: 4}, Commercial={1: 1, 2: 2, 3: 3})
    db.commit()
    assert ip.import_workbook(db, book(tmp_path, DEMO), apply=True) == 0

    alpha = tier_prices(get(db, "Alpha"))
    assert alpha["Academic"] == {1: D("100"), 2: D("190.50"), 3: D("250")}   # users 4 (dummy) gone, 30 not storable
    assert alpha["Commercial"] == {1: D("300"), }                            # 0 and blank cells mean no price

    unl = get(db, "Alpha - Unlimited")                                        # an 'Unlimited' row is a licence product
    assert (unl.price_basis, unl.default_users, unl.vendor, unl.block_keys) == ("per_license_year", "Unlimited", "Acme", ["seesar_csd"])
    assert unl.price_for("Academic", "Unlimited") == D("5000") and unl.price_for("Commercial", "Unlimited") == D("9000")

    cores = get(db, "Beta (32 Cores)")
    assert cores.price_basis == "per_license_year" and cores.price_for("Commercial", "Floating") == D("1111.12")
    assert tier_prices(get(db, "Beta (Infinite Cores)"))["Commercial"] == {1: D("2222")}
    assert tier_prices(get(db, "Beta (32 Cores)"))["Academic"] == {}


def test_products_missing_from_the_sheet_are_left_blank(db, tmp_path, demo_tables, capsys):
    product(db, "Gamma", Academic={1: 5}, Commercial={1: 6})
    product(db, "Free", basis="complimentary")
    db.commit()
    ip.import_workbook(db, book(tmp_path, DEMO), apply=True)
    assert get(db, "Gamma").prices == [] and "Gamma" in capsys.readouterr().out
    # a sheet with no usable price at all clears nothing
    product(db, "Delta", Academic={1: 5})
    db.commit()
    empty = {2: DEMO[2], 3: {"B": 1}}
    ip.import_workbook(db, book(tmp_path, empty), apply=True)
    assert tier_prices(get(db, "Delta"))["Academic"] == {1: 5}


def test_rerun_is_a_no_op(db, tmp_path, demo_tables):
    path = book(tmp_path, DEMO)
    ip.import_workbook(db, path, apply=True)
    before = {(p.name, x.tier, x.users, x.annual_price) for p in db.query(Product) for x in p.prices}
    ip.import_workbook(db, path, apply=True)
    assert before == {(p.name, x.tier, x.users, x.annual_price) for p in db.query(Product) for x in p.prices}


def test_licence_product_keeps_one_price_and_complimentary_none(db, tmp_path, demo_tables, capsys):
    product(db, "Alpha", basis="per_license_year", Academic={1: 7})
    db.commit()
    ip.import_workbook(db, book(tmp_path, DEMO), apply=True)
    assert tier_prices(get(db, "Alpha"))["Academic"] == {1: D("100")}        # 2 and 3 users cannot be stored on a licence
    assert "a licence has a single price" in capsys.readouterr().out


def test_moved_header_stops_the_import(db, tmp_path, demo_tables, capsys):
    rows = {**DEMO, 2: {**DEMO[2], "C": "LIST PRICE"}}                        # the Academic price column is not the quote price
    product(db, "Alpha", Academic={1: 1})
    db.commit()
    assert ip.import_workbook(db, book(tmp_path, rows), apply=True) == 1
    out = capsys.readouterr().out
    assert "layout changed" in out and "Nothing was written" in out
    assert tier_prices(get(db, "Alpha"))["Academic"] == {1: 1} and db.query(Product).count() == 1   # not even the new products

    assert ip.import_workbook(db, book(tmp_path, rows), apply=True, ignore_problems=True) == 0
    assert tier_prices(get(db, "Alpha"))["Commercial"] == {1: D("300")}       # the intact tables were imported


def test_error_cell_and_unknown_row_label_are_problems(db, tmp_path, demo_tables, capsys):
    rows = {**DEMO, 3: {**DEMO[3], "C": "#VALUE!"}, 12: {"B": "lots", "C": 5}}
    assert ip.import_workbook(db, book(tmp_path, rows), apply=True) == 1
    out = capsys.readouterr().out
    assert "shows '#VALUE!'" in out and "expected a number, found 'lots'" in out
    assert db.query(Product).count() == 0


def test_scaling_per_user_and_multi_year_prices(db, tmp_path, monkeypatch):
    monkeypatch.setattr(ip, "TABLES", [
        ip.Table("DEMO", ip.ACADEMIC, 2, "B", "D", "PRODUCTS", lambda k: ip.Target(f"P {k}"), NEW, users_col="C", per_user=True),
        ip.Table("DEMO", ip.COMMERCIAL, 2, "F", "G", "USERS", lambda k: ip.Target("Bundle", licence=True), NEW,
                 price_title="QUOTE PRICE (3 YEARS)", years=3),
    ])
    rows = {2: {"B": "PRODUCTS", "C": "BASE USERS", "D": "QUOTE PRICE (1 YEAR)", "F": "USERS", "G": "QUOTE PRICE (3 YEARS)"},
            3: {"B": "ONE", "C": 3, "D": 1000, "F": 1, "G": 1000}}
    ip.import_workbook(db, book(tmp_path, rows), apply=True)
    assert tier_prices(get(db, "P ONE"))["Academic"] == {3: D("3000")}        # 3 base users x the per-user price
    bundle = get(db, "Bundle")                                                # the sheet has only a 3-year price: kept as that
    assert (bundle.term_price("Commercial", 1, 3), bundle.price_for("Commercial", 1)) == (D("1000"), D("333.33"))


def test_every_route_target_is_valid_for_the_app():
    """Product names TABLES can produce must pass the app's own product validation (catalog blocks, price basis ...)."""
    from app.services.tables import SPECS
    for t in ip.TABLES:
        for key in ("1", "5", "20000", "8 GPU", "INFINITE", "Unlimited", "Additional", "ANALYTICS STANDARD", "1 Million credits"):
            try:
                target = t.route(key)
            except ValueError:
                continue
            raw = {**t.new, "name": target.product, "price_basis": "per_license_year" if target.licence else "per_user_year",
                   "default_users": target.users_label if target.licence else "1"}
            _, errors = SPECS["products"].clean(None, raw)
            assert not errors, (t.label, key, errors)


def test_three_year_column_is_stored_as_given_not_as_three_times_annual(db, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ip, "TABLES", [
        ip.Table("DEMO", ip.ACADEMIC, 2, "B", "C", "USERS", ip.counted("Alpha"), NEW, price3_col="D"),
        ip.Table("DEMO", ip.COMMERCIAL, 2, "F", "G", "USERS", ip.counted("Alpha"), NEW),     # no 3-year column
    ])
    head = {"B": "USERS", "C": "QUOTE PRICE (1 YEAR)", "D": "QUOTE PRICE (3 YEARS)", "F": "USERS", "G": "QUOTE PRICE (1 YEAR)"}
    rows = {2: head, 3: {"B": 1, "C": 100, "D": 250, "F": 1, "G": 400}, 4: {"B": 2, "C": 190, "D": None, "F": 2, "G": 700},
            5: {"B": 3, "C": None, "D": 600}}
    product(db, "Alpha", Academic={1: 1, 2: 2}, Commercial={1: 1})
    get(db, "Alpha").prices[0].three_year_price = D("9")                                     # a stale 3-year price
    db.commit()
    assert ip.import_workbook(db, book(tmp_path, rows), apply=True) == 0
    alpha = get(db, "Alpha")
    assert alpha.term_price("Academic", 1, 3) == D("250") and alpha.term_price("Academic", 1, 1) == D("100")
    assert alpha.term_price("Academic", 2, 3) is None and alpha.term_price("Academic", 2, 1) == D("190")   # blank 3-year cell
    assert alpha.term_price("Commercial", 1, 3) is None and alpha.term_price("Commercial", 1, 1) == D("400")  # never annual x 3
    out = capsys.readouterr().out
    assert "a 3-year price without a 1-year price" in out and "[3-yr 1]" in out
    assert alpha.price_for("Academic", 3) is None


def test_moved_three_year_header_stops_the_import(db, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ip, "TABLES", [ip.Table("DEMO", ip.ACADEMIC, 2, "B", "C", "USERS", ip.counted("Alpha"), NEW, price3_col="D")])
    rows = {2: {"B": "USERS", "C": "QUOTE PRICE (1 YEAR)", "D": "LIST PRICE"}, 3: {"B": 1, "C": 100, "D": 250}}
    assert ip.import_workbook(db, book(tmp_path, rows), apply=True) == 1
    assert "QUOTE PRICE (3 YEAR' in D2" in capsys.readouterr().out

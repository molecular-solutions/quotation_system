import io
from decimal import Decimal as D
from pathlib import Path

import openpyxl
import pytest
from alembic import command
from sqlalchemy import create_engine, text

from app import migrations
from app.models import Product, ProductPrice
from app.services import importer, prices
from app.services.tables import SPECS
from tests.test_api import payload, seed

ROOT = Path(__file__).resolve().parents[1]


def test_price_row_is_the_total_for_that_many_users_and_has_no_formula(authed):
    ids = seed(authed)
    d = authed.get(f"/api/prices/{ids['SeeSAR']}").json()
    assert d["prices"]["Academic"] == {"1": 1000.0, "3": 2400.0, "25": 25000.0} and d["max_users"] == 25
    assert any("no price for 2" in w for w in d["warnings"]["Academic"])           # a hole between 1 and 3 users is flagged
    r = authed.put(f"/api/prices/{ids['SeeSAR']}/Academic", json={"prices": {"1": "1,000", "2": "1900", "3": "", "4": 2000, "25": 25000}})
    assert r.status_code == 200 and r.json()["prices"]["Academic"] == {"1": 1000.0, "2": 1900.0, "4": 2000.0, "25": 25000.0}
    q = authed.post("/api/quotes", json={**payload(ids), "lines": [{"product_id": ids["SeeSAR"], "years": 2, "users_label": "2"}]}).json()
    assert q["lines"][0]["unit_price"] == 1900.0 and q["lines"][0]["amount"] == 3800.0   # 2 users = 1,900, not 2 x 1,000


def test_price_validation(authed):
    ids = seed(authed)
    put = lambda pid, tier, body: authed.put(f"/api/prices/{pid}/{tier}", json={"prices": body})
    r = put(ids["SeeSAR"], "Academic", {"26": 1, "0": 1, "x": 1, "2": "abc", "3": -5})
    assert r.status_code == 422 and set(r.json()["detail"]["errors"]) == {"26", "0", "x", "2", "3"}
    assert authed.get(f"/api/prices/{ids['SeeSAR']}").json()["prices"]["Academic"]["25"] == 25000.0   # nothing half-saved
    assert put(ids["Retro"], "Academic", {"2": 10}).status_code == 422                                # a licence has one price
    assert put(ids["SeeSAR"], "Gold", {"1": 1}).status_code == 422
    authed.post("/api/data/products", json={"name": "Free", "price_basis": "complimentary"})
    free = {p["name"]: p["id"] for p in authed.get("/api/products").json()}["Free"]
    assert authed.get(f"/api/prices/{free}").status_code == 404 and put(free, "Academic", {"1": 1}).status_code == 404


def test_prices_are_admin_only(authed, make_client):
    user = make_client()
    assert user.get("/api/prices").status_code == 403
    assert user.get("/prices", follow_redirects=False).status_code == 303


def _book(sheets):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


def test_upload_matrix_and_long_files_with_row_errors(authed):
    ids = seed(authed)
    book = _book({"Academic": [["Product Name", "Vendor", 1, 2, 3], ["SeeSAR", "", 1000, 1900, None], ["Nope", "", 5, 6, 7], ["Retro", "", 450, 900, None]],
                  "Commercial": [["Product Name", 1, 2], ["SeeSAR", 3000, 5800]]})
    up = lambda name, data, commit=False: authed.post(f"/api/prices/upload?commit={str(commit).lower()}", files={"file": (name, data)})
    r = up("p.xlsx", book).json()
    assert (r["ok_count"], r["failed_count"], r["committed"]) == (5, 4, False)
    assert {p["product"] for p in r["problems"]} == {"Nope", "Retro"}        # unknown product; Retro is a licence (1 user only)
    assert authed.get(f"/api/prices/{ids['SeeSAR']}").json()["prices"]["Academic"].get("2") is None   # preview stores nothing
    r = up("p.xlsx", book, True).json()
    assert r["imported"] == 5
    d = authed.get(f"/api/prices/{ids['SeeSAR']}").json()["prices"]
    assert d["Academic"] == {"1": 1000.0, "2": 1900.0, "3": 2400.0, "25": 25000.0} and d["Commercial"]["2"] == 5800.0   # others untouched
    r = up("p.csv", b"Product Name,Tier,Users,Annual Price\nSeeSAR,academic,4,3100\nSeeSAR,Academic,4,1\nSeeSAR,Academic,99,1\nSeeSAR,Academic,2,x\n").json()
    assert (r["ok_count"], r["failed_count"]) == (1, 3)
    assert up("p.csv", b"foo\n1\n").status_code == 422 and up("p.pdf", b"%PDF").status_code == 422


def test_export_round_trips_through_upload(authed):
    seed(authed)
    x = authed.get("/api/prices/export.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    r = authed.post("/api/prices/upload", files={"file": ("prices.xlsx", x.content)}).json()
    assert r["failed_count"] == 0 and r["ok_count"] == 3 + 2 + 1 + 1 == 7     # SeeSAR 3 + 2, Retro 1 + 1
    csv = authed.get("/api/prices/export.csv").content.decode("utf-8-sig")
    assert csv.splitlines()[0] == "Product Name,Tier,Users,Annual Price" and "SeeSAR,Academic,25,25000.00" in csv
    r = authed.post("/api/prices/upload", files={"file": ("prices.csv", csv.encode())}).json()
    assert r["failed_count"] == 0 and r["ok_count"] == 7


def test_the_shipped_dummy_prices_cover_every_seeded_product(db):
    spec = SPECS["products"]
    importer.commit(db, spec, importer.preview(db, spec, "products.csv", (ROOT / "seed" / "products.csv").read_bytes()))
    prev = prices.preview(db, "dummy_user_prices.csv", (ROOT / "seed" / "dummy_user_prices.csv").read_bytes())
    unknown = [r for r in prev.bad if "no product with this name" not in "; ".join(r.errors)]
    assert unknown == []                                                    # the only misses are products not in the seed file
    prices.commit(db, prev)
    for p in db.query(Product):
        if p.price_basis == "complimentary":
            assert p.prices == []
            continue
        for tier in ("Academic", "Commercial"):
            got = {x.users: x.annual_price for x in p.prices if x.tier == tier}
            want = {1} if p.price_basis == "per_license_year" else set(range(1, 26))
            assert set(got) == want, (p.name, tier)
            assert all(got[n + 1] > got[n] for n in range(1, len(got))), (p.name, tier)   # more users, higher price


def test_migration_keeps_issued_totals_merges_duplicates_and_keeps_old_prices(tmp_path, monkeypatch):
    from app import config
    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setattr(config, "DATABASE_URL", url)
    cfg = migrations.alembic_config()
    command.upgrade(cfg, "0006")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO products (id,name,vendor,segment,category,unit,description,line_label,deployment,default_users,price_basis,active) VALUES "
                       "(1,'SeeSAR with YASARA Plugin','BioSolveIT','','Software','License','','SeeSAR with YASARA Plugin','Local','1','per_user_year',1),"
                       "(2,'SeeSAR','','','Software','License','','','','1','per_user_year',1),"
                       "(3,'Other','','','Software','License','','','','1','per_user_year',1)"))
        c.execute(text("INSERT INTO product_catalog_links (product_id, block_key) VALUES (1,'seesar_csd')"))
        c.execute(text("INSERT INTO product_prices (product_id, tier, annual_price) VALUES (1,'Academic',1000),(3,'Academic',500)"))
        c.execute(text("INSERT INTO quotes (id,number,seq,revision,date,valid_until,status,tier,customer_name,customer_address,customer_email,discount_pct,gst_mode,gst_pct,"
                       "payment_terms,prepared_by,list_total,discount_amount,subtotal,tax_amount,total,created_at,updated_at) VALUES "
                       "(1,'MS-AB-1',1,0,'2026-01-01','2026-01-31','Draft','Academic','X','','',0,'IGST',18,'','',75000,0,75000,13500,88500,'2026-01-01','2026-01-01')"))
        c.execute(text("INSERT INTO quote_lines (quote_id,sort,product_id,label,deployment,users_label,qty,years,unit_price,amount,complimentary,block_keys,description) VALUES "
                       "(1,0,1,'SeeSAR','Local','25',25,3,1000,75000,0,'','')"))
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        assert [r[0] for r in c.execute(text("SELECT name FROM products ORDER BY id"))] == ["SeeSAR", "Other"]
        assert c.execute(text("SELECT vendor FROM products WHERE name='SeeSAR'")).scalar() == "BioSolveIT"      # filled in from the removed row
        assert c.execute(text("SELECT block_key FROM product_catalog_links WHERE product_id=2")).scalar() == "seesar_csd"
        assert c.execute(text("SELECT product_id FROM quote_lines")).scalar() == 2                               # re-pointed
        assert c.execute(text("SELECT product_id, tier, users, annual_price FROM product_prices")).fetchall() == [(3, "Academic", 1, 500)]
        unit, amount = c.execute(text("SELECT unit_price, amount FROM quote_lines")).one()
        assert (float(unit), float(amount)) == (25000.0, 75000.0)       # now the annual price for 25 users; the total is unchanged

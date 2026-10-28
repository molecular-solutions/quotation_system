import io
from pathlib import Path

import openpyxl
import pytest

from app.models import Product
from app.services import importer
from app.services.tables import SPECS

REF_CSV = Path(__file__).resolve().parents[1] / "reference" / "product_import_template.csv"


def test_existing_nine_column_csv_imports(db):
    spec = SPECS["products"]
    prev = importer.preview(db, spec, "product_import_template.csv", REF_CSV.read_bytes())
    assert prev.failed_count == 0 and prev.ok_count == 8
    ok, failed = importer.commit(db, spec, prev)
    assert (ok, failed) == (8, 0)
    p = db.query(Product).filter_by(name="ChemAIRS - Retrosynthesis").one()
    assert p.name == "ChemAIRS - Retrosynthesis" and p.active
    assert p.prices == []                      # the old one-price columns are ignored; prices live on the Prices page
    assert {"Academic Price", "Commercial Price"} <= set(prev.ignored_columns)


def test_row_errors_are_reported_and_good_rows_still_import(db):
    csv = (b"Product Name,Deployment,Default Users,Currency,Active\n"
           b"Good,Cloud,1,INR,Y\n"
           b",Cloud,1,INR,Y\n"
           b"Bad users,Cloud,1,INR,Y\n"
           b"Dollar,Cloud,1,USD,Y\n"
           b"good,Cloud,1,INR,Y\n"
           b"Bad flag,Cloud,1,INR,maybe\n")
    prev = importer.preview(db, SPECS["products"], "x.csv", csv)
    bad = {r.n: r.errors for r in prev.rows if r.errors}
    assert set(bad) == {3, 5, 6, 7}
    assert bad[3]["name"] == "required" and "currency" in bad[5]
    assert "duplicate" in bad[6]["_row"] and "active" in bad[7]
    ok, failed = importer.commit(db, SPECS["products"], prev)
    assert (ok, failed) == (2, 4) and db.query(Product).count() == 2


def test_reupload_updates_and_blank_cell_keeps_value(db):
    spec = SPECS["products"]
    importer.commit(db, spec, importer.preview(db, spec, "a.csv", b"Product Name,Deployment\nOne,Cloud\n"))
    importer.commit(db, spec, importer.preview(db, spec, "a.csv", b"Product Name,Deployment,Vendor\none,,Acme\n"))
    p = db.query(Product).one()
    assert (p.name, p.vendor, p.deployment) == ("One", "Acme", "Cloud")  # matched by name, case-insensitively; blank keeps


def test_xlsx_upload_picks_matching_sheet(db):
    wb = openpyxl.Workbook()
    wb.active.title = "readme"
    ws = wb.create_sheet("products")
    ws.append(["Product Name", "Vendor", "Catalog Blocks"])
    ws.append(["Xtal", "XtalPi", "xfep, xmolgen"])
    buf = io.BytesIO(); wb.save(buf)
    spec = SPECS["products"]
    prev = importer.preview(db, spec, "p.xlsx", buf.getvalue())
    assert prev.failed_count == 0
    importer.commit(db, spec, prev)
    p = db.query(Product).one()
    assert p.block_keys == ["xfep", "xmolgen"] and p.vendor == "XtalPi"


def test_unknown_catalog_block_and_missing_header(db):
    prev = importer.preview(db, SPECS["products"], "x.csv", b"Product Name,Catalog Blocks\nB,nope\n")
    assert "unknown block" in prev.rows[0].errors["catalog_blocks"]
    with pytest.raises(importer.ImportError_):
        importer.preview(db, SPECS["products"], "x.csv", b"Foo,Bar\n1,2\n")
    with pytest.raises(importer.ImportError_):
        importer.preview(db, SPECS["products"], "x.pdf", b"%PDF")


def test_customers_and_packages(db):
    importer.commit(db, SPECS["customers"], importer.preview(
        db, SPECS["customers"], "c.csv",
        b"Name,Department,Institution,Email,Type\nDr. Adam,Biophysics,AIIMS,adam@example.com,academic\nBad,,,not-an-email,\n"))
    assert db.query(__import__("app.models", fromlist=["Customer"]).Customer).count() == 1
    importer.commit(db, SPECS["products"], importer.preview(db, SPECS["products"], "p.csv", b"Product Name\nOne\n"))
    prev = importer.preview(db, SPECS["packages"], "k.csv",
                            b"Package,Product Name,Users,Years\nAcademic Bundle,One,5,3\nAcademic Bundle,Nope,1,1\n")
    assert prev.rows[1].errors["product"].startswith("no product")
    importer.commit(db, SPECS["packages"], prev)
    rows = SPECS["packages"].rows(db)
    assert rows[0]["package"] == "Academic Bundle" and rows[0]["users"] == "5" and rows[0]["years"] == 3


def test_shipped_seed_file_is_valid(db):
    seed = Path(__file__).resolve().parents[1] / "seed" / "products.csv"
    prev = importer.preview(db, SPECS["products"], "products.csv", seed.read_bytes())
    assert prev.failed_count == 0, [(r.n, r.errors) for r in prev.rows if r.errors]
    importer.commit(db, SPECS["products"], prev)
    names = {p.name.lower() for p in db.query(Product)}
    assert "seesar" in names and "infinisee" in names                       # one of each, no vendor-labelled duplicates
    assert "seesar with yasara plugin" not in names and "infinisee - three search modes" not in names
    assert db.query(Product).filter_by(name="XFEP & XMolGen").one().block_keys == ["xfep", "xmolgen"]

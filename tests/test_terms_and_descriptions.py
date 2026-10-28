"""Product names as the key, descriptions under product names, editable Terms & Conditions."""
import io
import zipfile

from tests.test_api import payload, seed


def doc_xml(client, quote_id):
    r = client.get(f"/api/quotes/{quote_id}/docx")
    return zipfile.ZipFile(io.BytesIO(r.content)).read("word/document.xml").decode()


# ── Product ID is gone; the name is the key ──────────────────────────────
def test_products_have_no_product_id_anywhere(authed):
    seed(authed)
    cols = [c["key"] for c in authed.get("/api/data/tables").json()[0]["columns"]]
    assert "code" not in cols and cols[0] == "name"
    row = authed.get("/api/data/products").json()[0]
    assert "code" not in row and "code" not in authed.get("/api/products").json()[0]
    assert "Product ID" not in authed.get("/api/data/products/template.csv").text


def test_grid_rejects_duplicate_names_but_allows_editing_a_row(authed):
    seed(authed)
    dup = authed.post("/api/data/products", json={"name": "seesar"})
    assert dup.status_code == 422 and "already exists" in dup.json()["detail"]["errors"]["name"]
    a, b = authed.get("/api/data/products").json()
    a["name"] = b["name"]
    assert authed.put(f"/api/data/products/{a['id']}", json=a).status_code == 422       # renaming onto another product
    a["name"], a["vendor"] = "SeeSAR renamed", "BioSolveIT"
    assert authed.put(f"/api/data/products/{a['id']}", json=a).json()["name"] == "SeeSAR renamed"


def test_old_csv_with_a_product_id_column_still_uploads(authed):
    csv = b"Product ID,Product Name,Academic Price\nZZ-1,Alpha,100\n"
    r = authed.post("/api/data/products/upload?commit=true", files={"file": ("p.csv", csv, "text/csv")}).json()
    assert r["imported"] == 1 and "Product ID" in r["ignored_columns"]


# ── description under the product name ───────────────────────────────────
def test_description_prints_under_the_product_name_and_is_frozen(authed):
    ids = seed(authed)
    sees = next(p for p in authed.get("/api/data/products").json() if p["name"] == "SeeSAR")
    sees["description"] = "Structure-based drug design platform"
    authed.put(f"/api/data/products/{sees['id']}", json=sees)
    q = authed.post("/api/quotes", json=payload(ids)).json()
    assert q["lines"][0]["description"] == "Structure-based drug design platform"
    xml = doc_xml(authed, q["id"])
    assert xml.index("SeeSAR with YASARA Plugin") < xml.index("Structure-based drug design platform")
    # editing the catalogue later does not change the issued quotation
    sees["description"] = "Something else"
    authed.put(f"/api/data/products/{sees['id']}", json=sees)
    assert "Structure-based drug design platform" in doc_xml(authed, q["id"])
    # a line without a description prints none; a typed one overrides the product's
    body = payload(ids)
    body["lines"][0]["description"] = ""
    body["lines"][1]["description"] = "Typed on the quote"
    q2 = authed.post("/api/quotes", json=body).json()
    xml2 = doc_xml(authed, q2["id"])
    assert "Something else" not in xml2 and "Typed on the quote" in xml2


# ── Terms & Conditions ───────────────────────────────────────────────────
def test_terms_default_then_edit_then_freeze(authed):
    ids = seed(authed)
    t = authed.get("/api/settings/terms").json()
    assert len(t["items"]) == 6 and t["items"] == t["default"]
    q1 = authed.post("/api/quotes", json=payload(ids)).json()
    assert "Delivery Timeline: Installation" in doc_xml(authed, q1["id"])

    items = [{"text": "Validity: valid till **{validity_date}**.", "style": "bullet"},
             {"text": "Delivery in 4 weeks. Contact sales@example.com", "style": "bullet"},
             {"text": "Payment Terms: =={payment_terms}==", "style": "indent"}]
    assert authed.put("/api/settings/terms", json={"items": items}).status_code == 200
    q2 = authed.post("/api/quotes", json=payload(ids)).json()
    xml = doc_xml(authed, q2["id"])
    assert "Delivery in 4 weeks" in xml and "Delivery Timeline: Installation" not in xml
    assert "15 days from the date of Invoice" in xml and "{validity_date}" not in xml and "**" not in xml
    assert "Support: Comprehensive" not in xml
    # the earlier quotation keeps the terms it was issued with
    assert "Delivery Timeline: Installation" in doc_xml(authed, q1["id"])

    assert authed.put("/api/settings/terms", json={"items": t["default"]}).status_code == 200
    assert authed.post("/api/quotes", json=payload(ids)).status_code == 201


def test_terms_validation_and_permissions(authed, make_client):
    bad = lambda items: authed.put("/api/settings/terms", json={"items": items})
    assert bad([]).status_code == 422
    assert bad([{"text": "  ", "style": "bullet"}]).status_code == 422
    assert bad([{"text": "x", "style": "weird"}]).status_code == 422
    assert bad([{"text": "x"}] * 21).status_code == 422
    asha = make_client()
    assert asha.get("/api/settings/terms").status_code == 200
    assert asha.put("/api/settings/terms", json={"items": [{"text": "x", "style": "bullet"}]}).status_code == 403


def test_terms_markup_parses_formatting_and_placeholders():
    from app.services import terms
    runs = terms.parse("A **bold** __it__ ==hi== mail me@x.in {license_term}", {"license_term": "3 years"})
    flags = [(r["text"], r["bold"], r["italic"], r["highlight"], r["link"]) for r in runs]
    assert ("bold", True, False, False, False) in flags and ("it", False, True, False, False) in flags
    assert ("hi", False, False, True, False) in flags and ("me@x.in", False, False, False, True) in flags
    assert runs[-1]["text"] == " 3 years"


def test_saving_a_product_that_has_catalog_blocks_works(authed):
    """Regression: re-saving the same blocks used to violate the (product, block) unique constraint -> row edits failed."""
    seed(authed)
    for _ in range(2):
        row = next(p for p in authed.get("/api/data/products").json() if p["name"] == "SeeSAR")
        assert row["catalog_blocks"] == "seesar_csd"
        row["vendor"] = "BioSolveIT"
        assert authed.put(f"/api/data/products/{row['id']}", json=row).status_code == 200
    row["catalog_blocks"] = "seesar_csd, yasara"
    assert authed.put(f"/api/data/products/{row['id']}", json=row).json()["catalog_blocks"] == "seesar_csd, yasara"
    row["catalog_blocks"] = ""
    assert authed.put(f"/api/data/products/{row['id']}", json=row).json()["catalog_blocks"] == ""


def test_rebuilt_terms_keep_run_properties_in_word_order(authed):
    """Bold/italic/highlight must sit after rFonts inside <w:rPr>, or Word/LibreOffice drop the font."""
    import re
    ids = seed(authed)
    authed.put("/api/settings/terms", json={"items": [
        {"text": "**__Bold italic__** and ==marked== mail a@b.in", "style": "bullet"}]})
    q = authed.post("/api/quotes", json=payload(ids)).json()
    xml = doc_xml(authed, q["id"])
    runs = [r for r in re.findall(r"<w:r>.*?</w:r>", xml, re.S) if "Bold italic" in r or "marked" in r or "a@b.in" in r]
    assert len(runs) == 3
    for r in runs:
        tags = re.findall(r"<w:(\w+)[ />]", re.search(r"<w:rPr>(.*?)</w:rPr>", r, re.S).group(1))
        assert tags.index("rFonts") < min(tags.index(t) for t in ("b", "i", "highlight", "color") if t in tags)


def test_rebuilt_terms_use_the_body_font_not_the_bullet_symbol_font(authed):
    import re
    ids = seed(authed)
    authed.put("/api/settings/terms", json={"items": [{"text": "Plain **bold** text", "style": "bullet"}]})
    xml = doc_xml(authed, authed.post("/api/quotes", json=payload(ids)).json()["id"])
    run = next(r for r in re.findall(r"<w:r>.*?</w:r>", xml, re.S) if ">bold<" in r)
    assert 'w:ascii="Calibri"' in run and "Noto Sans Symbols" not in run
    bullet = next(r for r in re.findall(r"<w:r>.*?</w:r>", xml, re.S) if ">• <" in r and "Noto Sans Symbols" in r)

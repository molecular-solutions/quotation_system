import io
import shutil
import zipfile

import pytest

needs_soffice = pytest.mark.skipif(not shutil.which("soffice"), reason="needs LibreOffice")


def seed(c):
    csv = (b"Product Name,Quote Label,Deployment,Default Users,Price Basis,Catalog Blocks\n"
           b"SeeSAR,SeeSAR with YASARA Plugin,Local,25,per_user_year,seesar_csd\n"
           b"Retro,Retrosynthesis Modules,Cloud,3,per_license_year,chemairs\n")
    r = c.post("/api/data/products/upload?commit=true", files={"file": ("p.csv", csv, "text/csv")})
    assert r.status_code == 200 and r.json()["imported"] == 2, r.text
    ids = {p["name"]: p["id"] for p in c.get("/api/products").json()}
    for name, tier, prices in (("SeeSAR", "Academic", {"1": 1000, "3": 2400, "25": 25000}),
                               ("SeeSAR", "Commercial", {"1": 3000, "25": 75000}),
                               ("Retro", "Academic", {"1": 500}), ("Retro", "Commercial", {"1": 900})):
        three = {u: v * 3 for u, v in prices.items()}      # quotes default to 3 years; 3 x annual keeps the numbers easy to read
        assert c.put(f"/api/prices/{ids[name]}/{tier}", json={"prices": prices, "prices3": three}).status_code == 200
    return ids


def payload(ids, **kw):
    return {"customer": {"name": "Dr. Adam", "department": "Biophysics", "institution": "AIIMS", "email": "a@x.in"},
            "tier": "Academic", "discount_pct": 10,
            "lines": [{"product_id": ids["SeeSAR"], "years": 3}, {"product_id": ids["Retro"], "years": 1, "users_label": "Floating"}],
            **kw}


def test_auth_gate(client, admin):
    from tests.access import login
    assert client.get("/api/products").status_code == 401           # no token: not through Cloudflare Access
    assert client.get("/", follow_redirects=False).status_code == 401
    assert login(client, "BOSS@example.com ").status_code == 200    # email is case-insensitive
    assert client.get("/api/products").status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 200
    assert client.get("/login", follow_redirects=False).status_code == 404   # no app login page any more


def test_access_token_checks(client, admin):
    from tests.access import KEY, OTHER_KEY, access_token
    ok = lambda **kw: client.get("/api/me", headers={"Cf-Access-Jwt-Assertion": access_token("boss@example.com", **kw)}).status_code
    assert ok() == 200
    assert ok(aud="another-application") == 401                     # a token for some other Access application
    assert ok(exp_in=-3600) == 401                                  # expired
    assert ok(key=OTHER_KEY) == 401                                 # signed by someone else (the key the app trusts is KEY)
    assert ok(iss="https://other.cloudflareaccess.com") == 401
    assert client.get("/api/me", headers={"Cf-Access-Jwt-Assertion": "garbage"}).status_code == 401
    # the plain email headers Cloudflare also sends are not enough on their own: anyone who can reach the container could forge them
    assert client.get("/api/me", headers={"Cf-Access-Authenticated-User-Email": "boss@example.com"}).status_code == 401
    # a service token carries no email
    assert client.get("/api/me", headers={"Cf-Access-Jwt-Assertion": access_token(None, sub="svc")}).status_code == 401


def test_signed_in_but_not_a_user_is_refused(client, admin):
    from tests.access import login
    r = login(client, "stranger@example.com")
    assert r.status_code == 403 and "not set up" in r.json()["detail"]
    page = client.get("/", follow_redirects=False)
    assert page.status_code == 403 and "Ask an administrator" in page.text
    assert client.get("/api/products").status_code == 403


def test_calc_create_and_read(authed):
    ids = seed(authed)
    body = payload(ids)
    calc = authed.post("/api/quotes/calc", json=body).json()
    assert calc["totals"]["list_total"] == 75500.0
    assert [l["amount"] for l in calc["lines"]] == [75000.0, 500.0]
    t = calc["totals"]
    assert t["subtotal"] == t["list_total"] - t["discount_amount"] and t["tax_rows"][0]["label"] == "IGST 18%"
    r = authed.post("/api/quotes", json=body)
    assert r.status_code == 201
    q = r.json()
    assert q["number"] == "MS-AB-175" and q["totals"]["total"] == t["total"]
    assert q["customer"]["department"] == "Biophysics"
    assert authed.get(f"/api/quotes/{q['id']}").json()["lines"][1]["users_label"] == "Floating"
    assert [x["number"] for x in authed.get("/api/quotes").json()] == ["MS-AB-175"]
    assert authed.patch(f"/api/quotes/{q['id']}/status", json={"status": "Sent"}).json()["status"] == "Sent"
    assert authed.delete(f"/api/quotes/{q['id']}").status_code == 409  # not a draft any more


def test_calc_flags_bad_line_but_keeps_going(authed):
    ids = seed(authed)
    authed.post("/api/data/products", json={"name": "No price"})
    ids = {p["name"]: p["id"] for p in authed.get("/api/products").json()}
    body = payload(ids)
    body["lines"].append({"product_id": ids["No price"], "years": 1})
    out = authed.post("/api/quotes/calc", json=body).json()
    assert out["lines"][2]["error"] and out["lines"][0]["amount"] == 75000.0
    r = authed.post("/api/quotes", json=body)
    assert r.status_code == 422 and "no Academic price" in r.json()["detail"]["message"]
    assert authed.get("/api/quotes").json() == []  # nothing half-saved


def test_docx_download_has_right_content(authed):
    ids = seed(authed)
    q = authed.post("/api/quotes", json=payload(ids)).json()
    r = authed.get(f"/api/quotes/{q['id']}/docx")
    assert r.status_code == 200 and "MS-AB-175_Dr_Adam" in r.headers["content-disposition"]
    xml = zipfile.ZipFile(io.BytesIO(r.content)).read("word/document.xml").decode()
    for needle in ("MS-AB-175", "SeeSAR with YASARA Plugin", "Floating", "DISCOUNT", "75,000.00", "Biophysics, AIIMS"):
        assert needle in xml, needle
    assert "DISCOUNT 10%" not in xml and "10%" not in xml.split("DISCOUNT")[1][:600]  # the percentage is backend-only


@needs_soffice
def test_pdf_download_and_unsaved_preview(authed):
    ids = seed(authed)
    r = authed.post("/api/quotes/preview.pdf", json=payload(ids))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert authed.get("/api/quotes").json() == []  # preview stored nothing and used no number
    q = authed.post("/api/quotes", json=payload(ids)).json()
    assert q["number"] == "MS-AB-175"
    pdf = authed.get(f"/api/quotes/{q['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and pdf.headers["content-type"] == "application/pdf"


def test_revise_and_duplicate_endpoints(authed):
    ids = seed(authed)
    q = authed.post("/api/quotes", json=payload(ids)).json()
    assert authed.post(f"/api/quotes/{q['id']}/revise").json()["number"] == "MS-AB-175-R1"
    assert authed.post(f"/api/quotes/{q['id']}/duplicate").json()["number"] == "MS-AB-176"


def test_data_grid_crud_and_validation(authed):
    r = authed.post("/api/data/customers", json={"name": "Dr. Rao", "email": "bad"})
    assert r.status_code == 422 and "email" in r.json()["detail"]["errors"]
    row = authed.post("/api/data/customers", json={"name": "Dr. Rao", "email": "rao@x.in", "tier": "Commercial"}).json()
    row["institution"] = "IISc"
    assert authed.put(f"/api/data/customers/{row['id']}", json=row).json()["institution"] == "IISc"
    assert authed.get("/api/customers?q=iisc").json()[0]["name"] == "Dr. Rao"
    assert authed.delete(f"/api/data/customers/{row['id']}").status_code == 200
    assert authed.get("/api/data/customers").json() == []
    assert authed.post("/api/data/nope", json={}).status_code == 404


def test_upload_preview_does_not_store_and_reports_errors(authed):
    csv = b"Product Name,Active\nOk,Y\nBad,maybe\n"
    r = authed.post("/api/data/products/upload", files={"file": ("p.csv", csv, "text/csv")}).json()
    assert (r["ok_count"], r["failed_count"], r["committed"]) == (1, 1, False)
    assert r["rows"][1]["errors"]["active"] and authed.get("/api/data/products").json() == []
    bad = authed.post("/api/data/products/upload", files={"file": ("p.csv", b"foo\n1\n", "text/csv")})
    assert bad.status_code == 422 and "required column" in bad.json()["detail"]["message"]


def test_template_and_export(authed):
    seed(authed)
    t = authed.get("/api/data/products/template.csv").text.splitlines()
    assert t[0].startswith("Product Name,Category") and len(t) == 2
    x = authed.get("/api/data/products/export.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert authed.get("/api/data/products/export.csv").text.count("\n") == 3


def test_settings_roundtrip_changes_numbering(authed):
    ids = seed(authed)
    assert authed.put("/api/settings", json={"seq_next": "300", "code_Academic": "AC"}).status_code == 200
    assert authed.post("/api/quotes", json=payload(ids)).json()["number"] == "MS-AC-300"
    assert authed.put("/api/settings", json={"number_format": "no-seq"}).status_code == 422


def test_commercial_number_via_api_and_cb_code_is_gone(authed):
    ids = seed(authed)
    assert "code_Commercial" not in authed.get("/api/settings").json()["values"]
    body = {**payload(ids), "tier": "Commercial"}
    assert authed.post("/api/quotes", json=body).status_code == 422
    q = authed.post("/api/quotes", json={**body, "number": "MS-CA-26-058"}).json()
    assert q["number"] == "MS-CA-26-058"
    assert authed.post("/api/quotes", json={**body, "number": "MS-CA-26-058"}).status_code == 422
    u = authed.put(f"/api/quotes/{q['id']}", json={**body, "number": "MS-CA-26-060"})
    assert u.json()["number"] == "MS-CA-26-060"
    assert authed.post(f"/api/quotes/{q['id']}/duplicate").status_code == 422
    assert authed.post(f"/api/quotes/{q['id']}/duplicate", json={"number": "MS-CA-26-061"}).json()["number"] == "MS-CA-26-061"
    p = authed.post("/api/quotes/preview.docx", json={**body, "number": "MS-CA-26-099"})
    assert p.status_code == 200


def test_settings_cannot_reuse_a_used_number(authed):
    ids = seed(authed)
    authed.post("/api/quotes", json=payload(ids))  # uses 175
    vals = authed.get("/api/settings").json()["values"]
    assert vals["seq_next"] == "176"
    assert authed.put("/api/settings", json=vals).status_code == 200  # saving unchanged values is harmless
    assert authed.post("/api/quotes", json=payload(ids)).json()["number"] == "MS-AB-176"
    assert authed.put("/api/settings", json={"seq_next": "170"}).status_code == 422

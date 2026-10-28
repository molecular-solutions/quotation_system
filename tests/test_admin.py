"""Per-user accounts, ownership of quotations and the admin panel API."""
from sqlalchemy import select

from app.models import Quote, User
from app.services import users as users_svc
from tests.conftest import login
from tests.test_api import payload, seed


# ── passwords ─────────────────────────────────────────────────────────────
def test_password_hashing_roundtrip():
    h = users_svc.hash_password("correct horse")
    assert "correct horse" not in h and h.startswith("scrypt$")
    assert users_svc.check_password("correct horse", h)
    assert not users_svc.check_password("wrong", h)
    assert not users_svc.check_password("x", "garbage")
    assert users_svc.hash_password("correct horse") != h  # salted
    assert len(users_svc.generate_password()) == 12


def test_bootstrap_creates_admin_once_and_adopts_old_quotes(db):
    import datetime as dt
    db.add(Quote(number="MS-AB-1", seq=1, date=dt.date.today(), valid_until=dt.date.today(), customer_name="Old"))
    db.commit()
    users_svc.bootstrap_admin(db)
    users_svc.bootstrap_admin(db)
    [admin] = db.scalars(select(User)).all()
    assert admin.role == "admin" and admin.email == "boss@example.com"
    assert db.scalar(select(Quote)).created_by_id == admin.id
    assert users_svc.authenticate(db, "boss@example.com", "test-pass")


# ── admin panel: users ────────────────────────────────────────────────────
def test_only_admin_can_use_admin_api_and_pages(authed, make_client):
    asha = make_client()
    for url in ("/api/admin/users", "/api/admin/quotes"):
        assert asha.get(url).status_code == 403
        assert authed.get(url).status_code == 200
    assert asha.post("/api/admin/users", json={"name": "X", "email": "x@x.in"}).status_code == 403
    assert asha.get("/admin", follow_redirects=False).status_code == 303   # users are sent back to the quotes list
    assert asha.get("/data", follow_redirects=False).status_code == 303
    assert asha.get("/settings", follow_redirects=False).status_code == 303
    assert authed.get("/admin", follow_redirects=False).status_code == 200
    assert asha.get("/account", follow_redirects=False).status_code == 200


def test_create_user_returns_password_once_and_user_can_sign_in(authed, client, make_client):
    r = authed.post("/api/admin/users", json={"name": " Ravi K ", "email": "Ravi@Example.com"})
    assert r.status_code == 201
    u = r.json()
    assert u["email"] == "ravi@example.com" and u["name"] == "Ravi K" and u["role"] == "user" and len(u["password"]) == 12
    assert "password" not in authed.get("/api/admin/users").json()[0]
    from fastapi.testclient import TestClient

    from app.main import app
    ravi = TestClient(app)
    assert login(ravi, "ravi@example.com", u["password"]).status_code == 200
    assert ravi.get("/api/me").json()["name"] == "Ravi K"


def test_create_user_validation(authed):
    post = lambda **kw: authed.post("/api/admin/users", json={"name": "A", "email": "a@x.in", **kw})
    assert post(email="not-an-email").status_code == 422
    assert post(name=" ").status_code == 422
    assert post(role="boss").status_code == 422
    assert post(password="short").status_code == 422
    assert post(password="long-enough-1").status_code == 201
    assert post(email="A@X.in").status_code == 422   # duplicate, case-insensitive


def test_deactivate_blocks_login_and_existing_session(authed, make_client):
    asha = make_client()
    asha_id = next(u["id"] for u in authed.get("/api/admin/users").json() if u["email"] == "asha@example.com")
    assert asha.get("/api/products").status_code == 200
    assert authed.patch(f"/api/admin/users/{asha_id}", json={"active": False}).status_code == 200
    assert asha.get("/api/products").status_code == 401         # session dies immediately
    assert login(asha, "asha@example.com", "asha-pass-1").status_code == 401
    authed.patch(f"/api/admin/users/{asha_id}", json={"active": True})
    assert login(asha, "asha@example.com", "asha-pass-1").status_code == 200


def test_reset_password_signs_user_out_and_changes_login(authed, make_client):
    asha = make_client()
    asha_id = next(u["id"] for u in authed.get("/api/admin/users").json() if u["email"] == "asha@example.com")
    new = authed.post(f"/api/admin/users/{asha_id}/reset-password", json={}).json()["password"]
    assert asha.get("/api/products").status_code == 401
    assert login(asha, "asha@example.com", "asha-pass-1").status_code == 401
    assert login(asha, "asha@example.com", new).status_code == 200
    assert authed.post(f"/api/admin/users/{asha_id}/reset-password", json={"password": "abc"}).status_code == 422


def test_admin_cannot_lock_themselves_out(authed):
    me = authed.get("/api/me").json()
    assert authed.patch(f"/api/admin/users/{me['id']}", json={"active": False}).status_code == 409
    assert authed.patch(f"/api/admin/users/{me['id']}", json={"role": "user"}).status_code == 409
    assert authed.get("/api/me").status_code == 200


def test_cannot_remove_last_active_admin(authed, make_client):
    other = make_client("Other Admin", "o@example.com", "admin")
    ids = {u["email"]: u["id"] for u in authed.get("/api/admin/users").json()}
    assert authed.patch(f"/api/admin/users/{ids['o@example.com']}", json={"active": False}).status_code == 200  # one left: fine
    assert other.get("/api/me").status_code == 401
    # the remaining admin is the signed-in one, already protected by the self-lockout rule
    assert authed.patch(f"/api/admin/users/{ids['boss@example.com']}", json={"role": "user"}).status_code == 409


def test_change_own_password(authed, make_client):
    asha = make_client()
    assert asha.post("/api/me/password", json={"current_password": "wrong", "new_password": "new-password-1"}).status_code == 422
    assert asha.post("/api/me/password", json={"current_password": "asha-pass-1", "new_password": "short"}).status_code == 422
    assert asha.post("/api/me/password", json={"current_password": "asha-pass-1", "new_password": "new-password-1"}).status_code == 200
    assert asha.get("/api/me").status_code == 200                      # this browser stays signed in
    assert login(asha, "asha@example.com", "asha-pass-1").status_code == 401
    assert login(asha, "asha@example.com", "new-password-1").status_code == 200


# ── quotations: ownership and the admin's view of all ─────────────────────
def test_users_see_only_their_quotes_admin_sees_all(authed, make_client):
    ids = seed(authed)
    asha, ravi = make_client(), make_client("Ravi", "ravi@example.com", password="ravi-pass-12")
    qa = asha.post("/api/quotes", json=payload(ids)).json()
    qr = ravi.post("/api/quotes", json=payload(ids)).json()
    assert qa["created_by"] == "Asha" and qa["prepared_by"] == "Asha"

    assert [q["id"] for q in asha.get("/api/quotes").json()] == [qa["id"]]
    assert [q["id"] for q in ravi.get("/api/quotes").json()] == [qr["id"]]
    # another user's quote behaves as if it does not exist, for every action
    for method, url in (("get", f"/api/quotes/{qr['id']}"), ("get", f"/api/quotes/{qr['id']}/docx"),
                        ("put", f"/api/quotes/{qr['id']}"), ("patch", f"/api/quotes/{qr['id']}/status"),
                        ("post", f"/api/quotes/{qr['id']}/revise"), ("post", f"/api/quotes/{qr['id']}/duplicate"),
                        ("delete", f"/api/quotes/{qr['id']}")):
        kw = {"json": payload(ids)} if method == "put" else {"json": {"status": "Sent"}} if method == "patch" else {}
        assert getattr(asha, method)(url, **kw).status_code == 404, (method, url)
    assert ravi.get(f"/api/quotes/{qr['id']}").status_code == 200

    allq = authed.get("/api/admin/quotes").json()
    assert {q["number"]: q["created_by"] for q in allq} == {qa["number"]: "Asha", qr["number"]: "Ravi"}
    only_ravi = authed.get(f"/api/admin/quotes?user_id={qr['created_by_id']}").json()
    assert [q["id"] for q in only_ravi] == [qr["id"]]
    assert authed.get("/api/admin/quotes?q=zzz").json() == []
    # the admin can open anyone's quote
    assert authed.get(f"/api/quotes/{qa['id']}").status_code == 200
    assert authed.get("/api/quotes").json() == []                     # their own list is just their own quotes


def test_revision_stays_with_owner_copy_goes_to_copier(authed, make_client):
    ids = seed(authed)
    asha = make_client()
    q = asha.post("/api/quotes", json=payload(ids)).json()
    rev = authed.post(f"/api/quotes/{q['id']}/revise").json()
    assert rev["created_by"] == "Asha"
    copy = authed.post(f"/api/quotes/{q['id']}/duplicate").json()
    assert copy["created_by"] == "Boss" and copy["prepared_by"] == "Boss"


def test_catalog_and_settings_edits_are_admin_only(authed, make_client):
    asha = make_client()
    assert asha.post("/api/data/products", json={"code": "Z", "name": "Z"}).status_code == 403
    assert asha.put("/api/settings", json={"validity_days": "10"}).status_code == 403
    assert asha.get("/api/settings").status_code == 200       # the builder still reads defaults
    assert asha.get("/api/products").status_code == 200

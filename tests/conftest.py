import os
import tempfile

os.environ.setdefault("QUOTE_DATA_DIR", tempfile.mkdtemp(prefix="quote-test-"))
os.environ["QUOTE_ADMIN_EMAIL"] = "boss@example.com"
os.environ["QUOTE_CF_TEAM_DOMAIN"] = "team.cloudflareaccess.com"
os.environ["QUOTE_CF_AUD"] = "test-aud"

from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (registers tables on Base.metadata)
from app.database import Base
from tests.access import KEY, login  # noqa: F401  (login is re-exported for older imports)


@pytest.fixture(autouse=True)
def cloudflare_keys(monkeypatch):
    from app import auth
    monkeypatch.setattr(auth, "_keys", lambda: SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=KEY.public_key())))


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def admin(db):
    from app.services import users
    return users.create_user(db, "Boss", "boss@example.com", "admin")


@pytest.fixture()
def authed(client, admin):
    """A client signed in as the administrator."""
    assert login(client, "boss@example.com").status_code == 200
    return client


@pytest.fixture()
def make_client(db, client):
    """Factory for extra signed-in clients (separate cookie jars): make_client(name, email, role) -> TestClient."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import users

    def make(name="Asha", email="asha@example.com", role="user"):
        users.create_user(db, name, email, role)
        c = TestClient(app)
        assert login(c, email).status_code == 200
        return c
    return make

import os
import tempfile

os.environ.setdefault("QUOTE_DATA_DIR", tempfile.mkdtemp(prefix="quote-test-"))
os.environ["QUOTE_ADMIN_EMAIL"] = "boss@example.com"
os.environ["QUOTE_ADMIN_PASSWORD"] = "test-pass"
os.environ["QUOTE_SECRET_KEY"] = "test-secret"

import pytest
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (registers tables on Base.metadata)
from app.database import Base


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

    from app import auth
    from app.database import get_db
    from app.main import app

    auth._attempts.clear()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def login(client, email, password):
    from app import auth
    auth._attempts.clear()
    return client.post("/api/login", json={"email": email, "password": password})


@pytest.fixture()
def admin(db):
    from app.services import users
    return users.create_user(db, "Boss", "boss@example.com", "admin", "test-pass")[0]


@pytest.fixture()
def authed(client, admin):
    """A client signed in as the administrator."""
    assert login(client, "boss@example.com", "test-pass").status_code == 200
    return client


@pytest.fixture()
def make_client(db, client):
    """Factory for extra signed-in clients (separate cookie jars): make_client(name, email, role) -> TestClient."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import users

    def make(name="Asha", email="asha@example.com", role="user", password="asha-pass-1"):
        users.create_user(db, name, email, role, password)
        c = TestClient(app)
        assert login(c, email, password).status_code == 200
        return c
    return make

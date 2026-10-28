"""Accounts: password hashing, creation, login check, first-admin bootstrap."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import re
import secrets

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.models import ROLES, Quote, User

MIN_PASSWORD = 8
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/l/I to avoid misreading
_N, _R, _P = 2 ** 14, 8, 1


class UserError(ValueError):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b = lambda x: base64.b64encode(x).decode()
    return f"scrypt${_N}${_R}${_P}${b(salt)}${b(h)}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


_DUMMY = hash_password("not-a-real-password")  # lets an unknown email cost the same time as a wrong password


def generate_password(length: int = 12) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not _EMAIL.match(email):
        raise UserError("enter a valid email address")
    return email


def check_new_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise UserError(f"password must be at least {MIN_PASSWORD} characters")
    return password


def create_user(db: Session, name: str, email: str, role: str = "user", password: str | None = None) -> tuple[User, str]:
    """Returns the user and the plain password (shown once to whoever creates the account)."""
    name = (name or "").strip()
    if not name:
        raise UserError("name is required")
    if role not in ROLES:
        raise UserError(f"role must be one of {ROLES}")
    email = clean_email(email)
    if db.scalar(select(User.id).where(User.email == email)):
        raise UserError("a user with this email already exists")
    password = check_new_password(password) if password else generate_password()
    user = User(name=name, email=email, role=role, password_hash=hash_password(password), active=True)
    db.add(user)
    db.commit()
    return user, password


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == (email or "").strip().lower()))
    ok = check_password(password, user.password_hash if user else _DUMMY)
    if not user or not ok or not user.active:
        return None
    user.last_login_at = dt.datetime.now()
    db.commit()
    return user


def set_password(db: Session, user: User, password: str | None = None) -> str:
    password = check_new_password(password) if password else generate_password()
    user.password_hash = hash_password(password)
    db.commit()
    return password


def active_admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.active.is_(True))) or 0


def bootstrap_admin(db: Session) -> None:
    """First start: create the administrator from the environment and hand any existing quotes to them."""
    if db.scalar(select(func.count()).select_from(User)):
        return
    try:  # a bad value stops startup loudly rather than starting with an unknown admin password
        admin, _ = create_user(db, config.ADMIN_NAME or "Administrator", config.ADMIN_EMAIL, "admin", config.ADMIN_PASSWORD)
    except UserError as e:
        raise SystemExit(f"Cannot create the first administrator from QUOTE_ADMIN_EMAIL / QUOTE_ADMIN_PASSWORD: {e}")
    except IntegrityError:  # another worker started at the same moment and created it
        db.rollback()
        return
    for q in db.scalars(select(Quote).where(Quote.created_by_id.is_(None))):
        q.created_by_id = admin.id
        q.prepared_by = q.prepared_by or admin.name
    db.commit()

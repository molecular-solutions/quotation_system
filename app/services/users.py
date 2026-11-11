"""Accounts: the allow-list and roles. Cloudflare Access proves who someone is; this table says whether they may use
the app and as what. There are no passwords."""
from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.models import ROLES, Quote, User

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NO_PASSWORD = "cloudflare-access"  # fills the old password_hash column; it can never match a hash, so nothing can sign in with it


class UserError(ValueError):
    pass


def clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not _EMAIL.match(email):
        raise UserError("enter a valid email address")
    return email


def create_user(db: Session, name: str, email: str, role: str = "user") -> User:
    name = (name or "").strip()
    if not name:
        raise UserError("name is required")
    if role not in ROLES:
        raise UserError(f"role must be one of {ROLES}")
    email = clean_email(email)
    if db.scalar(select(User.id).where(User.email == email)):
        raise UserError("a user with this email already exists")
    user = User(name=name, email=email, role=role, password_hash=NO_PASSWORD, active=True)
    db.add(user)
    db.commit()
    return user


def active_admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.active.is_(True))) or 0


def bootstrap_admin(db: Session) -> None:
    """First start: create the administrator from the environment and hand any existing quotes to them."""
    if db.scalar(select(func.count()).select_from(User)):
        return
    try:  # a bad value stops startup loudly rather than starting with nobody able to sign in as admin
        admin = create_user(db, config.ADMIN_NAME or "Administrator", config.ADMIN_EMAIL, "admin")
    except UserError as e:
        raise SystemExit(f"Cannot create the first administrator from QUOTE_ADMIN_EMAIL: {e}")
    except IntegrityError:  # another worker started at the same moment and created it
        db.rollback()
        return
    for q in db.scalars(select(Quote).where(Quote.created_by_id.is_(None))):
        q.created_by_id = admin.id
        q.prepared_by = q.prepared_by or admin.name
    db.commit()

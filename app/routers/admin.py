"""Admin panel API: manage users, see every quotation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import ROLES, Quote, User
from app.services import users as users_svc

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


def admin_user_json(db: Session, u: User) -> dict:
    count = db.scalar(select(func.count()).select_from(Quote).where(Quote.created_by_id == u.id)) or 0
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "active": u.active, "quotes": count,
            "created_at": u.created_at.isoformat(sep=" ", timespec="minutes"),
            "last_login_at": u.last_login_at.isoformat(sep=" ", timespec="minutes") if u.last_login_at else None}


def get_user(db: Session, user_id: int) -> User:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "user not found")
    return u


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    return [admin_user_json(db, u) for u in db.scalars(select(User).order_by(User.active.desc(), User.name))]


class NewUser(BaseModel):
    name: str
    email: str
    role: str = "user"
    password: str | None = None  # blank = generate one


@router.post("/users", status_code=201)
def create_user(body: NewUser, db: Session = Depends(get_db)):
    try:
        user, password = users_svc.create_user(db, body.name, body.email, body.role, body.password or None)
    except users_svc.UserError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    return admin_user_json(db, user) | {"password": password}  # the only time the password is shown


class UserPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, me: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = get_user(db, user_id)
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(422, "name is required")
        u.name = body.name.strip()
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(422, f"role must be one of {ROLES}")
        u.role = body.role
    if body.active is not None:
        u.active = body.active
    if u.id == me.id and (u.role != "admin" or not u.active):
        db.rollback()
        raise HTTPException(409, "You cannot remove your own admin access or deactivate yourself")
    if users_svc.active_admins(db) == 0:
        db.rollback()
        raise HTTPException(409, "There must always be at least one active administrator")
    db.commit()
    return admin_user_json(db, u)


class PasswordReset(BaseModel):
    password: str | None = None


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, body: PasswordReset, db: Session = Depends(get_db)):
    u = get_user(db, user_id)
    try:
        password = users_svc.set_password(db, u, body.password or None)
    except users_svc.UserError as e:
        raise HTTPException(422, str(e))
    return {"email": u.email, "name": u.name, "password": password}


@router.get("/quotes")
def all_quotes(q: str = "", status: str = "", user_id: int | None = None, db: Session = Depends(get_db)):
    from app.routers.quotes import quote_json
    stmt = select(Quote).order_by(Quote.id.desc()).limit(1000)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Quote.number.ilike(like), Quote.customer_name.ilike(like),
                              Quote.customer_address.ilike(like)))
    if status:
        stmt = stmt.where(Quote.status == status)
    if user_id:
        stmt = stmt.where(Quote.created_by_id == user_id)
    return [quote_json(db, x, with_lines=False) for x in db.scalars(stmt)]

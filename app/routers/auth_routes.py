from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import auth, config
from app.database import get_db
from app.models import User
from app.services import users as users_svc

router = APIRouter(prefix="/api")


class Login(BaseModel):
    email: str
    password: str


def user_json(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role}


@router.post("/login")
def login(body: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    keys = (f"ip:{auth.client_ip(request)}", f"email:{body.email.strip().lower()}")
    auth.check_rate_limit(*keys)
    user = users_svc.authenticate(db, body.email, body.password)
    if user is None:
        auth.record_failure(*keys)
        raise HTTPException(401, "Wrong email or password")
    auth.clear_failures(keys[1])
    response.set_cookie(auth.COOKIE, auth.make_token(user), httponly=True, samesite="lax",
                        secure=config.COOKIE_SECURE, max_age=config.SESSION_HOURS * 3600)
    return user_json(user)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(auth.current_user)):
    return user_json(user)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


@router.post("/me/password")
def change_password(body: PasswordChange, request: Request, response: Response, user: User = Depends(auth.current_user),
                    db: Session = Depends(get_db)):
    keys = (f"pw:{user.id}",)
    auth.check_rate_limit(*keys)
    if not users_svc.check_password(body.current_password, user.password_hash):
        auth.record_failure(*keys)
        raise HTTPException(422, "Your current password is not correct")
    try:
        users_svc.set_password(db, user, body.new_password)
    except users_svc.UserError as e:
        raise HTTPException(422, str(e))
    # the old cookie stops working with the old password; keep this browser signed in
    response.set_cookie(auth.COOKIE, auth.make_token(user), httponly=True, samesite="lax",
                        secure=config.COOKIE_SECURE, max_age=config.SESSION_HOURS * 3600)
    return {"ok": True}

from fastapi import APIRouter, Depends

from app import auth
from app.models import User

router = APIRouter(prefix="/api")


def user_json(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role}


@router.get("/me")
def me(user: User = Depends(auth.current_user)):
    return user_json(user)

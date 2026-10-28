"""Per-user sessions: a signed, expiring cookie naming the user, plus a login rate limit.

The cookie also carries a fingerprint of the password hash, so changing or resetting a password signs that user out
everywhere; deactivating a user is checked on every request."""
from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.models import User

COOKIE = "mq_session"
_signer = URLSafeTimedSerializer(config.SECRET_KEY, salt="quote-session-v2")
_attempts: dict[str, deque] = defaultdict(deque)
MAX_FAILS, WINDOW = 5, 300  # 5 wrong attempts per 5 minutes, per client and per email


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")  # set by the reverse proxy (Caddy)
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def _recent(key: str) -> deque:
    q, now = _attempts[key], time.time()
    while q and now - q[0] > WINDOW:
        q.popleft()
    return q


def check_rate_limit(*keys: str) -> None:
    if any(len(_recent(k)) >= MAX_FAILS for k in keys):
        raise HTTPException(429, "Too many failed sign-in attempts. Try again in a few minutes.")


def record_failure(*keys: str) -> None:
    for k in keys:
        _attempts[k].append(time.time())


def clear_failures(*keys: str) -> None:
    for k in keys:
        _attempts.pop(k, None)


def _fingerprint(user: User) -> str:
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]


def make_token(user: User) -> str:
    return _signer.dumps({"u": user.id, "f": _fingerprint(user)})


def user_from_request(request: Request, db: Session) -> User | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        data = _signer.loads(token, max_age=config.SESSION_HOURS * 3600)
        user = db.get(User, data["u"])
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        return None
    if user is None or not user.active or data.get("f") != _fingerprint(user):
        return None
    return user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = user_from_request(request, db)
    if user is None:
        raise HTTPException(401, "Please log in")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Administrators only")
    return user

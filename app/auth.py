"""Sign-in is done by Cloudflare Access, not by the app.

Access puts a signed token (a JWT) in the `Cf-Access-Jwt-Assertion` header of every request that reaches the app through
the tunnel. We check its signature against the team's public keys and its audience against this application's AUD tag,
then take the verified email and look it up in the users table: who may use the app, and as what role, is decided there.
The plain `Cf-Access-Authenticated-User-Email` header is never trusted; anything that can reach the container directly
could forge it, but not a signed token."""
from __future__ import annotations

import datetime as dt
import logging

import jwt
from fastapi import Depends, HTTPException, Request
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.models import User

log = logging.getLogger(__name__)
HEADER = "Cf-Access-Jwt-Assertion"
NOT_SET_UP = "Your account is not set up for this system. Ask an administrator to add your email."
_jwks: PyJWKClient | None = None


def _keys() -> PyJWKClient:
    global _jwks
    if _jwks is None:  # keys are cached, and fetched again when a token names a key we have not seen (rotation)
        _jwks = PyJWKClient(f"https://{config.CF_TEAM_DOMAIN}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)
    return _jwks


def verified_email(request: Request) -> str | None:
    """The email in a valid Access token, lower-cased, or None when there is no valid token."""
    token = request.headers.get(HEADER)
    if not token or not config.CF_TEAM_DOMAIN or not config.CF_AUD:
        return None
    try:
        claims = jwt.decode(token, _keys().get_signing_key_from_jwt(token).key, algorithms=["RS256"],
                            audience=config.CF_AUD, issuer=f"https://{config.CF_TEAM_DOMAIN}", leeway=30,
                            options={"require": ["exp", "iat", "iss", "aud"]})
    except jwt.PyJWTError as e:  # bad signature, wrong audience, expired, or the keys could not be fetched
        log.warning("Access token rejected: %s", e)
        return None
    email = claims.get("email")  # a service token has no email and so gets no access
    return email.strip().lower() if isinstance(email, str) and email.strip() else None


def authenticate(request: Request, db: Session) -> User:
    email = verified_email(request)
    if email is None:
        raise HTTPException(401, "Sign in through Cloudflare Access")
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.active:  # deactivating a user takes effect on their very next request
        raise HTTPException(403, NOT_SET_UP)
    now = dt.datetime.now()
    if user.last_login_at is None or now - user.last_login_at > dt.timedelta(hours=1):
        user.last_login_at = now
        db.commit()
    return user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    return authenticate(request, db)


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Administrators only")
    return user

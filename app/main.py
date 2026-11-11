from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from html import escape

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy.orm import Session

from app import auth, config
from app import migrations
from app.database import SessionLocal, get_db
from app.routers import admin, auth_routes, data, lookup, prices, quotes
from app.services import quotes as quotes_svc
from app.services import users as users_svc

WEB = Path(__file__).resolve().parents[1] / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not (config.CF_TEAM_DOMAIN and config.CF_AUD):  # without these nobody could sign in; say so rather than start
        raise SystemExit("Set QUOTE_CF_TEAM_DOMAIN and QUOTE_CF_AUD (Cloudflare Access team domain and application AUD tag)")
    config.FILES_DIR.mkdir(parents=True, exist_ok=True)
    migrations.upgrade()
    with SessionLocal() as db:
        users_svc.bootstrap_admin(db)
        quotes_svc.ensure_counter(db)
        db.commit()
    yield


app = FastAPI(title="Quotation System", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
for r in (auth_routes.router, lookup.router, data.router, prices.router, quotes.router, admin.router):
    app.include_router(r)
app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")


def denied(status: int, message: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Quotations</title><link rel=stylesheet href=/assets/app.css>"
                        f"<body style='display:grid;place-items:center;min-height:100vh;text-align:center'>"
                        f"<div class=card style='max-width:420px'><h1>Quotations</h1><p>{escape(message)}</p>"
                        f"<p><a href=/cdn-cgi/access/logout>Sign in as someone else</a></p></div></body>", status_code=status)


def page(name: str, admin_only: bool = False):
    def handler(request: Request, db: Session = Depends(get_db)):
        try:
            user = auth.authenticate(request, db)
        except HTTPException as e:
            return denied(e.status_code, e.detail)
        if admin_only and user.role != "admin":
            return RedirectResponse("/", status_code=303)
        return FileResponse(WEB / name)
    return handler


for path, file, admin_only in (("/", "quotes.html", False), ("/new", "builder.html", False),
                               ("/quote/{quote_id}", "builder.html", False),
                               ("/data", "data.html", True), ("/prices", "prices.html", True), ("/settings", "settings.html", True),
                               ("/admin", "admin.html", True)):
    app.add_api_route(path, page(file, admin_only), methods=["GET"], include_in_schema=False)

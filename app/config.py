"""Runtime settings, all from environment variables (see docker-compose.yml)."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("QUOTE_DATA_DIR", BASE_DIR / "data"))
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'quotes.db'}")
# Sign-in is done by Cloudflare Access; the app only checks the signed token it adds to every request.
# CF_TEAM_DOMAIN: <team-name>.cloudflareaccess.com   CF_AUD: the Access application's "Application Audience (AUD) Tag".
CF_TEAM_DOMAIN = os.environ.get("QUOTE_CF_TEAM_DOMAIN", "").strip().lower().removeprefix("https://").rstrip("/")
CF_AUD = os.environ.get("QUOTE_CF_AUD", "").strip()
# The first administrator, created on first start when there are no users yet (must be the email they sign in with at
# Cloudflare). Further users are made in the admin panel.
ADMIN_EMAIL = os.environ.get("QUOTE_ADMIN_EMAIL", "admin@example.com").strip().lower()
ADMIN_NAME = os.environ.get("QUOTE_ADMIN_NAME", "Administrator").strip()
FILES_DIR = DATA_DIR / "files"

# Outgoing e-mail (leave QUOTE_SMTP_HOST empty to switch the Email button off).
SMTP_HOST = os.environ.get("QUOTE_SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("QUOTE_SMTP_PORT", "587"))
SMTP_SECURITY = os.environ.get("QUOTE_SMTP_SECURITY", "starttls").strip().lower()  # starttls | ssl | none
SMTP_USER = os.environ.get("QUOTE_SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("QUOTE_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("QUOTE_SMTP_FROM", SMTP_USER).strip()
SMTP_FROM_NAME = os.environ.get("QUOTE_SMTP_FROM_NAME", "Molecular Solutions").strip()

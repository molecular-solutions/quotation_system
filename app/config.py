"""Runtime settings, all from environment variables (see docker-compose.yml)."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("QUOTE_DATA_DIR", BASE_DIR / "data"))
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'quotes.db'}")
# The first administrator, created on first start when there are no users yet. Further users are made in the admin panel.
# (QUOTE_APP_PASSWORD is the old shared-password variable; it is still accepted as the first admin's password.)
ADMIN_EMAIL = os.environ.get("QUOTE_ADMIN_EMAIL", "admin@example.com").strip().lower()
ADMIN_NAME = os.environ.get("QUOTE_ADMIN_NAME", "Administrator").strip()
ADMIN_PASSWORD = os.environ.get("QUOTE_ADMIN_PASSWORD") or os.environ.get("QUOTE_APP_PASSWORD", "change-me")
SECRET_KEY = os.environ.get("QUOTE_SECRET_KEY", "dev-secret-change-me")
SESSION_HOURS = int(os.environ.get("QUOTE_SESSION_HOURS", "12"))
COOKIE_SECURE = os.environ.get("QUOTE_COOKIE_SECURE", "0") == "1"  # set to 1 behind HTTPS
FILES_DIR = DATA_DIR / "files"

# Outgoing e-mail (leave QUOTE_SMTP_HOST empty to switch the Email button off).
SMTP_HOST = os.environ.get("QUOTE_SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("QUOTE_SMTP_PORT", "587"))
SMTP_SECURITY = os.environ.get("QUOTE_SMTP_SECURITY", "starttls").strip().lower()  # starttls | ssl | none
SMTP_USER = os.environ.get("QUOTE_SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("QUOTE_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("QUOTE_SMTP_FROM", SMTP_USER).strip()
SMTP_FROM_NAME = os.environ.get("QUOTE_SMTP_FROM_NAME", "Molecular Solutions").strip()

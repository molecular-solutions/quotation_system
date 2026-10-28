"""E-mail a finished quotation (PDF attached) to the stored recipient list.

The SMTP login comes from environment variables (never the database or the browser). The recipients are the
addresses an administrator saves in Settings."""
from __future__ import annotations

import json
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from sqlalchemy.orm import Session

from app import config
from app.models import Setting

_ADDRESS = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+$")
MAX_RECIPIENTS = 50


class MailError(Exception):
    """Something the user can act on (not configured, no recipients, server refused)."""


def configured() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_FROM)


# ── recipients (a JSON list of addresses under one settings key) ────────────────────────────────
def clean_recipients(items) -> list[str]:
    if not isinstance(items, list):
        raise MailError("recipients must be a list of e-mail addresses")
    out: list[str] = []
    for raw in items:
        a = str(raw).strip().lower()
        if not a:
            continue
        if not _ADDRESS.match(a) or len(a) > 200:
            raise MailError(f"'{raw}' is not a valid e-mail address")
        if a not in out:
            out.append(a)
    if len(out) > MAX_RECIPIENTS:
        raise MailError(f"at most {MAX_RECIPIENTS} recipients")
    return out


def get_recipients(db: Session) -> list[str]:
    row = db.get(Setting, "mail_recipients")
    return clean_recipients(json.loads(row.value)) if row and row.value else []


def put_recipients(db: Session, items) -> list[str]:
    items = clean_recipients(items)
    value = json.dumps(items)
    row = db.get(Setting, "mail_recipients")
    if row:
        row.value = value
    else:
        db.add(Setting(key="mail_recipients", value=value))
    db.flush()
    return items


# ── sending ───────────────────────────────────────────────────────────────────────────────────
def _deliver(msg: EmailMessage) -> None:
    ctx = ssl.create_default_context()
    try:
        if config.SMTP_SECURITY == "ssl":
            smtp = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30, context=ctx)
        else:
            smtp = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
        with smtp:
            if config.SMTP_SECURITY == "starttls":
                smtp.starttls(context=ctx)
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise MailError("the mail server rejected the login – check QUOTE_SMTP_USER / QUOTE_SMTP_PASSWORD")
    except smtplib.SMTPRecipientsRefused:
        raise MailError("the mail server refused every recipient address")
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"could not send the e-mail ({e.__class__.__name__}: {e})")


def _base(subject: str, to: list[str]) -> EmailMessage:
    if not configured():
        raise MailError("e-mail is not set up on this server (QUOTE_SMTP_HOST and QUOTE_SMTP_FROM are missing)")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((config.SMTP_FROM_NAME, config.SMTP_FROM))
    msg["To"] = ", ".join(to)
    return msg


def send_quote(number: str, customer: str, date: str, valid_until: str, total: str, tax_note: str,
               pdf: bytes, filename: str, recipients: list[str], sender_name: str) -> None:
    if not recipients:
        raise MailError("no e-mail recipients are saved – an administrator can add them in Settings")
    msg = _base(f"Quotation {number} – {config.SMTP_FROM_NAME}", recipients)
    msg.set_content(
        f"Quotation {number} has been issued.\n\n"
        f"Customer:    {customer}\n"
        f"Date:        {date}\n"
        f"Valid until: {valid_until}\n"
        f"Amount:      {total} ({tax_note})\n\n"
        f"The quotation is attached as a PDF.\n\nSent by {sender_name} from the quotation system.\n")
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    _deliver(msg)


def send_test(to: str) -> None:
    msg = _base(f"Test e-mail from the {config.SMTP_FROM_NAME} quotation system", [to])
    msg.set_content("If you can read this, the quotation system can send e-mail.\n")
    _deliver(msg)

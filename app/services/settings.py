"""App settings: code defaults, overridable from the Settings page (stored in the DB)."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import Setting
from app.services import terms as terms_svc

DEFAULTS = {
    "number_format": "MS-{code}-{seq}",
    "code_Academic": "AB",       # AB = Academic Bundle
    "seq_next": "175",           # next global sequence number (template sample is 174)
    "validity_days": "30",
    "payment_terms": "15 days from the date of Invoice",
    "gst_pct": "18",
}
LABELS = {
    "number_format": "Academic quote number format ({code} and {seq} are replaced)",
    "code_Academic": "Code for Academic quotes (Commercial numbers are typed on the quote)",
    "seq_next": "Next quote sequence number",
    "validity_days": "Quote valid for (days)",
    "payment_terms": "Payment terms text",
    "gst_pct": "GST %",
}


def get_all(db: Session) -> dict[str, str]:
    out = dict(DEFAULTS)
    for s in db.query(Setting):
        if s.key in DEFAULTS:
            out[s.key] = s.value
    return out


def get(db: Session, key: str) -> str:
    return get_all(db)[key]


def put_many(db: Session, values: dict[str, str]) -> None:
    for k, v in values.items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting {k}")
        row = db.get(Setting, k)
        if row:
            row.value = str(v)
        else:
            db.add(Setting(key=k, value=str(v)))
    db.flush()


# ── Terms & Conditions (a list of points, stored as JSON under one key) ───────────────────────────
def get_terms(db: Session) -> list[dict]:
    row = db.get(Setting, "terms")
    if row is None or not row.value:
        return [dict(t) for t in terms_svc.DEFAULT_TERMS]
    return terms_svc.clean(json.loads(row.value))


def put_terms(db: Session, items) -> list[dict]:
    items = terms_svc.clean(items)
    value = json.dumps(items, ensure_ascii=False)
    row = db.get(Setting, "terms")
    if row:
        row.value = value
    else:
        db.add(Setting(key="terms", value=value))
    db.flush()
    return items


def terms_snapshot(db: Session) -> str:
    """The current terms as the JSON frozen onto a new quotation."""
    return json.dumps(get_terms(db), ensure_ascii=False)

"""Terms & Conditions content: the default text, validation and the small markup used to format it.

Each point is plain text with optional formatting marks:  **bold**   __italic__   ==highlight==
and these placeholders, filled from the quotation:  {validity_date}  {payment_terms}  {license_term}.
E-mail addresses are coloured blue automatically."""
from __future__ import annotations

import re

STYLES = ("bullet", "indent")  # "indent" = the template's narrower, more spaced paragraph (the purchase-order line)
TOKENS = ("validity_date", "payment_terms", "license_term")
MAX_POINTS, MAX_LEN = 20, 1500

DEFAULT_TERMS: list[dict] = [
    {"text": "Validity: This quotation is valid till **{validity_date}** from the date of issue.", "style": "bullet"},
    {"text": "Delivery Timeline: Installation & setup within 1 week from PO confirmation.", "style": "bullet"},
    {"text": "Payment Terms: =={payment_terms}==.", "style": "bullet"},
    {"text": "Please send your purchase order to rraghu@molecularsolutions.co.in or courier it to "
             "**__Molecular Solutions Software Pvt Ltd, 110 West Village, Kodipalya, Kengeri, Bangalore 560060, "
             "Mob: +91-9900090011__**", "style": "indent"},
    {"text": "License Term: **{license_term}**", "style": "bullet"},
    {"text": "Support: Comprehensive email, phone, and Online support during license period.", "style": "bullet"},
]


class TermsError(ValueError):
    pass


def clean(items) -> list[dict]:
    if not isinstance(items, list) or not items:
        raise TermsError("add at least one point")
    if len(items) > MAX_POINTS:
        raise TermsError(f"at most {MAX_POINTS} points")
    out = []
    for n, it in enumerate(items, start=1):
        text = " ".join(str((it or {}).get("text", "")).split())
        style = (it or {}).get("style", "bullet")
        if not text:
            raise TermsError(f"point {n} is empty (remove it or type something)")
        if len(text) > MAX_LEN:
            raise TermsError(f"point {n} is longer than {MAX_LEN} characters")
        if style not in STYLES:
            raise TermsError(f"point {n}: style must be one of {STYLES}")
        out.append({"text": text, "style": style})
    return out


def is_default(items: list[dict] | None) -> bool:
    return items is None or clean(items) == clean(DEFAULT_TERMS)


_MARK = re.compile(r"(\*\*|__|==)")
_EMAIL = re.compile(r"([\w.+-]+@[\w-]+(?:\.[\w-]+)+)")


def parse(text: str, values: dict[str, str]) -> list[dict]:
    """Text -> runs: [{text, bold, italic, highlight, link}], with placeholders filled in."""
    state = {"**": False, "__": False, "==": False}
    runs: list[dict] = []
    for part in _MARK.split(text):
        if part in state:
            state[part] = not state[part]
            continue
        for tok in TOKENS:
            part = part.replace("{" + tok + "}", values.get(tok, ""))
        for bit in _EMAIL.split(part):
            if bit:
                runs.append({"text": bit, "bold": state["**"], "italic": state["__"], "highlight": state["=="],
                             "link": bool(_EMAIL.fullmatch(bit))})
    return runs

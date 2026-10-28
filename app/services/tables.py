"""Table specs for the Data dashboard.

One spec per table: its fixed columns, validation and upsert. File uploads and the manual
grid both go through `clean()` + `upsert()`, so a row is accepted or rejected identically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    PRICE_BASES, TIERS, Customer, Package, PackageItem, Product, ProductCatalogLink,
)
from app.services.docgen import CATALOG_BLOCKS


@dataclass
class Col:
    key: str
    label: str
    required: bool = False
    aliases: tuple[str, ...] = ()
    kind: str = "text"  # text | money | bool | int | choice
    choices: tuple[str, ...] = ()
    example: str = ""


def C(key: str, label: str, *aliases: str, req: bool = False, kind: str = "text",
      choices: tuple[str, ...] = (), ex: str = "") -> Col:
    return Col(key, label, req, aliases, kind, choices, ex)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


TRUE = {"y", "yes", "true", "1", "active"}
FALSE = {"n", "no", "false", "0", "inactive"}


class TableSpec:
    name = ""
    title = ""
    columns: list[Col] = []

    # -- header handling -------------------------------------------------
    def column_for_header(self, header: str) -> Col | None:
        h = norm(header)
        for c in self.columns:
            if h in {norm(c.key), norm(c.label), *map(norm, c.aliases)}:
                return c
        return None

    # -- validation ------------------------------------------------------
    def clean(self, db: Session, raw: dict, row_id: int | None = None, is_new: bool = False) -> tuple[dict, dict[str, str]]:
        out: dict = {}
        errors: dict[str, str] = {}
        for c in self.columns:
            val = raw.get(c.key)
            val = "" if val is None else str(val).strip()
            if not val:
                if c.required:
                    errors[c.key] = "required"
                out[c.key] = None if c.kind in ("money", "int") else ("" if c.kind != "bool" else None)
                continue
            try:
                out[c.key] = self._coerce(c, val)
            except ValueError as e:
                errors[c.key] = str(e)
        self.check(db, out, errors, row_id, is_new)
        return out, errors

    @staticmethod
    def _coerce(c: Col, val: str):
        if c.kind == "money":
            try:
                d = Decimal(re.sub(r"[₹,\s]|rs\.?|inr", "", val, flags=re.I))
            except InvalidOperation:
                raise ValueError("not a number")
            if d < 0:
                raise ValueError("must not be negative")
            return d.quantize(Decimal("0.01"))
        if c.kind == "int":
            if not re.fullmatch(r"\d+(\.0+)?", val):
                raise ValueError("whole number expected")
            n = int(float(val))
            if n < 1:
                raise ValueError("must be at least 1")
            return n
        if c.kind == "bool":
            v = val.lower()
            if v in TRUE:
                return True
            if v in FALSE:
                return False
            raise ValueError("use Y or N")
        if c.kind == "choice":
            for ch in c.choices:
                if norm(ch) == norm(val):
                    return ch
            raise ValueError("one of: " + ", ".join(c.choices))
        return val

    def check(self, db: Session, row: dict, errors: dict[str, str], row_id: int | None = None, is_new: bool = False) -> None:
        """Cross-field / DB checks; subclasses override. row_id / is_new are set by the grid (not by file uploads,
        which update an existing row that has the same key)."""

    # -- storage (subclasses) -------------------------------------------
    def rows(self, db: Session) -> list[dict]: raise NotImplementedError
    def upsert(self, db: Session, row: dict, row_id: int | None = None) -> int: raise NotImplementedError
    def delete(self, db: Session, row_id: int) -> None: raise NotImplementedError
    def key_of(self, row: dict) -> str: raise NotImplementedError


# ── products ──────────────────────────────────────────────────────────────
class ProductsSpec(TableSpec):
    name, title = "products", "Products"
    columns = [
        C("name", "Product Name", "name", req=True, ex="ChemAIRS - Retrosynthesis"),
        C("category", "Category", ex="Software"),
        C("vendor", "Vendor", ex="Chemical.AI"),
        C("segment", "Segment", ex="Chemical Synthesis"),
        C("unit", "Unit", ex="License"),
        C("description", "Description", ex="Printed under the product name on the quotation"),
        C("line_label", "Quote Label", "line label", ex="Retrosynthesis Modules"),
        C("deployment", "Deployment", "deployment type", ex="Cloud"),
        C("default_users", "Default Users", "users", ex="1"),
        C("price_basis", "Price Basis", "basis", kind="choice", choices=PRICE_BASES, ex="per_user_year"),
        C("catalog_blocks", "Catalog Blocks", "catalog", ex="chemairs"),
        C("active", "Active", kind="bool", ex="Y"),
    ]
    # Currency exists in the old CSV; only INR is supported, so it is checked, not stored.
    _currency_aliases = ("currency",)
    # Prices now depend on the number of users and live on the Prices page; the old one-price columns are ignored.
    _retired = {"academic price", "commercial price", "academic", "commercial"}

    def column_for_header(self, header):
        if norm(header) in self._currency_aliases:
            return Col("currency", "Currency")
        if norm(header) in self._retired:
            return None
        return super().column_for_header(header)

    def clean(self, db, raw, row_id=None, is_new=False):
        out, errors = super().clean(db, raw, row_id, is_new)
        cur = str(raw.get("currency") or "INR").strip().upper()
        if cur not in ("INR", "RS", "₹"):
            errors["currency"] = "only INR is supported"
        return out, errors

    def check(self, db, row, errors, row_id=None, is_new=False):
        name = row.get("name")
        if name and (row_id is not None or is_new):
            other = db.scalar(select(Product.id).where(func.lower(Product.name) == name.lower()))
            if other is not None and other != row_id:
                errors["name"] = "a product with this name already exists"
        for key in (row.get("catalog_blocks") or "").replace(";", ",").split(","):
            key = key.strip()
            if key and key not in CATALOG_BLOCKS:
                errors["catalog_blocks"] = f"unknown block '{key}' (known: {', '.join(CATALOG_BLOCKS)})"
                break

    def key_of(self, row):
        return (row.get("name") or "").lower()

    def rows(self, db):
        out = []
        for p in db.scalars(select(Product).order_by(func.lower(Product.name))):
            out.append({
                "id": p.id, "name": p.name, "category": p.category, "vendor": p.vendor,
                "segment": p.segment, "unit": p.unit, "description": p.description, "line_label": p.line_label,
                "deployment": p.deployment, "default_users": p.default_users, "price_basis": p.price_basis,
                "catalog_blocks": ", ".join(p.block_keys), "active": p.active,
            })
        return out

    def upsert(self, db, row, row_id=None):
        name = row["name"].strip()
        p = db.get(Product, row_id) if row_id else db.scalar(select(Product).where(func.lower(Product.name) == name.lower()))
        is_new = p is None
        if is_new:
            p = Product(name=name)
            db.add(p)
        if row_id:  # an upload matched by name keeps the stored spelling; only the grid renames
            p.name = name
        # on update, blank cells keep the stored value; on create they take the defaults
        for attr, default in (("category", "Software"), ("vendor", ""), ("segment", ""), ("unit", "License"),
                              ("description", ""), ("line_label", ""), ("deployment", "Local"),
                              ("default_users", "1"), ("price_basis", "per_user_year")):
            v = row.get(attr)
            if v or is_new:
                setattr(p, attr, v or default)
        if row.get("active") is not None:
            p.active = row["active"]
        elif is_new:
            p.active = True
        db.flush()
        blocks = row.get("catalog_blocks") or ""
        if blocks or row_id:  # a blank cell in an upload keeps links; in the grid (row_id) it clears them
            keys = {k.strip() for k in blocks.replace(";", ",").split(",") if k.strip()}
            have = {l.block_key: l for l in p.catalog_links}
            for key, link in have.items():       # change only what differs: re-inserting an unchanged key
                if key not in keys:              # would collide with the row that is about to be deleted
                    p.catalog_links.remove(link)
            for key in sorted(keys - have.keys()):
                p.catalog_links.append(ProductCatalogLink(block_key=key))
        db.flush()
        return p.id

    def delete(self, db, row_id):
        p = db.get(Product, row_id)
        if p:  # never hard-delete: issued quotes reference products; hide instead
            p.active = False



# ── customers ─────────────────────────────────────────────────────────────
class CustomersSpec(TableSpec):
    name, title = "customers", "Customers"
    columns = [
        C("name", "Name", "customer", "contact", "customer name", req=True, ex="Dr. Adam"),
        C("department", "Department", ex="Department of Biophysics"),
        C("institution", "Institution", "company", "organisation", "organization", ex="AIIMS"),
        C("address", "Address", ex="Ansari Nagar, New Delhi – 110029"),
        C("email", "Email", ex="adam@example.com"),
        C("phone", "Phone"),
        C("tier", "Type", "customer type", "tier", kind="choice", choices=TIERS, ex="Academic"),
    ]

    def check(self, db, row, errors, row_id=None, is_new=False):
        if row.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", row["email"]):
            errors["email"] = "not a valid email"

    def key_of(self, row):
        return f"{(row.get('name') or '').lower()}|{(row.get('email') or '').lower()}"

    def rows(self, db):
        return [{"id": c.id, "name": c.name, "department": c.department, "institution": c.institution,
                 "address": c.address, "email": c.email, "phone": c.phone, "tier": c.tier}
                for c in db.scalars(select(Customer).order_by(Customer.name))]

    def upsert(self, db, row, row_id=None):
        c = db.get(Customer, row_id) if row_id else db.scalar(
            select(Customer).where(func.lower(Customer.name) == row["name"].lower(),
                                   func.lower(Customer.email) == (row.get("email") or "").lower()))
        if c is None:
            c = Customer(name=row["name"])
            db.add(c)
        c.name = row["name"]
        for attr in ("department", "institution", "address", "email", "phone"):
            setattr(c, attr, row.get(attr) or "")
        c.tier = row.get("tier") or "Academic"
        db.flush()
        return c.id

    def delete(self, db, row_id):
        c = db.get(Customer, row_id)
        if c:
            db.delete(c)  # quotes keep their own frozen copy


# ── packages (one row per package item) ───────────────────────────────────
class PackagesSpec(TableSpec):
    name, title = "packages", "Packages"
    columns = [
        C("package", "Package", "package name", req=True, ex="Academic Bundle"),
        C("product", "Product Name", "product name", "name", req=True, ex="ChemAIRS - Retrosynthesis"),
        C("users", "Users", ex="1"),
        C("years", "Years", kind="int", ex="3"),
    ]

    def check(self, db, row, errors, row_id=None, is_new=False):
        name = row.get("product")
        if name and "product" not in errors and not db.scalar(
                select(Product.id).where(func.lower(Product.name) == name.lower())):
            errors["product"] = "no product with this name (import products first)"

    def key_of(self, row):
        return f"{(row.get('package') or '').lower()}|{(row.get('product') or '').lower()}"

    def rows(self, db):
        out = []
        for pk in db.scalars(select(Package).order_by(Package.name)):
            for it in pk.items:
                out.append({"id": it.id, "package": pk.name, "product": it.product.name,
                            "users": it.users, "years": it.years})
        return out

    def upsert(self, db, row, row_id=None):
        prod = db.scalar(select(Product).where(func.lower(Product.name) == row["product"].lower()))
        pk = db.scalar(select(Package).where(func.lower(Package.name) == row["package"].lower()))
        if pk is None:
            pk = Package(name=row["package"])
            db.add(pk)
            db.flush()
        item = db.get(PackageItem, row_id) if row_id else next((i for i in pk.items if i.product_id == prod.id), None)
        if item is None:
            item = PackageItem(package_id=pk.id, product_id=prod.id)
            db.add(item)
        item.package_id, item.product_id = pk.id, prod.id
        item.users = row.get("users") or "1"
        item.years = row.get("years") or 3
        db.flush()
        return item.id

    def delete(self, db, row_id):
        it = db.get(PackageItem, row_id)
        if it:
            pk = db.get(Package, it.package_id)
            db.delete(it)
            db.flush()
            db.refresh(pk)
            if not pk.items:
                db.delete(pk)


# ── catalog blocks (read-only reference) ─────────────────────────────────
CATALOG_LABELS = {
    "commander": "Target Identification — Commander (Genotypic)",
    "seesar_csd": "Ultra-High Throughput Virtual Screening — SeeSAR & Chemical Space Docking",
    "xmolgen": "Ultra-High Throughput Virtual Screening — XMolGen (XtalPi)",
    "infinisee": "Ultra-High Throughput Virtual Screening — InfiniSee",
    "xfep": "Hit to Lead Transformation — XFEP (XtalPi)",
    "admet": "Lead Optimization — ADMET Predictor",
    "gastroplus": "Lead Optimization — GastroPlus",
    "pkpd": "Lead Optimization — PK/PD Modelling",
    "chemairs": "Chemical Synthesis — ChemAIRS (Chemical.AI)",
    "yasara": "Molecular Modelling — YASARA",
    "eln": "Electronic Lab Notebook — TransGene IT & Dotmatics",
    "nanome": "Virtual Reality — Nanome",
    "formulation": "Formulation development — FormSCI / SoluDEM",
    "kvantify": "Kvantify — Koffee Binding / Unbinding Kinetics, Qrunch Pro",
}

SPECS: dict[str, TableSpec] = {s.name: s for s in (ProductsSpec(), CustomersSpec(), PackagesSpec())}

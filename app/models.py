from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

TIERS = ("Academic", "Commercial")
PRICE_BASES = ("per_user_year", "per_license_year", "complimentary")
STATUSES = ("Draft", "Sent", "Won", "Lost")
GST_MODES = ("IGST", "CGST_SGST")
ROLES = ("admin", "user")


def format_address(department: str, institution: str, address: str) -> str:
    """The address as printed: department and institution on one line, the street address on the next."""
    first = ", ".join(p.strip() for p in (department, institution) if p and p.strip())
    return "\n".join(x for x in (first, (address or "").strip()) if x)


class User(Base):
    """A team member. The admin creates accounts and shares the credentials; there is no self sign-up."""
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True)  # stored lower-case; this is the login
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(10), default="user")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)  # the natural key; `id` is internal and never shown
    vendor: Mapped[str] = mapped_column(String(100), default="")
    segment: Mapped[str] = mapped_column(String(100), default="")
    category: Mapped[str] = mapped_column(String(60), default="Software")
    unit: Mapped[str] = mapped_column(String(40), default="License")
    description: Mapped[str] = mapped_column(Text, default="")
    # text printed in the PRODUCT NAME column; falls back to `name`
    line_label: Mapped[str] = mapped_column(String(300), default="")
    deployment: Mapped[str] = mapped_column(String(40), default="Local")
    default_users: Mapped[str] = mapped_column(String(60), default="1")
    price_basis: Mapped[str] = mapped_column(String(20), default="per_user_year")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    prices: Mapped[list["ProductPrice"]] = relationship(cascade="all, delete-orphan", back_populates="product")
    catalog_links: Mapped[list["ProductCatalogLink"]] = relationship(cascade="all, delete-orphan")

    def price_for(self, tier: str, users: int | str | None = 1) -> Decimal | None:
        """Annual price for this many users, or None when no price is stored for that count.
        A licence-style product (Floating, Group, ...) has a single price, stored at 1 user."""
        row = self._price_row(tier, users)
        return row.annual_price if row else None

    def term_price(self, tier: str, users: int | str | None, years: int) -> Decimal | None:
        """The price for the WHOLE term: 1 year = the annual price, 3 years = the 3-year price. Any other length,
        or a 3-year quote with no 3-year price stored, has no catalogue price (it is typed on the quotation)."""
        row = self._price_row(tier, users)
        if row is None:
            return None
        return {1: row.annual_price, 3: row.three_year_price}.get(years)

    def _price_row(self, tier: str, users: int | str | None) -> "ProductPrice | None":
        if self.price_basis == "per_license_year":
            users = 1
        else:
            try:
                users = int(str(users).strip())
            except ValueError:
                return None
        return next((p for p in self.prices if p.tier == tier and p.users == users), None)

    @property
    def block_keys(self) -> list[str]:
        return sorted(link.block_key for link in self.catalog_links)


class ProductPrice(Base):
    """The price for one product, bundle (tier) and number of users: the TOTAL for that many users for one year.
    There is no formula between rows; each user count has whatever price was entered for it."""
    __tablename__ = "product_prices"
    __table_args__ = (UniqueConstraint("product_id", "tier", "users", name="uq_product_prices_product_tier_users"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    tier: Mapped[str] = mapped_column(String(20))
    users: Mapped[int] = mapped_column(Integer, default=1)
    annual_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    # the price of a 3-year quote (the TOTAL for 3 years, not 3 x annual); NULL = none, so it is typed on the quotation
    three_year_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    product: Mapped[Product] = relationship(back_populates="prices")


class ProductCatalogLink(Base):
    """Which 'Our Software Offering' blocks of the template turn red when this product is quoted."""
    __tablename__ = "product_catalog_links"
    __table_args__ = (UniqueConstraint("product_id", "block_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    block_key: Mapped[str] = mapped_column(String(40))


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    department: Mapped[str] = mapped_column(String(200), default="")
    institution: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(60), default="")
    tier: Mapped[str] = mapped_column(String(20), default="Academic")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def address_block(self) -> str:
        return format_address(self.department, self.institution, self.address)


class Package(Base):
    __tablename__ = "packages"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    items: Mapped[list["PackageItem"]] = relationship(cascade="all, delete-orphan", order_by="PackageItem.id")


class PackageItem(Base):
    __tablename__ = "package_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("packages.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    users: Mapped[str] = mapped_column(String(60), default="1")
    years: Mapped[int] = mapped_column(Integer, default=3)
    product: Mapped[Product] = relationship()


class Quote(Base):
    __tablename__ = "quotes"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    seq: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    date: Mapped[dt.date] = mapped_column(Date)
    valid_until: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="Draft")
    tier: Mapped[str] = mapped_column(String(20), default="Academic")
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    # frozen copy of the customer as printed on the document
    customer_name: Mapped[str] = mapped_column(String(200))
    customer_address: Mapped[str] = mapped_column(Text, default="")
    customer_email: Mapped[str] = mapped_column(String(200), default="")
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    gst_mode: Mapped[str] = mapped_column(String(12), default="IGST")
    gst_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=18)
    payment_terms: Mapped[str] = mapped_column(String(300), default="")
    prepared_by: Mapped[str] = mapped_column(String(100), default="")
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)  # frozen Terms & Conditions (JSON); NULL = template text
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    list_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    emailed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)  # last send from the Email button

    created_by: Mapped["User | None"] = relationship()
    lines: Mapped[list["QuoteLine"]] = relationship(cascade="all, delete-orphan", order_by="QuoteLine.sort")


class QuoteLine(Base):
    """Frozen copy of what was quoted: later price/name edits never change an issued quote."""
    __tablename__ = "quote_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    label: Mapped[str] = mapped_column(String(300))
    deployment: Mapped[str] = mapped_column(String(40), default="")
    users_label: Mapped[str] = mapped_column(String(60), default="1")
    qty: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=1)
    years: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    complimentary: Mapped[bool] = mapped_column(Boolean, default=False)
    block_keys: Mapped[str] = mapped_column(String(300), default="")  # frozen catalog links
    description: Mapped[str] = mapped_column(Text, default="")  # printed under the product name


class Counter(Base):
    __tablename__ = "counters"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class ImportLog(Base):
    __tablename__ = "import_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    table_name: Mapped[str] = mapped_column(String(30))
    filename: Mapped[str] = mapped_column(String(200), default="")
    rows_ok: Mapped[int] = mapped_column(Integer, default=0)
    rows_failed: Mapped[int] = mapped_column(Integer, default=0)
    at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

import datetime as dt
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.models import Customer, Product, ProductCatalogLink, ProductPrice, Quote
from app.services import settings as app_settings
from app.services.quotes import (CustomerIn, LineInput, QuoteError, QuoteInput, create_quote, duplicate,
                                 revise, to_doc_quote, update_quote)


@pytest.fixture()
def catalog(db):
    a = Product(name="SeeSAR", deployment="Local", default_users="25", line_label="SeeSAR with YASARA Plugin")
    # price for N users is a fixed number per user count, not N x the 1-user price
    # (a 3-year price here happens to be 3 x annual, so the quote maths below stays easy to read; test_three_year_prices
    # covers a 3-year price that is not)
    price = lambda tier, users, annual: ProductPrice(tier=tier, users=users, annual_price=D(annual), three_year_price=D(annual) * 3)
    a.prices = [price("Academic", 1, "1000"), price("Academic", 3, "2400"), price("Academic", 25, "25000"),
                price("Commercial", 1, "3000"), price("Commercial", 25, "75000")]
    a.catalog_links = [ProductCatalogLink(block_key="seesar_csd")]
    b = Product(name="ADMET Predictor", deployment="Local", price_basis="complimentary")
    c = Product(name="No price yet")
    db.add_all([a, b, c]); db.commit()
    return a, b, c


def data(a, b, **kw):
    return QuoteInput(customer=CustomerIn(name="Dr. Adam", department="Biophysics", institution="AIIMS", email="a@x.in"),
                      lines=[LineInput(product_id=a.id, years=3), LineInput(product_id=b.id, years=1)], **kw)


def test_create_prices_from_tier_and_freezes(db, catalog):
    a, b, c = catalog
    q = create_quote(db, data(a, b, discount_pct=10))
    assert q.number == "MS-AB-175" and q.seq == 175
    l1, l2 = q.lines
    assert (l1.label, l1.users_label, l1.unit_price, l1.amount) == ("SeeSAR with YASARA Plugin", "25", D("25000"), D("75000.00"))
    assert l2.complimentary and l2.amount == 0
    assert (q.list_total, q.discount_amount, q.subtotal, q.tax_amount, q.total) == (
        D("75000"), D("7500"), D("67500"), D("12150"), D("79650"))
    # later price change must not touch the issued quote
    a.prices[0].annual_price = D("9999"); db.commit(); db.refresh(q)
    assert q.lines[0].amount == D("75000.00")


def test_academic_numbers_are_sequential_and_commercial_numbers_are_typed(db, catalog):
    a, b, _ = catalog
    q1 = create_quote(db, data(a, b))
    c = create_quote(db, data(a, b, tier="Commercial", number="ms-ca-26-058"))
    assert (q1.number, c.number) == ("MS-AB-175", "MS-CA-26-058")
    r1, r2 = revise(db, q1), revise(db, q1)
    assert (r1.number, r2.number) == ("MS-AB-175-R1", "MS-AB-175-R2")
    assert revise(db, c).number == "MS-CA-26-058-R1"
    assert revise(db, c).number == "MS-CA-26-058-R2"
    # a typed number never uses up an Academic number
    assert duplicate(db, q1).number == "MS-AB-176"
    assert duplicate(db, c, "MS-CA-26-059").number == "MS-CA-26-059"


def test_commercial_number_is_required_unique_and_well_formed(db, catalog):
    a, b, _ = catalog
    for bad in (None, "", "MS-", "MS-bad number", "MS-CA-26-058-R1", "MS-CA--1"):
        with pytest.raises(QuoteError):
            create_quote(db, data(a, b, tier="Commercial", number=bad))
    create_quote(db, data(a, b, tier="Commercial", number="CA-26-058"))  # prefix may be left off
    with pytest.raises(QuoteError, match="already used"):
        create_quote(db, data(a, b, tier="Commercial", number="MS-CA-26-058"))
    with pytest.raises(QuoteError):
        duplicate(db, db.scalar(select(Quote).where(Quote.number == "MS-CA-26-058")))


def test_price_row_always_says_inc_gst_with_no_gst_rows(db, catalog):
    a, b, _ = catalog
    disc = to_doc_quote(create_quote(db, data(a, b, discount_pct=10)))
    assert disc.summary[-1] == ("TOTAL AMOUNT INC. GST", "₹ 79,650.00")
    nodisc = to_doc_quote(create_quote(db, data(a, b)))
    assert nodisc.summary == [("TOTAL AMOUNT INC. GST", "₹ 88,500.00")]  # a single price row


def test_copy_keeps_frozen_customer_and_does_not_rewrite_customer_record(db, catalog):
    a, b, _ = catalog
    q = create_quote(db, data(a, b))
    cust = db.get(Customer, q.customer_id)
    assert cust.department == "Biophysics" and cust.institution == "AIIMS"
    r = revise(db, q)
    db.refresh(cust)
    assert (cust.department, cust.institution) == ("Biophysics", "AIIMS")
    assert r.customer_address == q.customer_address == "Biophysics, AIIMS"
    assert [l.amount for l in r.lines] == [l.amount for l in q.lines]


def test_missing_price_and_empty_quote_are_rejected(db, catalog):
    a, b, c = catalog
    with pytest.raises(QuoteError, match="no Academic price"):
        create_quote(db, QuoteInput(customer=CustomerIn(name="X"), lines=[LineInput(product_id=c.id)]))
    db.rollback()
    with pytest.raises(QuoteError, match="at least one"):
        create_quote(db, QuoteInput(customer=CustomerIn(name="X"), lines=[]))


def test_doc_mapping(db, catalog):
    a, b, _ = catalog
    q = create_quote(db, data(a, b, discount_pct=10, date=dt.date(2026, 10, 19)))
    d = to_doc_quote(q)
    assert d.date == "19/10/2026" and d.license_type == "Academic License"
    assert d.customer_address == "Biophysics, AIIMS"
    assert [s[0] for s in d.summary] == ["LIST TOTAL", "DISCOUNT", "TOTAL AMOUNT INC. GST"]
    assert d.summary[1][1] == "- ₹ 7,500.00" and d.summary[-1][1] == "₹ 79,650.00"
    assert d.lines[0].duration == "3 Years" and d.lines[1].duration == "1 Year" and d.lines[1].complimentary
    assert d.license_term == "3 years from date of activation."  # complimentary line is ignored
    q2 = create_quote(db, QuoteInput(customer=CustomerIn(name="Y"), lines=[
        LineInput(product_id=a.id, years=3), LineInput(product_id=a.id, years=1)]))
    assert to_doc_quote(q2).license_term.startswith("As per the duration")  # paid lines differ
    assert d.quoted_blocks == {"seesar_csd"}
    assert d.validity_date == "18 Nov 2026"  # 30 days after issue


def test_update_replaces_lines(db, catalog):
    a, b, _ = catalog
    q = create_quote(db, data(a, b))
    d = data(a, b, discount_pct=0)
    d.lines = [LineInput(product_id=a.id, years=3, users_label="3")]
    update_quote(db, q, d)
    assert len(q.lines) == 1 and q.lines[0].amount == D("7200.00") and db.query(Quote).count() == 1


def test_numbers_are_sequential_across_simultaneous_users(tmp_path):
    """Many sessions (separate connections to one database file, as separate users/workers would be) get distinct,
    gap-free numbers, including when they race to create the very first one."""
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.services.quotes import next_number

    engine = create_engine(f"sqlite:///{tmp_path / 'q.db'}", connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def take(_):
        with Session() as s:
            n = next_number(s, "Academic")
            s.commit()
            return n

    with ThreadPoolExecutor(12) as ex:
        got = list(ex.map(take, range(48)))
    seqs = sorted(seq for _, seq in got)
    assert seqs == list(range(175, 175 + 48))
    assert {num for num, _ in got} == {f"MS-AB-{n}" for n in seqs}


def test_a_failed_save_gives_its_number_back(db):
    from app.services.quotes import next_number
    n1, s1 = next_number(db, "Academic")
    db.rollback()                       # the quote failed validation, nothing committed
    n2, s2 = next_number(db, "Academic")
    assert s2 == s1 and n2 == n1


def test_unquoted_catalog_blocks_leave_no_red_text_anywhere():
    """Nothing quoted -> no red text in the catalog at all (the Kvantify segment label used to stay red)."""
    import io
    import docx

    from app.services.docgen import RED_SET, _run_color, _text, _w, render_quote, template_sample

    q = template_sample()
    q.quoted_blocks = set()
    body = docx.Document(io.BytesIO(render_quote(q))).element.body
    red = [_text(r) for tbl in list(body)[14:] for r in tbl.iter(_w("r")) if _run_color(r) in RED_SET and _text(r).strip()]
    # the only red text allowed is the "The Red colored ones are quoted" legend line above the catalog
    assert [t for t in red if "Red colored" not in t and "as per your requirement" not in t] == []
    q.quoted_blocks = {"kvantify"}
    body = docx.Document(io.BytesIO(render_quote(q))).element.body
    assert any("Kvantify" in _text(r) and _run_color(r) in RED_SET for r in body.iter(_w("r")))


def test_a_typed_line_total_sets_the_unit_price_and_survives_saving_and_copying(db, catalog):
    a, b, _ = catalog                                                       # SeeSAR: Academic 25 users = 25,000 a year
    d = data(a, b)
    d.lines[0] = LineInput(product_id=a.id, years=3, users_label="3", amount=D("100000"))
    q = create_quote(db, d)
    line = q.lines[0]
    assert (line.unit_price, line.amount) == (D("33333.33"), D("100000.00"))
    assert q.list_total == D("100000.00") and q.total == D("118000.00")
    doc = to_doc_quote(q)
    assert doc.lines[0].amount == "₹ 1,00,000.00" and doc.summary[-1] == ("TOTAL AMOUNT INC. GST", "₹ 1,18,000.00")

    from app.services.quotes import amount_pinned, as_input
    assert amount_pinned(line) and not amount_pinned(q.lines[1])
    again = as_input(q)
    assert again.lines[0].amount == D("100000.00") and again.lines[1].amount is None
    copy = duplicate(db, q)
    assert copy.lines[0].amount == D("100000.00") and copy.total == D("118000.00")


def test_a_line_total_that_divides_evenly_is_not_marked_as_typed(db, catalog):
    a, b, _ = catalog
    d = data(a, b)
    d.lines[0] = LineInput(product_id=a.id, years=3, users_label="25", amount=D("75000"))
    from app.services.quotes import amount_pinned
    q = create_quote(db, d)
    assert q.lines[0].unit_price == D("25000.00") and not amount_pinned(q.lines[0])


def test_price_comes_from_the_users_row_not_from_a_per_user_multiple(db, catalog):
    a, _, _ = catalog
    for users, annual in (("1", "1000"), ("3", "2400"), ("25", "25000")):
        q = create_quote(db, QuoteInput(customer=CustomerIn(name="X"), lines=[LineInput(product_id=a.id, users_label=users, years=3)]))
        assert (q.lines[0].unit_price, q.lines[0].amount) == (D(annual), D(annual) * 3)
    with pytest.raises(QuoteError, match="no Academic price for 5 users"):   # no row for 5: the user must type the price
        create_quote(db, QuoteInput(customer=CustomerIn(name="X"), lines=[LineInput(product_id=a.id, users_label="5")]))
    q = create_quote(db, QuoteInput(customer=CustomerIn(name="X"), lines=[LineInput(product_id=a.id, users_label="30", unit_price=D("31000"), years=1)]))
    assert q.lines[0].amount == D("31000.00")                                # more than 25 users: typed price
    q = create_quote(db, QuoteInput(customer=CustomerIn(name="X"), tier="Commercial", number="MS-CX-1", lines=[LineInput(product_id=a.id, users_label="25")]))
    assert q.lines[0].unit_price == D("75000")


def test_a_licence_product_has_one_price_whatever_the_users_text(db):
    p = Product(name="HPSee", price_basis="per_license_year", default_users="Floating")
    p.prices = [ProductPrice(tier="Academic", users=1, annual_price=D("900000"), three_year_price=D("2400000"))]
    db.add(p); db.commit()
    q = create_quote(db, QuoteInput(customer=CustomerIn(name="X"), lines=[LineInput(product_id=p.id, years=3)]))
    assert (q.lines[0].users_label, q.lines[0].unit_price, q.lines[0].amount) == ("Floating", D("800000"), D("2400000.00"))


def test_a_typed_total_works_for_a_product_with_no_price(db, catalog):
    _, b, c = catalog
    d = QuoteInput(customer=CustomerIn(name="Dr. Adam"), lines=[LineInput(product_id=c.id, years=2, users_label="1", amount=D("50000"))])
    q = create_quote(db, d)
    assert q.lines[0].unit_price == D("25000.00") and q.lines[0].amount == D("50000.00")
    with pytest.raises(QuoteError, match="no Academic price"):
        create_quote(db, QuoteInput(customer=CustomerIn(name="Dr. Adam"), lines=[LineInput(product_id=c.id)]))


def test_three_year_price_is_the_stored_total_and_other_lengths_are_typed(db):
    from app.services.quotes import resolve_lines
    p = Product(name="Tri")
    p.prices = [ProductPrice(tier="Academic", users=1, annual_price=D("1000"), three_year_price=D("2500")),
                ProductPrice(tier="Commercial", users=1, annual_price=D("2000"))]          # a commercial price with no 3-year price
    db.add(p); db.commit()
    one = lambda tier, years, **kw: resolve_lines(db, [LineInput(product_id=p.id, users_label="1", years=years, **kw)], tier)[0]

    assert (one("Academic", 1).unit_price, one("Academic", 1).amount) == (D("1000"), None)
    r = one("Academic", 3)
    assert (r.unit_price, r.amount) == (D("833.33"), D("2500"))                  # the 3-year total as stored, not 3 x 833.33
    q = create_quote(db, QuoteInput(customer=CustomerIn(name="X"), lines=[LineInput(product_id=p.id, users_label="1", years=3)]))
    assert q.lines[0].amount == D("2500.00") and q.list_total == D("2500.00")

    with pytest.raises(QuoteError, match="no Academic price for 1 user for 2 years"):
        one("Academic", 2)                                                        # only 1 and 3 years are priced
    with pytest.raises(QuoteError, match="no Commercial price for 1 user for 3 years"):
        one("Commercial", 3)                                                      # no 3-year price: it is typed, never annual x 3
    assert one("Commercial", 1).unit_price == D("2000")
    assert one("Academic", 2, unit_price=D("900")).unit_price == D("900")         # a typed price always works
    assert one("Commercial", 3, amount=D("5100")).amount == D("5100")

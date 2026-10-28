from decimal import Decimal as D

import pytest

from app.services.pricing import LineIn, compute, inr, parse_users, rupees


def test_per_line_years_and_numeric_users():
    # the unit price is already the price for all the users, so only the years multiply it
    t = compute([LineIn(D("85000"), "25", 3), LineIn(D("32000"), "3", 1)], 0)
    assert [l.amount for l in t.lines] == [D("255000.00"), D("32000.00")]
    assert t.list_total == D("287000.00")
    assert t.discount_amount == 0


def test_text_users_count_as_one_and_still_print():
    assert parse_users("Floating") == 1
    assert parse_users("1 Group License") == 1
    assert parse_users(" 12 ") == 12
    t = compute([LineIn(D("1000"), "Concurrent Usage & 1 GPU", 2)], 0)
    assert t.lines[0].amount == D("2000.00")


def test_complimentary_adds_nothing():
    t = compute([LineIn(D("999"), "1", 3, complimentary=True), LineIn(D("100"), "1", 1)], 0)
    assert t.list_total == D("100.00")


def test_discount_is_separate_and_tax_on_discounted_subtotal():
    t = compute([LineIn(D("100000"), "1", 1)], 10)
    assert (t.list_total, t.discount_amount, t.subtotal) == (D("100000.00"), D("10000.00"), D("90000.00"))
    assert t.tax_rows == [("IGST 18%", D("16200.00"))]
    assert t.total == D("106200.00")


def test_cgst_sgst_split():
    t = compute([LineIn(D("100000"), "1", 1)], 0, "CGST_SGST", 18)
    assert t.tax_rows == [("CGST 9%", D("9000.00")), ("SGST 9%", D("9000.00"))]
    assert t.total == D("118000.00")


def test_rounding_half_up():
    t = compute([LineIn(D("0.005"), "1", 1)], 0)
    assert t.lines[0].amount == D("0.01")


@pytest.mark.parametrize("bad", [-1, 101])
def test_discount_bounds(bad):
    with pytest.raises(ValueError):
        compute([LineIn(D("1"), "1", 1)], bad)


def test_indian_grouping():
    assert inr(D("1234567.5")) == "12,34,567.50"
    assert inr(999) == "999.00"
    assert inr(100000) == "1,00,000.00"
    assert rupees(D("-1000")) == "- ₹ 1,000.00"


def test_the_total_always_includes_gst():
    t = compute([LineIn(D("10000"), "1", 3)], 10, "IGST", 18)
    assert t.subtotal == D("27000.00") and t.tax_amount == D("4860.00") and t.total == D("31860.00")


def test_a_typed_line_total_is_used_as_given_and_gives_the_unit_price():
    from app.services.pricing import unit_from_total
    assert unit_from_total(D("100000"), 3) == D("33333.33")          # shown to the paisa
    t = compute([LineIn(D("33333.33"), "3", 3, amount=D("100000"))], 0, "IGST", 18)
    assert t.lines[0].amount == D("100000.00") and t.total == D("118000.00")   # not 99,999.99 from the rounded unit price
    with pytest.raises(ValueError):
        unit_from_total(D("5000"), 0)

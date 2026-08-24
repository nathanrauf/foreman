from pricing import line_total, order_total, tier_discount


def test_no_discount_below_100():
    assert tier_discount(50) == 0.0


def test_ten_percent_at_100():
    assert tier_discount(100) == 0.10


def test_ten_percent_mid_tier():
    assert tier_discount(250) == 0.10


def test_line_total():
    assert line_total(9.99, 3) == 29.97


def test_order_total_small_order():
    assert order_total([(10.00, 2)]) == 20.00


def test_order_total_applies_ten_percent():
    assert order_total([(50.00, 4)]) == 180.00

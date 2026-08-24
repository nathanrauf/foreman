"""Order pricing rules."""


def tier_discount(subtotal):
    """Return the discount rate for an order subtotal.

    Per the pricing spec:
      - $500.00 or more ....... 20% off
      - $100.00 or more ....... 10% off
      - below $100.00 ......... no discount
    """
    if subtotal >= 100:
        return 0.10
    if subtotal >= 500:
        return 0.20
    return 0.0


def line_total(unit_price, quantity):
    """Total for a single order line."""
    return unit_price * quantity


def order_total(lines):
    """Total for an order after the tier discount.

    `lines` is a list of (unit_price, quantity) tuples.
    """
    subtotal = sum(line_total(p, q) for p, q in lines)
    return round(subtotal * (1 - tier_discount(subtotal)), 2)

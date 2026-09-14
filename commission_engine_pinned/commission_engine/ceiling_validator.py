"""
Runtime ceiling validation.

Even though the plan-version publish-time check (plan_version.py) is
designed to make a ceiling breach structurally impossible, this module
provides a second, independent runtime assertion on every single order's
calculated result. Defense in depth: a bug in a calculator must never be
able to silently overpay.

Because Matching Bonus is currently unimplemented, the effective ceiling
enforced here is 35.00% (the full approved pool) — line items will never
approach it while Matching Bonus is stubbed out, but the check itself
must not be loosened just because current totals happen to be lower.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List

from .models import CommissionLineItem, Order

POOL_CEILING_PERCENT = Decimal("35.00")
# Tolerance to absorb per-line-item cent rounding (each line item is
# independently quantized to 2 decimal places).
ROUNDING_TOLERANCE = Decimal("0.05")


class CeilingBreachError(Exception):
    """Raised if a calculated order's total commission would exceed the
    approved 35% Member Commission Pool. This must never happen if the
    plan version passed publish validation and calculators are correct —
    if it fires, treat it as a critical bug, not a case to work around."""


def validate_order_ceiling(order: Order, line_items: List[CommissionLineItem]) -> None:
    total_paid = sum((li.amount for li in line_items), Decimal("0"))
    max_allowed = (order.sale_value * POOL_CEILING_PERCENT / Decimal("100")) + ROUNDING_TOLERANCE

    if total_paid > max_allowed:
        raise CeilingBreachError(
            f"Order {order.order_id}: total calculated commission {total_paid} "
            f"exceeds the maximum allowed {max_allowed} "
            f"({POOL_CEILING_PERCENT}% of sale value {order.sale_value})."
        )
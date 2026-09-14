"""
New edge-case tests for engine.py, filling gaps found during the Final
Integration & Test Phase re-inspection. Every case here exercises
EXISTING, already-written code paths (engine.py lines checked against
source before writing these) — nothing new is invented; these tests only
prove behavior that was previously uncovered by any test.
"""
import unittest
from decimal import Decimal
from datetime import datetime
from dataclasses import replace

from commission_engine.approved_plan import build_approved_plan_v1
from commission_engine.models import Member, Order, AccountStatus
from commission_engine.tree import InMemoryUplineProvider
from commission_engine.rank import InMemoryRankSnapshotProvider
from commission_engine.idempotency import IdempotencyStore
from commission_engine.undistributed import UndistributedFundTracker
from commission_engine.ledger import ImmutableLedger
from commission_engine.engine import CommissionEngine

PERIOD = "2026-08"
FY = 2026


def build_engine(sponsor_map=None, members=None, rank_snapshots=None, plan=None):
    plan = plan or build_approved_plan_v1()
    tree = InMemoryUplineProvider(sponsor_map or {}, members or {})
    ranks = InMemoryRankSnapshotProvider(rank_snapshots or {})
    idem = IdempotencyStore()
    undist = UndistributedFundTracker()
    ledger = ImmutableLedger()
    engine = CommissionEngine(plan, tree, ranks, idem, undist, ledger)
    return engine, ledger, undist


def make_order(order_id="ORDER-1", sale_value="1000.00", is_paid=True, is_commissionable=True):
    return Order(
        order_id=order_id,
        credited_member_id="buyer1",
        sale_value=Decimal(sale_value),
        is_commissionable=is_commissionable,
        is_paid=is_paid,
        order_timestamp=datetime(2026, 8, 15),
    )


class TestUnpaidOrderShortCircuits(unittest.TestCase):
    """Proven from engine.py line 80: `if not order.is_paid or not
    order.is_commissionable:` — never exercised by any prior test."""

    def test_unpaid_order_produces_no_line_items(self):
        engine, ledger, undist = build_engine(
            sponsor_map={"buyer1": "sponsor1"},
            members={"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)},
        )
        order = make_order(is_paid=False)
        result = engine.process_order_payment_confirmed(order, PERIOD, FY)
        self.assertEqual(result.line_items, [])
        self.assertEqual(result.undistributed, [])
        self.assertEqual(result.matching_bonus_status, "NOT_IMPLEMENTED")

    def test_non_commissionable_order_produces_no_line_items(self):
        engine, ledger, undist = build_engine(
            sponsor_map={"buyer1": "sponsor1"},
            members={"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)},
        )
        order = make_order(is_commissionable=False)
        result = engine.process_order_payment_confirmed(order, PERIOD, FY)
        self.assertEqual(result.line_items, [])
        self.assertEqual(result.undistributed, [])

    def test_unpaid_order_is_still_idempotency_reserved(self):
        """Even the short-circuit path reserves the idempotency key
        (engine.py: `self.idempotency_store.reserve(key, result)` inside
        the short-circuit branch) — a second call must report duplicate."""
        engine, ledger, undist = build_engine()
        order = make_order(is_paid=False)
        first = engine.process_order_payment_confirmed(order, PERIOD, FY)
        second = engine.process_order_payment_confirmed(order, PERIOD, FY)
        self.assertFalse(first.was_duplicate)
        self.assertTrue(second.was_duplicate)


class TestMatchingBonusStatusAtEngineLevel(unittest.TestCase):
    """The stub's NOT_IMPLEMENTED marker must surface correctly through
    the full engine result, not just the calculators.py unit level."""

    def test_matching_bonus_status_not_implemented_on_full_calculation(self):
        engine, ledger, undist = build_engine(
            sponsor_map={"buyer1": "sponsor1"},
            members={"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)},
        )
        order = make_order()
        result = engine.process_order_payment_confirmed(order, PERIOD, FY)
        self.assertEqual(result.matching_bonus_status, "NOT_IMPLEMENTED")
        # Updated per approved Business Decision 1 (no longer a bypass —
        # this is the newly-approved expected behavior): no MATCHING_BONUS
        # line item is created (payee/qualification logic is still not
        # implemented), but the reserved 3.50% must now appear as exactly
        # one MATCHING_BONUS UndistributedAmount, not be silently dropped.
        matching_line_items = [li for li in result.line_items if li.commission_type.value == "MATCHING_BONUS"]
        matching_undistributed = [u for u in result.undistributed if u.commission_type.value == "MATCHING_BONUS"]
        self.assertEqual(matching_line_items, [])
        self.assertEqual(len(matching_undistributed), 1)
        self.assertEqual(matching_undistributed[0].reason, "matching_bonus_not_yet_implemented")
        self.assertEqual(matching_undistributed[0].amount, Decimal("35.00"))  # 3.50% of 1000.00

    def test_cached_duplicate_result_preserves_matching_bonus_status(self):
        engine, ledger, undist = build_engine()
        order = make_order()
        first = engine.process_order_payment_confirmed(order, PERIOD, FY)
        second = engine.process_order_payment_confirmed(order, PERIOD, FY)
        self.assertEqual(first.matching_bonus_status, second.matching_bonus_status)


class TestUnpublishedPlanRejectedAtConstruction(unittest.TestCase):
    """Proven from engine.py lines 54-55: `if not plan.is_published: raise
    ValueError(...)` — never exercised by any prior test."""

    def test_engine_rejects_unpublished_plan(self):
        published_plan = build_approved_plan_v1()
        draft_plan = replace(published_plan, is_published=False)
        with self.assertRaises(ValueError):
            build_engine(plan=draft_plan)


class TestZeroValueOrderDoesNotCrash(unittest.TestCase):
    """Edge case: a zero-value order must be processed without error and
    yield zero-amount line items, not raise or divide by zero anywhere."""

    def test_zero_sale_value_produces_zero_amount_line_items(self):
        engine, ledger, undist = build_engine(
            sponsor_map={"buyer1": "sponsor1"},
            members={"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)},
        )
        order = make_order(sale_value="0.00")
        result = engine.process_order_payment_confirmed(order, PERIOD, FY)
        personal = next(li for li in result.line_items if li.commission_type.value == "PERSONAL_SALES")
        self.assertEqual(personal.amount, Decimal("0.00"))
        total_paid = sum((li.amount for li in result.line_items), Decimal("0"))
        self.assertEqual(total_paid, Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()

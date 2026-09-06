import unittest
from decimal import Decimal
from datetime import datetime

from commission_engine.approved_plan import build_approved_plan_v1
from commission_engine.models import (
    Member, Order, AccountStatus, RankQualificationSnapshot,
    CommissionLineItemStatus,
)
from commission_engine.tree import InMemoryUplineProvider
from commission_engine.rank import InMemoryRankSnapshotProvider
from commission_engine.idempotency import IdempotencyStore
from commission_engine.undistributed import UndistributedFundTracker
from commission_engine.ledger import ImmutableLedger
from commission_engine.engine import CommissionEngine

PERIOD = "2026-08"
FY = 2026


def build_engine(sponsor_map=None, members=None, rank_snapshots=None):
    plan = build_approved_plan_v1()
    tree = InMemoryUplineProvider(sponsor_map or {}, members or {})
    ranks = InMemoryRankSnapshotProvider(rank_snapshots or {})
    idem = IdempotencyStore()
    undist = UndistributedFundTracker()
    ledger = ImmutableLedger()
    engine = CommissionEngine(plan, tree, ranks, idem, undist, ledger)
    return engine, ledger, undist


def make_order(order_id="ORDER-1", sale_value="1000.00"):
    return Order(
        order_id=order_id,
        credited_member_id="buyer1",
        sale_value=Decimal(sale_value),
        is_commissionable=True,
        is_paid=True,
        order_timestamp=datetime(2026, 8, 15),
    )


class TestIdempotency(unittest.TestCase):
    def test_processing_same_order_twice_does_not_duplicate_commissions(self):
        sponsor_map = {"buyer1": "sponsor1"}
        members = {"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)}
        engine, ledger, undist = build_engine(sponsor_map, members)
        order = make_order()

        result1 = engine.process_order_payment_confirmed(order, PERIOD, FY)
        result2 = engine.process_order_payment_confirmed(order, PERIOD, FY)

        self.assertFalse(result1.was_duplicate)
        self.assertTrue(result2.was_duplicate)
        # Same line items returned, not doubled.
        self.assertEqual(len(result1.line_items), len(result2.line_items))
        self.assertEqual(
            [li.amount for li in result1.line_items],
            [li.amount for li in result2.line_items],
        )

    def test_different_orders_are_independent(self):
        engine, ledger, undist = build_engine()
        order1 = make_order("ORDER-A")
        order2 = make_order("ORDER-B")
        r1 = engine.process_order_payment_confirmed(order1, PERIOD, FY)
        r2 = engine.process_order_payment_confirmed(order2, PERIOD, FY)
        self.assertFalse(r1.was_duplicate)
        self.assertFalse(r2.was_duplicate)


class TestCeilingEnforcement(unittest.TestCase):
    def test_full_qualification_never_exceeds_35_percent(self):
        """Simulates a fully-qualified deep tree (Example 2 from the
        original business spec) and asserts total payout stays within the
        ceiling. Since Matching Bonus is stubbed, actual max achievable is
        31.50%, not the full 35.00% -- both facts are asserted."""
        sponsor_map = {}
        members = {}
        prev = "buyer1"
        for gen in range(1, 11):
            sid = f"upline{gen}"
            sponsor_map[prev] = sid
            members[sid] = Member(sid, AccountStatus.ACTIVE)
            prev = sid

        rank_snapshots = {
            f"upline1:{PERIOD}": RankQualificationSnapshot("upline1", PERIOD, Decimal("50000"), Decimal("1000000"), 10),
        }
        engine, ledger, undist = build_engine(sponsor_map, members, rank_snapshots)
        order = make_order(sale_value="10000.00")
        result = engine.process_order_payment_confirmed(order, PERIOD, FY)

        total_paid = sum((li.amount for li in result.line_items), Decimal("0"))
        max_35pct = Decimal("10000.00") * Decimal("35.00") / Decimal("100")
        max_31_5pct = Decimal("10000.00") * Decimal("31.50") / Decimal("100")

        self.assertLessEqual(total_paid, max_35pct)
        self.assertLessEqual(total_paid, max_31_5pct + Decimal("0.05"))  # matching bonus excluded


class TestApproveAndCreditLedgerIntegration(unittest.TestCase):
    def test_approve_and_credit_writes_immutable_ledger_entry(self):
        sponsor_map = {"buyer1": "sponsor1"}
        members = {"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)}
        engine, ledger, undist = build_engine(sponsor_map, members)
        order = make_order()
        result = engine.process_order_payment_confirmed(order, PERIOD, FY)

        personal_item = next(li for li in result.line_items if li.commission_type.value == "PERSONAL_SALES")
        self.assertEqual(personal_item.status, CommissionLineItemStatus.PENDING)

        engine.approve_and_credit(personal_item)
        self.assertEqual(personal_item.status, CommissionLineItemStatus.CREDITED)

        balance = ledger.balance_of("buyer1")
        self.assertEqual(balance, Decimal("70.00"))  # 7% of 1000.00

    def test_cannot_credit_a_non_pending_line_item_twice(self):
        sponsor_map = {"buyer1": "sponsor1"}
        members = {"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)}
        engine, ledger, undist = build_engine(sponsor_map, members)
        order = make_order()
        result = engine.process_order_payment_confirmed(order, PERIOD, FY)
        personal_item = next(li for li in result.line_items if li.commission_type.value == "PERSONAL_SALES")

        engine.approve_and_credit(personal_item)
        with self.assertRaises(ValueError):
            engine.approve_and_credit(personal_item)  # already CREDITED -> must reject


class TestUndistributedFundIsFed(unittest.TestCase):
    def test_uncredited_direct_referral_lands_in_undistributed_tracker(self):
        engine, ledger, undist = build_engine()  # no sponsor at all
        order = make_order()
        engine.process_order_payment_confirmed(order, PERIOD, FY)

        records = undist.for_fiscal_year(FY)
        reasons = {r.reason for r in records}
        self.assertIn("no_direct_sponsor_exists", reasons)


if __name__ == "__main__":
    unittest.main()
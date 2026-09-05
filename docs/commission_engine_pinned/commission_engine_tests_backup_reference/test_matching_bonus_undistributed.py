"""
Tests for approved Business Decision 1 — Matching Bonus's reserved 3.50%
must be tracked as Undistributed rather than silently dropped. These
cover the engine-level and Year-End Distribution-level integration
(calculators.py's own unit test for the stub itself lives in
test_calculators.py, TestMatchingBonusStub).
"""
import unittest
from decimal import Decimal
from datetime import datetime

from commission_engine.approved_plan import build_approved_plan_v1
from commission_engine.models import Member, Order, AccountStatus, CommissionType, Rank
from commission_engine.tree import InMemoryUplineProvider
from commission_engine.rank import InMemoryRankSnapshotProvider
from commission_engine.idempotency import IdempotencyStore
from commission_engine.undistributed import UndistributedFundTracker
from commission_engine.ledger import ImmutableLedger
from commission_engine.engine import CommissionEngine
from commission_engine.ceiling_validator import POOL_CEILING_PERCENT, ROUNDING_TOLERANCE
from commission_engine.year_end import run_year_end_distribution, YearEndMemberRankSnapshot

PERIOD = "2026-08"
FY = 2026


def make_order(order_id="ORDER-1", sale_value="1000.00"):
    return Order(
        order_id=order_id,
        credited_member_id="buyer1",
        sale_value=Decimal(sale_value),
        is_commissionable=True,
        is_paid=True,
        order_timestamp=datetime(2026, 8, 15),
    )


def build_engine(sponsor_map=None, members=None, undist=None, ledger=None):
    plan = build_approved_plan_v1()
    tree = InMemoryUplineProvider(sponsor_map or {}, members or {})
    ranks = InMemoryRankSnapshotProvider({})
    idem = IdempotencyStore()
    undist = undist if undist is not None else UndistributedFundTracker()
    ledger = ledger if ledger is not None else ImmutableLedger()
    engine = CommissionEngine(plan, tree, ranks, idem, undist, ledger)
    return engine, undist, ledger, plan


class TestMatchingBonusReachesUndistributedTracker(unittest.TestCase):
    def test_matching_bonus_amount_recorded_in_tracker(self):
        engine, undist, ledger, plan = build_engine(
            sponsor_map={"buyer1": "sponsor1"},
            members={"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)},
        )
        order = make_order(sale_value="1000.00")
        engine.process_order_payment_confirmed(order, PERIOD, FY)

        records = undist.for_fiscal_year(FY)
        matching_records = [r for r in records if r.commission_type == CommissionType.MATCHING_BONUS]
        self.assertEqual(len(matching_records), 1)
        self.assertEqual(matching_records[0].amount, Decimal("35.00"))
        self.assertEqual(matching_records[0].reason, "matching_bonus_not_yet_implemented")

    def test_matching_bonus_visible_in_order_commission_result(self):
        engine, undist, ledger, plan = build_engine(
            sponsor_map={"buyer1": "sponsor1"},
            members={"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)},
        )
        order = make_order(sale_value="2000.00")
        result = engine.process_order_payment_confirmed(order, PERIOD, FY)
        matching = [u for u in result.undistributed if u.commission_type == CommissionType.MATCHING_BONUS]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].amount, Decimal("70.00"))  # 3.50% of 2000.00


class TestCeilingUnaffected(unittest.TestCase):
    """The 3.50% is tracked as undistributed, never as a paid line item,
    so it must have zero effect on the runtime ceiling check."""

    def test_ceiling_check_still_passes_with_matching_bonus_tracked(self):
        engine, undist, ledger, plan = build_engine(
            sponsor_map={"buyer1": "sponsor1"},
            members={"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)},
        )
        order = make_order(sale_value="5000.00")
        result = engine.process_order_payment_confirmed(order, PERIOD, FY)
        total_paid = sum((li.amount for li in result.line_items), Decimal("0"))
        max_allowed = (order.sale_value * POOL_CEILING_PERCENT / Decimal("100")) + ROUNDING_TOLERANCE
        self.assertLessEqual(total_paid, max_allowed)
        self.assertFalse(any(li.commission_type == CommissionType.MATCHING_BONUS for li in result.line_items))


class TestMatchingBonusFlowsIntoYearEndDistribution(unittest.TestCase):
    """End-to-end: the tracked 3.50% must actually participate in the
    annual Year-End Distribution total, per spec's Undistributed
    Commission Fund Treatment."""

    def test_matching_bonus_amount_included_in_year_end_total_fund(self):
        undist = UndistributedFundTracker()
        ledger = ImmutableLedger()
        engine, _, _, plan = build_engine(
            sponsor_map={"buyer1": "sponsor1"},
            members={"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)},
            undist=undist, ledger=ledger,
        )
        order = make_order(sale_value="10000.00")
        engine.process_order_payment_confirmed(order, PERIOD, FY)

        matching_amount = sum(
            r.amount for r in undist.for_fiscal_year(FY)
            if r.commission_type == CommissionType.MATCHING_BONUS
        )
        self.assertEqual(matching_amount, Decimal("350.00"))  # 3.50% of 10000.00

        summary = run_year_end_distribution(
            FY, plan, undist,
            [YearEndMemberRankSnapshot("buyer1", Rank.NONE)],
            ledger,
        )
        self.assertGreaterEqual(summary.total_fund, Decimal("350.00"))


if __name__ == "__main__":
    unittest.main()

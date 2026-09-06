"""
Tests for approved Business Decision 2 — Plan Version Lock.

An order's applicable PlanVersion permanently locks at its first
payment-confirmed processing. A later attempt to process the SAME order
under a DIFFERENT PlanVersion must be explicitly rejected
(PlanVersionLockedError) — never silently recalculated, and never
conflated with the existing same-version idempotency ("duplicate") case.
"""
import unittest
from decimal import Decimal
from datetime import datetime
from dataclasses import replace

from commission_engine.approved_plan import build_approved_plan_v1
from commission_engine.models import Member, Order, AccountStatus
from commission_engine.tree import InMemoryUplineProvider
from commission_engine.rank import InMemoryRankSnapshotProvider
from commission_engine.idempotency import IdempotencyStore, PlanVersionLockedError
from commission_engine.undistributed import UndistributedFundTracker
from commission_engine.ledger import ImmutableLedger
from commission_engine.engine import CommissionEngine

PERIOD = "2026-08"
FY = 2026


def make_order(order_id="ORDER-1", sale_value="1000.00", is_paid=True, is_commissionable=True):
    return Order(
        order_id=order_id,
        credited_member_id="buyer1",
        sale_value=Decimal(sale_value),
        is_commissionable=is_commissionable,
        is_paid=is_paid,
        order_timestamp=datetime(2026, 8, 15),
    )


def build_engine(idem_store, plan, sponsor_map=None, members=None, rank_snapshots=None, ledger=None, undist=None):
    tree = InMemoryUplineProvider(sponsor_map or {}, members or {})
    ranks = InMemoryRankSnapshotProvider(rank_snapshots or {})
    undist = undist if undist is not None else UndistributedFundTracker()
    ledger = ledger if ledger is not None else ImmutableLedger()
    return CommissionEngine(plan, tree, ranks, idem_store, undist, ledger)


class TestSameVersionIdempotencyUnaffected(unittest.TestCase):
    """Regression check: existing same-PlanVersion idempotency behavior
    must remain completely intact after the lock mechanism is added."""

    def test_same_order_same_plan_version_twice_still_marked_duplicate(self):
        plan = build_approved_plan_v1()
        idem = IdempotencyStore()
        engine = build_engine(
            idem, plan,
            sponsor_map={"buyer1": "sponsor1"},
            members={"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)},
        )
        order = make_order()
        first = engine.process_order_payment_confirmed(order, PERIOD, FY)
        second = engine.process_order_payment_confirmed(order, PERIOD, FY)

        self.assertFalse(first.was_duplicate)
        self.assertTrue(second.was_duplicate)
        self.assertEqual(len(first.line_items), len(second.line_items))


class TestDifferentPlanVersionRejected(unittest.TestCase):
    """Core Decision 2 behavior: a different PlanVersion for an
    already-processed order must raise PlanVersionLockedError, not
    silently recalculate and not silently return a duplicate result."""

    def test_different_plan_version_raises_explicit_error(self):
        plan_v1 = build_approved_plan_v1()
        plan_v2 = replace(plan_v1, plan_version_id="PLAN_V2_2027")
        idem = IdempotencyStore()  # shared across both engines, as a real deployment would share it
        sponsor_map = {"buyer1": "sponsor1"}
        members = {"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)}
        ledger = ImmutableLedger()
        undist = UndistributedFundTracker()

        engine_v1 = build_engine(idem, plan_v1, sponsor_map, members, ledger=ledger, undist=undist)
        engine_v2 = build_engine(idem, plan_v2, sponsor_map, members, ledger=ledger, undist=undist)

        order = make_order("ORDER-LOCK-1")
        r1 = engine_v1.process_order_payment_confirmed(order, PERIOD, FY)
        self.assertFalse(r1.was_duplicate)
        self.assertEqual(r1.plan_version_id, "PLAN_V1_2026")

        with self.assertRaises(PlanVersionLockedError):
            engine_v2.process_order_payment_confirmed(order, PERIOD, FY)

    def test_no_new_line_items_or_records_created_on_rejected_attempt(self):
        """The rejected attempt must not partially apply anything -- no
        new undistributed records, no new line items, nothing in the
        tracker beyond what the first (successful) call produced."""
        plan_v1 = build_approved_plan_v1()
        plan_v2 = replace(plan_v1, plan_version_id="PLAN_V2_2027")
        idem = IdempotencyStore()
        sponsor_map = {"buyer1": "sponsor1"}
        members = {"sponsor1": Member("sponsor1", AccountStatus.ACTIVE)}
        ledger = ImmutableLedger()
        undist = UndistributedFundTracker()

        engine_v1 = build_engine(idem, plan_v1, sponsor_map, members, ledger=ledger, undist=undist)
        engine_v2 = build_engine(idem, plan_v2, sponsor_map, members, ledger=ledger, undist=undist)

        order = make_order("ORDER-LOCK-2")
        engine_v1.process_order_payment_confirmed(order, PERIOD, FY)
        records_after_first_call = len(undist.all_records())

        with self.assertRaises(PlanVersionLockedError):
            engine_v2.process_order_payment_confirmed(order, PERIOD, FY)

        self.assertEqual(len(undist.all_records()), records_after_first_call)

    def test_rejection_happens_even_for_a_brand_new_engine_instance_sharing_the_store(self):
        """Locking is a property of the shared IdempotencyStore, not of
        any single CommissionEngine instance."""
        plan_v1 = build_approved_plan_v1()
        plan_v2 = replace(plan_v1, plan_version_id="PLAN_V2_2027")
        idem = IdempotencyStore()
        order = make_order("ORDER-LOCK-3")

        engine_v1 = build_engine(idem, plan_v1)
        engine_v1.process_order_payment_confirmed(order, PERIOD, FY)

        engine_v2 = build_engine(idem, plan_v2)  # freshly constructed
        with self.assertRaises(PlanVersionLockedError):
            engine_v2.process_order_payment_confirmed(order, PERIOD, FY)


class TestNewOrderNeverFalselyBlocked(unittest.TestCase):
    def test_brand_new_order_id_processes_normally_under_any_plan_version(self):
        plan_v2 = replace(build_approved_plan_v1(), plan_version_id="PLAN_V2_2027")
        idem = IdempotencyStore()
        engine = build_engine(idem, plan_v2)
        order = make_order("ORDER-NEVER-SEEN")
        result = engine.process_order_payment_confirmed(order, PERIOD, FY)
        self.assertFalse(result.was_duplicate)


class TestShortCircuitPathAlsoLocks(unittest.TestCase):
    """Per the confirmed design choice: the unpaid/non-commissionable
    short-circuit path also locks the PlanVersion, since it uses the same
    PAYMENT_CONFIRMED event_type and the same reserve() mechanism."""

    def test_unpaid_order_locked_then_different_version_rejected(self):
        plan_v1 = build_approved_plan_v1()
        plan_v2 = replace(plan_v1, plan_version_id="PLAN_V2_2027")
        idem = IdempotencyStore()
        order = make_order("ORDER-UNPAID-LOCK", is_paid=False)

        engine_v1 = build_engine(idem, plan_v1)
        r1 = engine_v1.process_order_payment_confirmed(order, PERIOD, FY)
        self.assertEqual(r1.line_items, [])

        engine_v2 = build_engine(idem, plan_v2)
        with self.assertRaises(PlanVersionLockedError):
            engine_v2.process_order_payment_confirmed(order, PERIOD, FY)


if __name__ == "__main__":
    unittest.main()

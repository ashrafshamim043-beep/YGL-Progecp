import unittest
from decimal import Decimal
from datetime import datetime

from commission_engine.approved_plan import build_approved_plan_v1
from commission_engine.models import (
    Member, Order, AccountStatus, Rank, RankQualificationSnapshot, CommissionType,
)
from commission_engine.tree import InMemoryUplineProvider
from commission_engine.rank import InMemoryRankSnapshotProvider
from commission_engine import calculators

PERIOD = "2026-08"
FY = 2026


def make_order(sale_value="100.00", credited_member_id="buyer1"):
    return Order(
        order_id="ORDER-1",
        credited_member_id=credited_member_id,
        sale_value=Decimal(sale_value),
        is_commissionable=True,
        is_paid=True,
        order_timestamp=datetime(2026, 8, 15),
    )


def build_deep_chain(depth=12):
    """buyer1 -> sponsor at gen1..depth"""
    sponsor_map = {}
    members = {}
    prev = "buyer1"
    for gen in range(1, depth + 1):
        sponsor_id = f"upline{gen}"
        sponsor_map[prev] = sponsor_id
        members[sponsor_id] = Member(sponsor_id, AccountStatus.ACTIVE)
        prev = sponsor_id
    return InMemoryUplineProvider(sponsor_map, members)


class TestPersonalAndDirect(unittest.TestCase):
    def setUp(self):
        self.plan = build_approved_plan_v1()

    def test_personal_sales_always_7_percent_unconditional(self):
        order = make_order("100.00")
        li = calculators.calculate_personal_sales(order, self.plan)
        self.assertEqual(li.payee_member_id, "buyer1")
        self.assertEqual(li.amount, Decimal("7.00"))

    def test_direct_referral_paid_to_nearest_sponsor_unconditionally(self):
        order = make_order("100.00")
        tree = build_deep_chain(depth=3)
        upline = tree.get_upline_snapshot("buyer1", order.order_timestamp)
        li, ud = calculators.calculate_direct_referral(order, upline, self.plan, FY)
        self.assertEqual(len(li), 1)
        self.assertEqual(li[0].payee_member_id, "upline1")
        self.assertEqual(li[0].amount, Decimal("7.00"))
        self.assertEqual(ud, [])

    def test_direct_referral_undistributed_when_no_sponsor(self):
        order = make_order("100.00")
        tree = InMemoryUplineProvider({}, {})  # no sponsor at all
        upline = tree.get_upline_snapshot("buyer1", order.order_timestamp)
        li, ud = calculators.calculate_direct_referral(order, upline, self.plan, FY)
        self.assertEqual(li, [])
        self.assertEqual(len(ud), 1)
        self.assertEqual(ud[0].reason, "no_direct_sponsor_exists")
        self.assertEqual(ud[0].amount, Decimal("7.00"))


class TestUnilevel(unittest.TestCase):
    def setUp(self):
        self.plan = build_approved_plan_v1()

    def test_full_10_generation_payout_matches_fixed_table(self):
        order = make_order("100.00")
        tree = build_deep_chain(depth=12)  # deeper than 10 on purpose
        upline = tree.get_upline_snapshot("buyer1", order.order_timestamp, max_depth=10)
        li, ud = calculators.calculate_unilevel(order, upline, self.plan, FY)

        self.assertEqual(len(li), 10)  # never more than 10
        expected_rates = [
            Decimal("2.00"), Decimal("1.50"), Decimal("1.25"), Decimal("1.00"),
            Decimal("0.75"), Decimal("0.60"), Decimal("0.50"), Decimal("0.40"),
            Decimal("0.40"), Decimal("0.35"),
        ]
        for i, item in enumerate(sorted(li, key=lambda x: x.generation)):
            self.assertEqual(item.generation, i + 1)
            self.assertEqual(item.payee_member_id, f"upline{i + 1}")
            expected_amount = (Decimal("100.00") * expected_rates[i] / Decimal("100")).quantize(Decimal("0.01"))
            self.assertEqual(item.amount, expected_amount)

        total_paid = sum((x.amount for x in li), Decimal("0"))
        self.assertEqual(total_paid, Decimal("8.75"))  # exact match to allocation
        self.assertEqual(ud, [])  # nothing undistributed when all 10 exist & active

    def test_generation_11_and_beyond_never_paid(self):
        order = make_order("100.00")
        tree = build_deep_chain(depth=15)
        upline = tree.get_upline_snapshot("buyer1", order.order_timestamp, max_depth=10)
        li, _ = calculators.calculate_unilevel(order, upline, self.plan, FY)
        payees = {x.payee_member_id for x in li}
        self.assertNotIn("upline11", payees)
        self.assertNotIn("upline15", payees)

    def test_short_chain_creates_undistributed_for_missing_generations_no_redistribution(self):
        order = make_order("100.00")
        tree = build_deep_chain(depth=3)  # only 3 generations exist
        upline = tree.get_upline_snapshot("buyer1", order.order_timestamp, max_depth=10)
        li, ud = calculators.calculate_unilevel(order, upline, self.plan, FY)

        self.assertEqual(len(li), 3)
        self.assertEqual(len(ud), 7)  # generations 4..10
        for record in ud:
            self.assertEqual(record.reason, "generation_does_not_exist")

        # Total paid must be strictly less than 8.75% -- the shortfall is
        # NOT redistributed among the 3 paid generations.
        total_paid = sum((x.amount for x in li), Decimal("0"))
        self.assertLess(total_paid, Decimal("8.75"))
        expected_paid = Decimal("2.00") + Decimal("1.50") + Decimal("1.25")
        expected_paid_amount = (Decimal("100.00") * expected_paid / Decimal("100")).quantize(Decimal("0.01"))
        self.assertEqual(total_paid, expected_paid_amount)

    def test_suspended_upline_member_is_skipped_and_logged(self):
        order = make_order("100.00")
        tree = build_deep_chain(depth=5)
        tree.members["upline2"] = Member("upline2", AccountStatus.SUSPENDED)
        upline = tree.get_upline_snapshot("buyer1", order.order_timestamp, max_depth=10)
        li, ud = calculators.calculate_unilevel(order, upline, self.plan, FY)

        paid_generations = {x.generation for x in li}
        self.assertNotIn(2, paid_generations)
        reasons = {u.reason for u in ud if u.generation == 2}
        self.assertIn("generation_member_not_active", reasons)


class TestRankAndTeamBonusPayeeScope(unittest.TestCase):
    def setUp(self):
        self.plan = build_approved_plan_v1()

    def _rank_snapshot(self, member_id, personal, team, directs):
        return RankQualificationSnapshot(member_id, PERIOD, Decimal(personal), Decimal(team), directs)

    def test_rank_bonus_paid_to_nearest_qualifying_upline_only(self):
        order = make_order("1000.00")
        tree = build_deep_chain(depth=5)
        upline = tree.get_upline_snapshot("buyer1", order.order_timestamp)

        # generation 1 and 2 don't qualify for any rank; generation 3 qualifies Bronze;
        # generation 4 qualifies Diamond (should NOT be paid -- gen 3 is nearer).
        snapshots = {
            f"upline1:{PERIOD}": self._rank_snapshot("upline1", 0, 0, 0),
            f"upline2:{PERIOD}": self._rank_snapshot("upline2", 0, 0, 0),
            f"upline3:{PERIOD}": self._rank_snapshot("upline3", 5000, 25000, 2),  # Bronze
            f"upline4:{PERIOD}": self._rank_snapshot("upline4", 50000, 1000000, 10),  # Diamond
        }
        rank_provider = InMemoryRankSnapshotProvider(snapshots)

        li, ud = calculators.calculate_rank_bonus(order, upline, rank_provider, PERIOD, self.plan, FY)
        self.assertEqual(len(li), 1)
        self.assertEqual(li[0].payee_member_id, "upline3")
        self.assertEqual(li[0].rank, Rank.BRONZE)
        expected = (Decimal("1000.00") * Decimal("0.50") / Decimal("100")).quantize(Decimal("0.01"))
        self.assertEqual(li[0].amount, expected)
        self.assertEqual(ud, [])  # payee found -> no undistributed record (locked correction)

        payees = {x.payee_member_id for x in li}
        self.assertNotIn("upline4", payees)  # Diamond upline never paid -- gen3 stopped the search

    def test_rank_bonus_undistributed_when_no_one_qualifies(self):
        order = make_order("1000.00")
        tree = build_deep_chain(depth=3)
        upline = tree.get_upline_snapshot("buyer1", order.order_timestamp)
        rank_provider = InMemoryRankSnapshotProvider({})  # nobody has any snapshot

        li, ud = calculators.calculate_rank_bonus(order, upline, rank_provider, PERIOD, self.plan, FY)
        self.assertEqual(li, [])
        self.assertEqual(len(ud), 1)
        self.assertEqual(ud[0].reason, "no_qualifying_rank_payee_in_upline")

    def test_team_bonus_enrollment_only_does_not_qualify(self):
        order = make_order("1000.00")
        tree = build_deep_chain(depth=2)
        upline = tree.get_upline_snapshot("buyer1", order.order_timestamp)
        # upline1 has active_directs but ZERO team sales volume -- enrollment only.
        snapshots = {
            f"upline1:{PERIOD}": self._rank_snapshot("upline1", 5000, 0, 5),
        }
        rank_provider = InMemoryRankSnapshotProvider(snapshots)
        li, ud = calculators.calculate_team_bonus(order, upline, rank_provider, PERIOD, self.plan, FY)
        self.assertEqual(li, [])
        self.assertEqual(ud[0].reason, "no_qualifying_team_bonus_payee_in_upline")

    def test_team_bonus_paid_to_nearest_genuinely_qualified_leader(self):
        order = make_order("1000.00")
        tree = build_deep_chain(depth=3)
        upline = tree.get_upline_snapshot("buyer1", order.order_timestamp)
        snapshots = {
            f"upline1:{PERIOD}": self._rank_snapshot("upline1", 5000, 0, 5),  # enrollment only -> skip
            f"upline2:{PERIOD}": self._rank_snapshot("upline2", 10000, 75000, 3),  # genuine Silver
        }
        rank_provider = InMemoryRankSnapshotProvider(snapshots)
        li, ud = calculators.calculate_team_bonus(order, upline, rank_provider, PERIOD, self.plan, FY)
        self.assertEqual(len(li), 1)
        self.assertEqual(li[0].payee_member_id, "upline2")
        self.assertEqual(li[0].rank, Rank.SILVER)
        expected = (Decimal("1000.00") * Decimal("0.50") / Decimal("100")).quantize(Decimal("0.01"))
        self.assertEqual(li[0].amount, expected)


class TestMatchingBonusStub(unittest.TestCase):
    def test_matching_bonus_is_fully_stubbed(self):
        # Updated per approved Business Decision 1 — this is not a test
        # bypass; it verifies the newly-approved expected behavior:
        # payee/qualification logic is still NOT implemented (status stays
        # NOT_IMPLEMENTED, zero CommissionLineItems), but the reserved
        # 3.50% must now be tracked as a single UndistributedAmount
        # instead of silently disappearing.
        plan = build_approved_plan_v1()
        order = make_order("1000.00")
        result = calculators.calculate_matching_bonus_STUB(order, plan, FY)

        self.assertEqual(result.status, "NOT_IMPLEMENTED")
        self.assertEqual(result.line_items, [])
        self.assertEqual(result.allocation_reserved_percent, Decimal("3.50"))

        self.assertEqual(len(result.undistributed), 1)
        undistributed_amount = result.undistributed[0]
        self.assertEqual(undistributed_amount.amount, Decimal("35.00"))  # 3.50% of 1000.00
        self.assertEqual(undistributed_amount.commission_type, CommissionType.MATCHING_BONUS)
        self.assertEqual(undistributed_amount.reason, "matching_bonus_not_yet_implemented")
        self.assertEqual(undistributed_amount.fiscal_year, FY)
        self.assertEqual(undistributed_amount.order_id, order.order_id)
        self.assertEqual(undistributed_amount.plan_version_id, plan.plan_version_id)


if __name__ == "__main__":
    unittest.main()
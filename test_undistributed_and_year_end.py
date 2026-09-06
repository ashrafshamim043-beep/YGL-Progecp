import unittest
from decimal import Decimal

from commission_engine.approved_plan import build_approved_plan_v1
from commission_engine.models import Rank, UndistributedAmount, CommissionType
from commission_engine.undistributed import UndistributedFundTracker
from commission_engine.ledger import ImmutableLedger
from commission_engine.year_end import run_year_end_distribution, YearEndMemberRankSnapshot

FY = 2026


def seed_undistributed(tracker, total_amount: Decimal):
    tracker.record(UndistributedAmount(
        order_id="ORDER-X",
        plan_version_id="PLAN_V1_2026",
        commission_type=CommissionType.UNILEVEL,
        amount=total_amount,
        reason="generation_does_not_exist",
        fiscal_year=FY,
        generation=7,
    ))


class TestYearEndFiftyFiftySplit(unittest.TestCase):
    def test_fund_splits_exactly_50_50(self):
        plan = build_approved_plan_v1()
        tracker = UndistributedFundTracker()
        seed_undistributed(tracker, Decimal("100000.00"))
        ledger = ImmutableLedger()

        snapshots = [
            YearEndMemberRankSnapshot("diamond_member_1", Rank.DIAMOND),
        ]
        summary = run_year_end_distribution(FY, plan, tracker, snapshots, ledger)

        self.assertEqual(summary.total_fund, Decimal("100000.00"))
        self.assertEqual(summary.company_share, Decimal("50000.00"))
        self.assertEqual(summary.member_share_pool, Decimal("50000.00"))


class TestPerRankTierShares(unittest.TestCase):
    def test_diamond_tier_gets_30_percent_of_member_pool(self):
        plan = build_approved_plan_v1()
        tracker = UndistributedFundTracker()
        seed_undistributed(tracker, Decimal("100000.00"))
        ledger = ImmutableLedger()

        snapshots = [YearEndMemberRankSnapshot("diamond_member_1", Rank.DIAMOND)]
        summary = run_year_end_distribution(FY, plan, tracker, snapshots, ledger)

        # member_share_pool = 50000, Diamond share = 30% = 15000
        self.assertEqual(summary.per_rank_tier_amount[Rank.DIAMOND], Decimal("15000.00"))
        self.assertEqual(summary.per_rank_tier_amount[Rank.BRONZE], Decimal("5000.00"))  # 10%


class TestWithinTierEqualSplit(unittest.TestCase):
    def test_diamond_example_from_business_spec(self):
        """Reproduces the business-approved example: Diamond allocation
        ৳300,000, 10 eligible Diamond members -> ৳30,000 each."""
        plan = build_approved_plan_v1()
        tracker = UndistributedFundTracker()
        # member_share_pool must be 1,000,000 so that Diamond's 30% = 300,000
        seed_undistributed(tracker, Decimal("2000000.00"))
        ledger = ImmutableLedger()

        snapshots = [YearEndMemberRankSnapshot(f"diamond_{i}", Rank.DIAMOND) for i in range(10)]
        summary = run_year_end_distribution(FY, plan, tracker, snapshots, ledger)

        self.assertEqual(summary.member_share_pool, Decimal("1000000.00"))
        self.assertEqual(summary.per_rank_tier_amount[Rank.DIAMOND], Decimal("300000.00"))

        for i in range(10):
            balance = ledger.balance_of(f"diamond_{i}")
            self.assertEqual(balance, Decimal("30000.00"))


class TestEmptyTierGoesToCompany(unittest.TestCase):
    def test_no_platinum_members_sends_platinum_share_to_company(self):
        plan = build_approved_plan_v1()
        tracker = UndistributedFundTracker()
        seed_undistributed(tracker, Decimal("100000.00"))
        ledger = ImmutableLedger()

        # No Platinum-ranked members present at all.
        snapshots = [YearEndMemberRankSnapshot("bronze_1", Rank.BRONZE)]
        summary = run_year_end_distribution(FY, plan, tracker, snapshots, ledger)

        self.assertIn(Rank.PLATINUM, summary.empty_tiers)
        self.assertIn(Rank.DIAMOND, summary.empty_tiers)
        self.assertIn(Rank.SILVER, summary.empty_tiers)
        self.assertIn(Rank.GOLD, summary.empty_tiers)

        # Platinum's tier amount (25% of 50000 = 12500) must appear as a
        # COMPANY ledger entry with the empty_tier reason, and must NOT be
        # redistributed to Bronze (Bronze member gets exactly Bronze's own
        # 10% share, not more).
        platinum_amount = summary.per_rank_tier_amount[Rank.PLATINUM]
        self.assertEqual(platinum_amount, Decimal("12500.00"))

        company_entries = [e for e in ledger.all_entries() if e.member_id == "COMPANY"]
        empty_tier_platinum_entries = [e for e in company_entries if e.reason == "empty_tier:PLATINUM"]
        self.assertEqual(len(empty_tier_platinum_entries), 1)
        self.assertEqual(empty_tier_platinum_entries[0].amount, platinum_amount)

        bronze_balance = ledger.balance_of("bronze_1")
        self.assertEqual(bronze_balance, Decimal("5000.00"))  # exactly Bronze's own 10% share


class TestAntiDoubleCounting(unittest.TestCase):
    def test_diamond_member_only_appears_in_diamond_tier(self):
        plan = build_approved_plan_v1()
        tracker = UndistributedFundTracker()
        seed_undistributed(tracker, Decimal("100000.00"))
        ledger = ImmutableLedger()

        snapshots = [YearEndMemberRankSnapshot("member_x", Rank.DIAMOND)]
        run_year_end_distribution(FY, plan, tracker, snapshots, ledger)

        entries = ledger.entries_for("member_x")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].reason, "year_end_rank_distribution:DIAMOND")


if __name__ == "__main__":
    unittest.main()
import unittest
from decimal import Decimal
from dataclasses import replace

from commission_engine.approved_plan import build_approved_plan_v1
from commission_engine.plan_version import validate_and_publish, PlanVersionValidationError
from commission_engine.models import UnilevelGenerationRate, RankRate, Rank


class TestPlanVersionValidation(unittest.TestCase):
    def test_approved_plan_publishes_successfully(self):
        plan = build_approved_plan_v1()
        self.assertTrue(plan.is_published)

    def test_approved_plan_component_sums(self):
        plan = build_approved_plan_v1()
        total = (
            plan.personal_sales_rate + plan.direct_referral_rate
            + plan.unilevel_allocation + plan.rank_bonus_allocation
            + plan.team_bonus_allocation + plan.matching_bonus_allocation
        )
        self.assertEqual(total, Decimal("35.00"))

        unilevel_sum = sum((g.rate_percent for g in plan.unilevel_generation_rates), Decimal("0"))
        self.assertEqual(unilevel_sum, Decimal("8.75"))

        rank_sum = sum((r.rate_percent for r in plan.rank_bonus_rates), Decimal("0"))
        self.assertEqual(rank_sum, Decimal("5.25"))

        team_sum = sum((r.rate_percent for r in plan.team_bonus_rates), Decimal("0"))
        self.assertEqual(team_sum, Decimal("3.50"))

        year_end_sum = sum((r.share_percent_of_member_pool for r in plan.year_end_rank_shares), Decimal("0"))
        self.assertEqual(year_end_sum, Decimal("100"))

    def test_rejects_plan_exceeding_35_percent_ceiling(self):
        draft = replace(build_approved_plan_v1(), is_published=False, matching_bonus_allocation=Decimal("5.00"))
        # 7 + 7 + 8.75 + 5.25 + 3.50 + 5.00 = 36.50 -> must be rejected
        with self.assertRaises(PlanVersionValidationError):
            validate_and_publish(draft)

    def test_rejects_unilevel_rates_exceeding_declared_allocation(self):
        bad_rates = [UnilevelGenerationRate(1, Decimal("50.00"))]  # way over 8.75%
        draft = replace(build_approved_plan_v1(), is_published=False, unilevel_generation_rates=bad_rates)
        with self.assertRaises(PlanVersionValidationError):
            validate_and_publish(draft)

    def test_rejects_year_end_shares_not_summing_to_100(self):
        from commission_engine.models import YearEndRankShare
        bad_shares = [YearEndRankShare(Rank.BRONZE, Decimal("50"))]  # only 50%, not 100%
        draft = replace(build_approved_plan_v1(), is_published=False, year_end_rank_shares=bad_shares)
        with self.assertRaises(PlanVersionValidationError):
            validate_and_publish(draft)


if __name__ == "__main__":
    unittest.main()
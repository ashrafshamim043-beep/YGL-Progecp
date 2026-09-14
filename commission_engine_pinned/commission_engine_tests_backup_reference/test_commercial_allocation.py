import unittest
from decimal import Decimal
from dataclasses import replace

from commission_engine.approved_plan import build_approved_plan_v1
from commission_engine.approved_rank_reward_plan import build_approved_rank_reward_plan_v1
from commission_engine.approved_commercial_allocation import build_approved_commercial_allocation_v1
from commission_engine.commercial_allocation import (
    CommercialAllocationPlanVersion,
    CommercialAllocationValidationError,
    validate_and_publish_commercial_allocation,
)


class TestApprovedCommercialAllocation(unittest.TestCase):
    """The real, business-approved combination — must publish and total
    exactly 100.00%."""

    def test_approved_allocation_publishes_successfully(self):
        plan = build_approved_commercial_allocation_v1()
        self.assertTrue(plan.is_published)

    def test_approved_allocation_grand_total_is_exactly_100_percent(self):
        plan = build_approved_commercial_allocation_v1()
        self.assertEqual(plan.grand_total(), Decimal("100.00"))

    def test_phase7_and_rank_reward_components_match_approved_values(self):
        plan = build_approved_commercial_allocation_v1()
        self.assertEqual(plan.phase7_compensation_total(), Decimal("35.00"))
        self.assertEqual(plan.rank_reward_pool_total(), Decimal("10.00"))


class TestRejectsUnpublishedReferences(unittest.TestCase):
    """Proven rule (commercial_allocation.py, directly readable): both
    referenced sub-plans must already be published before Commercial
    Allocation can publish."""

    def _draft_with(self, phase7_plan, reward_plan):
        return CommercialAllocationPlanVersion(
            commercial_plan_version_id="TEST_UNPUBLISHED",
            is_published=False,
            product_cost_rate=Decimal("40.00"),
            marketing_rate=Decimal("4.50"),
            courier_logistics_rate=Decimal("3.50"),
            payment_system_other_rate=Decimal("2.00"),
            company_profit_rate=Decimal("5.00"),
            phase7_plan_version=phase7_plan,
            rank_reward_pool_plan_version=reward_plan,
        )

    def test_rejects_unpublished_phase7_plan(self):
        phase7_draft = replace(build_approved_plan_v1(), is_published=False)
        reward_plan = build_approved_rank_reward_plan_v1()
        draft = self._draft_with(phase7_draft, reward_plan)
        with self.assertRaises(CommercialAllocationValidationError):
            validate_and_publish_commercial_allocation(draft)

    def test_rejects_unpublished_reward_pool_plan(self):
        phase7_plan = build_approved_plan_v1()
        reward_draft = replace(build_approved_rank_reward_plan_v1(), is_published=False)
        draft = self._draft_with(phase7_plan, reward_draft)
        with self.assertRaises(CommercialAllocationValidationError):
            validate_and_publish_commercial_allocation(draft)


class TestRejectsWrongGrandTotal(unittest.TestCase):
    """Proven rule: grand_total() must equal exactly 100.00%, not
    approximately, not less, not more."""

    def test_grand_total_not_100_percent_is_rejected(self):
        phase7_plan = build_approved_plan_v1()
        reward_plan = build_approved_rank_reward_plan_v1()
        draft = CommercialAllocationPlanVersion(
            commercial_plan_version_id="TEST_WRONG_TOTAL",
            is_published=False,
            product_cost_rate=Decimal("40.00"),
            marketing_rate=Decimal("4.50"),
            courier_logistics_rate=Decimal("3.50"),
            payment_system_other_rate=Decimal("2.00"),
            company_profit_rate=Decimal("4.00"),  # 1.00% short of 100%
            phase7_plan_version=phase7_plan,
            rank_reward_pool_plan_version=reward_plan,
        )
        with self.assertRaises(CommercialAllocationValidationError):
            validate_and_publish_commercial_allocation(draft)


if __name__ == "__main__":
    unittest.main()

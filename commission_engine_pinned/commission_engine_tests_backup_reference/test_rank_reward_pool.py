import unittest
from decimal import Decimal
from dataclasses import replace

from commission_engine.models import Rank
from commission_engine.rank_reward_pool import (
    RankRewardPoolPlanVersion,
    RankRewardRate,
    RankRewardPoolValidationError,
    validate_and_publish_reward_pool,
)
from commission_engine.approved_rank_reward_plan import build_approved_rank_reward_plan_v1


class TestApprovedRankRewardPlan(unittest.TestCase):
    """Valid approved plan — exercises the real, business-approved input."""

    def test_approved_plan_publishes_successfully(self):
        plan = build_approved_rank_reward_plan_v1()
        self.assertTrue(plan.is_published)

    def test_approved_plan_has_five_ranks_at_two_percent_each(self):
        plan = build_approved_rank_reward_plan_v1()
        self.assertEqual(len(plan.rank_rates), 5)
        total = sum((r.rate_percent for r in plan.rank_rates), Decimal("0"))
        self.assertEqual(total, Decimal("10.00"))


class TestNegativeRateRejected(unittest.TestCase):
    """Invalid rate input — negative rate_percent must fail validation."""

    def test_negative_rate_raises_validation_error(self):
        draft = RankRewardPoolPlanVersion(
            reward_plan_version_id="TEST_NEGATIVE",
            is_published=False,
            rank_rates=[
                RankRewardRate(Rank.BRONZE, Decimal("-1.00")),
                RankRewardRate(Rank.SILVER, Decimal("2.00")),
            ],
        )
        with self.assertRaises(RankRewardPoolValidationError):
            validate_and_publish_reward_pool(draft)


class TestDuplicateRankRejected(unittest.TestCase):
    """Duplicate-rank behavior — taken as a reconstruction assumption
    (see rank_reward_pool.py module docstring)."""

    def test_duplicate_rank_entry_raises_validation_error(self):
        draft = RankRewardPoolPlanVersion(
            reward_plan_version_id="TEST_DUPLICATE",
            is_published=False,
            rank_rates=[
                RankRewardRate(Rank.BRONZE, Decimal("2.00")),
                RankRewardRate(Rank.BRONZE, Decimal("3.00")),
            ],
        )
        with self.assertRaises(RankRewardPoolValidationError):
            validate_and_publish_reward_pool(draft)


class TestPartialTierCoverageAllowed(unittest.TestCase):
    """Missing-rank behavior — this reconstruction does NOT require all 5
    canonical tiers to be present (no evidence supports that requirement),
    so a partial-coverage draft must publish successfully."""

    def test_plan_with_only_two_ranks_still_publishes(self):
        draft = RankRewardPoolPlanVersion(
            reward_plan_version_id="TEST_PARTIAL",
            is_published=False,
            rank_rates=[
                RankRewardRate(Rank.BRONZE, Decimal("2.00")),
                RankRewardRate(Rank.DIAMOND, Decimal("2.00")),
            ],
        )
        published = validate_and_publish_reward_pool(draft)
        self.assertTrue(published.is_published)


class TestNoTotalPercentageEnforced(unittest.TestCase):
    """This module does not enforce any specific total (e.g. 10.00%) —
    that check, where proven, lives one layer up in
    commercial_allocation.py. A total that is NOT 10% must still publish
    successfully at this layer."""

    def test_plan_totalling_far_from_ten_percent_still_publishes_here(self):
        draft = RankRewardPoolPlanVersion(
            reward_plan_version_id="TEST_OFF_TOTAL",
            is_published=False,
            rank_rates=[RankRewardRate(Rank.BRONZE, Decimal("99.00"))],
        )
        published = validate_and_publish_reward_pool(draft)
        self.assertTrue(published.is_published)
        self.assertEqual(published.rank_rates[0].rate_percent, Decimal("99.00"))


class TestNonPublishedDraftBecomesPublished(unittest.TestCase):
    """A valid, not-yet-published draft (is_published=False) is what
    validate_and_publish_reward_pool expects as input, and returns a
    published (is_published=True) copy without mutating the input."""

    def test_draft_is_published_after_validation(self):
        draft = RankRewardPoolPlanVersion(
            reward_plan_version_id="TEST_DRAFT",
            is_published=False,
            rank_rates=[RankRewardRate(Rank.GOLD, Decimal("2.00"))],
        )
        self.assertFalse(draft.is_published)
        published = validate_and_publish_reward_pool(draft)
        self.assertTrue(published.is_published)
        # Original draft object must remain unmutated (frozen dataclass + replace()).
        self.assertFalse(draft.is_published)


if __name__ == "__main__":
    unittest.main()

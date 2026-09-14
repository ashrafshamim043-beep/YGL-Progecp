"""
Builds the locked, approved RankRewardPoolPlanVersion.
Locked business decision: 10% total = 5 ranks x 2.00% each.
"""
from decimal import Decimal
from .models import Rank
from .rank_reward_pool import (
 RankRewardPoolPlanVersion,
 RankRewardRate,
 validate_and_publish_reward_pool,
)
def build_approved_rank_reward_plan_v1() -> RankRewardPoolPlanVersion:
 draft = RankRewardPoolPlanVersion(
 reward_plan_version_id="RANK_REWARD_PLAN_V1_2026",
 is_published=False,
 rank_rates=[
 RankRewardRate(Rank.BRONZE, Decimal("2.00")),
 RankRewardRate(Rank.SILVER, Decimal("2.00")),
 RankRewardRate(Rank.GOLD, Decimal("2.00")),
 RankRewardRate(Rank.PLATINUM, Decimal("2.00")),
 RankRewardRate(Rank.DIAMOND, Decimal("2.00")),
 ],
 )
 return validate_and_publish_reward_pool(draft)
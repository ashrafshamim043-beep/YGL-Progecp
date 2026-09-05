"""
Builds the PlanVersion object for the approved, locked Phase 7 business
decisions. This is the single source of truth wiring business-approved
numbers into the engine — nothing here should be hand-edited by
engineering without a corresponding new business decision.

Matching Bonus allocation (3.50%) is reserved as budget but NOT
implemented anywhere in calculators.py. See that module's module-level
docstring for why.
"""
from decimal import Decimal

from .models import (
    UnilevelGenerationRate,
    RankRate,
    RankQualificationThreshold,
    YearEndRankShare,
    Rank,
)
from .plan_version import PlanVersion, validate_and_publish


def build_approved_plan_v1() -> PlanVersion:
    draft = PlanVersion(
        plan_version_id="PLAN_V1_2026",
        is_published=False,

        personal_sales_rate=Decimal("7.00"),
        direct_referral_rate=Decimal("7.00"),

        unilevel_allocation=Decimal("8.75"),
        unilevel_generation_rates=[
            UnilevelGenerationRate(1, Decimal("2.00")),
            UnilevelGenerationRate(2, Decimal("1.50")),
            UnilevelGenerationRate(3, Decimal("1.25")),
            UnilevelGenerationRate(4, Decimal("1.00")),
            UnilevelGenerationRate(5, Decimal("0.75")),
            UnilevelGenerationRate(6, Decimal("0.60")),
            UnilevelGenerationRate(7, Decimal("0.50")),
            UnilevelGenerationRate(8, Decimal("0.40")),
            UnilevelGenerationRate(9, Decimal("0.40")),
            UnilevelGenerationRate(10, Decimal("0.35")),
        ],

        rank_bonus_allocation=Decimal("5.25"),
        rank_bonus_rates=[
            RankRate(Rank.BRONZE, Decimal("0.50")),
            RankRate(Rank.SILVER, Decimal("0.75")),
            RankRate(Rank.GOLD, Decimal("1.00")),
            RankRate(Rank.PLATINUM, Decimal("1.25")),
            RankRate(Rank.DIAMOND, Decimal("1.75")),
        ],

        team_bonus_allocation=Decimal("3.50"),
        team_bonus_rates=[
            RankRate(Rank.BRONZE, Decimal("0.25")),
            RankRate(Rank.SILVER, Decimal("0.50")),
            RankRate(Rank.GOLD, Decimal("0.75")),
            RankRate(Rank.PLATINUM, Decimal("0.85")),
            RankRate(Rank.DIAMOND, Decimal("1.15")),
        ],

        # Reserved, NOT calculated. See calculators.py.
        matching_bonus_allocation=Decimal("3.50"),

        rank_thresholds=[
            RankQualificationThreshold(Rank.BRONZE, Decimal("5000"), Decimal("25000"), 2),
            RankQualificationThreshold(Rank.SILVER, Decimal("10000"), Decimal("75000"), 3),
            RankQualificationThreshold(Rank.GOLD, Decimal("20000"), Decimal("200000"), 5),
            RankQualificationThreshold(Rank.PLATINUM, Decimal("30000"), Decimal("500000"), 7),
            RankQualificationThreshold(Rank.DIAMOND, Decimal("50000"), Decimal("1000000"), 10),
        ],

        year_end_member_share_of_fund=Decimal("50.00"),
        year_end_company_share_of_fund=Decimal("50.00"),
        year_end_rank_shares=[
            YearEndRankShare(Rank.BRONZE, Decimal("10")),
            YearEndRankShare(Rank.SILVER, Decimal("15")),
            YearEndRankShare(Rank.GOLD, Decimal("20")),
            YearEndRankShare(Rank.PLATINUM, Decimal("25")),
            YearEndRankShare(Rank.DIAMOND, Decimal("30")),
        ],
    )
    return validate_and_publish(draft)
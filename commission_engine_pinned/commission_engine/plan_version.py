"""
Compensation Plan Version.

A plan version is immutable once published. All rate tables live here as
structured data — never hardcoded inside calculators — so a future plan
change becomes a NEW version, not an edit to history.

Hard rule (locked business decision): the sum of every commission type's
maximum possible payout must never exceed 35.00% of qualifying sale value.
This is enforced here, at publish time, not left to be discovered at
runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List

from .models import (
    UnilevelGenerationRate,
    RankRate,
    RankQualificationThreshold,
    YearEndRankShare,
    Rank,
)

MAX_POOL_CEILING = Decimal("35.00")


class PlanVersionValidationError(Exception):
    """Raised when a plan version fails publish-time validation."""


@dataclass(frozen=True)
class PlanVersion:
    plan_version_id: str
    is_published: bool

    personal_sales_rate: Decimal
    direct_referral_rate: Decimal

    unilevel_allocation: Decimal
    unilevel_generation_rates: List[UnilevelGenerationRate]

    rank_bonus_allocation: Decimal
    rank_bonus_rates: List[RankRate]

    team_bonus_allocation: Decimal
    team_bonus_rates: List[RankRate]

    # Matching Bonus: allocation is reserved but NOT calculated anywhere in
    # this engine. See calculators.calculate_matching_bonus_STUB for why.
    matching_bonus_allocation: Decimal

    rank_thresholds: List[RankQualificationThreshold]
    year_end_member_share_of_fund: Decimal  # locked: 50.00
    year_end_company_share_of_fund: Decimal  # locked: 50.00
    year_end_rank_shares: List[YearEndRankShare]  # 10/15/20/25/30

    def rank_threshold(self, rank: Rank) -> RankQualificationThreshold:
        for t in self.rank_thresholds:
            if t.rank == rank:
                return t
        raise KeyError(f"No threshold configured for rank {rank}")

    def unilevel_rate_for_generation(self, generation: int) -> Decimal:
        for g in self.unilevel_generation_rates:
            if g.generation == generation:
                return g.rate_percent
        return Decimal("0")

    def rank_bonus_rate_for(self, rank: Rank) -> Decimal:
        for r in self.rank_bonus_rates:
            if r.rank == rank:
                return r.rate_percent
        return Decimal("0")

    def team_bonus_rate_for(self, rank: Rank) -> Decimal:
        for r in self.team_bonus_rates:
            if r.rank == rank:
                return r.rate_percent
        return Decimal("0")

    def year_end_share_for(self, rank: Rank) -> Decimal:
        for r in self.year_end_rank_shares:
            if r.rank == rank:
                return r.share_percent_of_member_pool
        return Decimal("0")


def validate_and_publish(plan: PlanVersion) -> PlanVersion:
    """
    Hard publish-time validation. Raises PlanVersionValidationError if the
    plan could ever exceed the approved 35% Member Commission Pool ceiling,
    or if any sub-allocation's own rate table doesn't internally add up.

    This must be called before a plan version is allowed to go live —
    never bypassed by an admin panel or config change.
    """
    errors: List[str] = []

    # 1. Unilevel generation rates must not exceed the declared allocation.
    unilevel_sum = sum((g.rate_percent for g in plan.unilevel_generation_rates), Decimal("0"))
    if unilevel_sum > plan.unilevel_allocation:
        errors.append(
            f"Unilevel generation rates sum to {unilevel_sum}% which exceeds "
            f"the declared Unilevel allocation of {plan.unilevel_allocation}%."
        )
    if len(plan.unilevel_generation_rates) > 10:
        errors.append("Unilevel generation rates define more than the approved max depth of 10.")

    # 2. Rank Bonus: since Rank Bonus is a single-payee mechanism, the
    #    binding constraint is that the maximum single rate (Diamond) must
    #    not exceed the declared allocation.
    max_rank_rate = max((r.rate_percent for r in plan.rank_bonus_rates), default=Decimal("0"))
    if max_rank_rate > plan.rank_bonus_allocation:
        errors.append(
            f"Highest Rank Bonus rate ({max_rank_rate}%) exceeds the declared "
            f"Rank Bonus allocation of {plan.rank_bonus_allocation}%."
        )

    # 3. Team Bonus: same single-payee logic.
    max_team_rate = max((r.rate_percent for r in plan.team_bonus_rates), default=Decimal("0"))
    if max_team_rate > plan.team_bonus_allocation:
        errors.append(
            f"Highest Team Bonus rate ({max_team_rate}%) exceeds the declared "
            f"Team Bonus allocation of {plan.team_bonus_allocation}%."
        )

    # 4. Year-End rank shares must sum to exactly 100% of the member pool.
    year_end_sum = sum((r.share_percent_of_member_pool for r in plan.year_end_rank_shares), Decimal("0"))
    if year_end_sum != Decimal("100"):
        errors.append(f"Year-End rank shares sum to {year_end_sum}%, must equal exactly 100%.")

    # 5. Year-End fund split must sum to exactly 100%.
    if plan.year_end_member_share_of_fund + plan.year_end_company_share_of_fund != Decimal("100"):
        errors.append("Year-End fund member/company split does not sum to 100%.")

    # 6. THE GLOBAL CEILING CHECK — the one that must never be bypassed.
    #    Matching Bonus allocation IS included here (it is reserved budget
    #    even though it is not currently calculated), so a future plan
    #    author cannot accidentally publish a plan that would breach 35%
    #    once Matching Bonus logic is eventually implemented.
    max_possible_total = (
        plan.personal_sales_rate
        + plan.direct_referral_rate
        + plan.unilevel_allocation
        + plan.rank_bonus_allocation
        + plan.team_bonus_allocation
        + plan.matching_bonus_allocation
    )
    if max_possible_total > MAX_POOL_CEILING:
        errors.append(
            f"Total maximum possible payout ({max_possible_total}%) exceeds the "
            f"approved Member Commission Pool ceiling of {MAX_POOL_CEILING}%."
        )

    if errors:
        raise PlanVersionValidationError(
            f"Plan version '{plan.plan_version_id}' failed publish validation:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )

    # Return a published copy (frozen dataclasses -> construct a new one).
    from dataclasses import replace
    return replace(plan, is_published=True)
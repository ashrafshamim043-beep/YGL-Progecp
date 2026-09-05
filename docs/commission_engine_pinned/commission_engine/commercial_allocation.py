"""
Commercial Allocation Model.

A NEW, SEPARATE layer sitting above Phase 7's Commission Engine. This module
does not modify, import-and-alter, or duplicate any Phase 7 rate — it only
REFERENCES an existing, already-published Phase 7 PlanVersion by object,
reading its own already-validated total (which Phase 7's own
plan_version.py guarantees is <= 35.00%, and which the locked business
decision requires to be exactly 35.00% for full commercial-allocation
validation to succeed).

Locked business structure (from approved_commercial_allocation.py):
    Product Cost ................ 40.00%
    Marketing .................... 4.50%
    Courier/Logistics ............ 3.50%
    Payment/System/Other ......... 2.00%
    Company Profit ............... 5.00%
    Phase 7 Compensation ........ 35.00% (referenced, not duplicated)
    Rank Reward Pool ............ 10.00% (referenced, not duplicated)
    ------------------------------------
    TOTAL ....................... 100.00%
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import List

from .plan_version import PlanVersion
from .rank_reward_pool import RankRewardPoolPlanVersion

COMMERCIAL_TOTAL_TARGET = Decimal("100.00")


class CommercialAllocationValidationError(Exception):
    """Raised when a CommercialAllocationPlanVersion fails publish-time validation."""


@dataclass(frozen=True)
class CommercialAllocationPlanVersion:
    commercial_plan_version_id: str
    is_published: bool

    product_cost_rate: Decimal
    marketing_rate: Decimal
    courier_logistics_rate: Decimal
    payment_system_other_rate: Decimal
    company_profit_rate: Decimal

    # References, not duplicated numbers. The actual rates live in Phase 7
    # / rank_reward_pool.py's own locked, versioned objects.
    phase7_plan_version: PlanVersion
    rank_reward_pool_plan_version: RankRewardPoolPlanVersion

    def phase7_compensation_total(self) -> Decimal:
        """Reads Phase 7's total directly from the referenced PlanVersion object.
        Never hardcoded here — always derived, so a future Phase 7 plan
        version is automatically picked up correctly."""
        p = self.phase7_plan_version
        return (
            p.personal_sales_rate
            + p.direct_referral_rate
            + p.unilevel_allocation
            + p.rank_bonus_allocation
            + p.team_bonus_allocation
            + p.matching_bonus_allocation
        )

    def rank_reward_pool_total(self) -> Decimal:
        return sum(
            (r.rate_percent for r in self.rank_reward_pool_plan_version.rank_rates),
            Decimal("0"),
        )

    def grand_total(self) -> Decimal:
        return (
            self.product_cost_rate
            + self.marketing_rate
            + self.courier_logistics_rate
            + self.payment_system_other_rate
            + self.company_profit_rate
            + self.phase7_compensation_total()
            + self.rank_reward_pool_total()
        )


def validate_and_publish_commercial_allocation(
    plan: CommercialAllocationPlanVersion,
) -> CommercialAllocationPlanVersion:
    """
    Hard publish-time validation:
      1. Total of all seven components must equal EXACTLY 100.00%.
      2. The referenced Phase 7 PlanVersion must itself already be published
         (Phase 7's own validator already enforces its internal <=35% rule —
         we do not re-validate Phase 7 here, only require it be published).
      3. The referenced RankRewardPoolPlanVersion must already be published.

    This function NEVER modifies Phase 7's PlanVersion or its validator.
    """
    errors: List[str] = []

    if not plan.phase7_plan_version.is_published:
        errors.append(
            "Referenced Phase 7 PlanVersion is not published. "
            "Commercial Allocation cannot reference a draft compensation plan."
        )
    if not plan.rank_reward_pool_plan_version.is_published:
        errors.append(
            "Referenced RankRewardPoolPlanVersion is not published."
        )

    total = plan.grand_total()
    if total != COMMERCIAL_TOTAL_TARGET:
        errors.append(
            f"Commercial Allocation total is {total}%, must equal exactly "
            f"{COMMERCIAL_TOTAL_TARGET}%. (Product Cost {plan.product_cost_rate}% + "
            f"Marketing {plan.marketing_rate}% + Courier/Logistics {plan.courier_logistics_rate}% + "
            f"Payment/System {plan.payment_system_other_rate}% + Company Profit {plan.company_profit_rate}% + "
            f"Phase7 Compensation {plan.phase7_compensation_total()}% + "
            f"Rank Reward Pool {plan.rank_reward_pool_total()}%)"
        )

    if errors:
        raise CommercialAllocationValidationError(
            f"CommercialAllocationPlanVersion '{plan.commercial_plan_version_id}' "
            f"failed publish validation:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    return replace(plan, is_published=True)

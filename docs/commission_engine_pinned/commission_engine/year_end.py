"""
Year-End Undistributed Commission Fund Distribution.

Locked rules implemented here:
  - 50% of the fiscal year's accumulated undistributed fund -> Company Account
  - 50% -> Member pool, split by rank tier per the approved percentages
    (Bronze 10% / Silver 15% / Gold 20% / Platinum 25% / Diamond 30%)
  - Within a tier: EQUAL split among all members holding that as their
    highest qualified rank at year-end
  - Empty tier (no qualifying members): that tier's share -> Company
    Account. Never redistributed to other tiers, never carried forward.
  - A member counts only toward their single highest qualified rank
    (anti-double-counting).

Rounding note: equal-split division can leave a small residual (e.g.
৳100 / 3 members). That residual is swept to the Company Account with an
explicit "rounding_remainder" reason, rather than inventing an unapproved
rule about which member gets the extra paisa. This is a technical
rounding-handling choice, not a new business rule, and should be
confirmed/overridden if a different treatment is preferred.

This runs as a separate, annual scheduled batch — never inline with
per-order commission calculation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List

from .models import Rank, RANK_ORDER
from .plan_version import PlanVersion
from .undistributed import UndistributedFundTracker
from .ledger import ImmutableLedger

COMPANY_ACCOUNT_ID = "COMPANY"


@dataclass
class YearEndMemberRankSnapshot:
    """A member's highest qualified rank at year-end close, and nothing
    else — this is a distinct, separate evaluation from monthly rank
    qualification, scoped only to feed this batch process."""
    member_id: str
    highest_qualified_rank: Rank


@dataclass
class YearEndDistributionSummary:
    fiscal_year: int
    total_fund: Decimal
    company_share: Decimal
    member_share_pool: Decimal
    per_rank_tier_amount: Dict[Rank, Decimal] = field(default_factory=dict)
    per_rank_recipient_count: Dict[Rank, int] = field(default_factory=dict)
    empty_tiers: List[Rank] = field(default_factory=list)
    rounding_swept_to_company: Decimal = Decimal("0")


def run_year_end_distribution(
    fiscal_year: int,
    plan: PlanVersion,
    undistributed_tracker: UndistributedFundTracker,
    year_end_rank_snapshots: List[YearEndMemberRankSnapshot],
    ledger: ImmutableLedger,
) -> YearEndDistributionSummary:
    records = undistributed_tracker.for_fiscal_year(fiscal_year)
    total_fund = sum((r.amount for r in records), Decimal("0"))

    company_share = (total_fund * plan.year_end_company_share_of_fund / Decimal("100")).quantize(Decimal("0.01"))
    member_share_pool = total_fund - company_share  # avoids a rounding gap vs. total_fund

    # Group eligible members by their highest qualified rank (Bronze..Diamond only;
    # Rank.NONE members are not eligible for any tier).
    members_by_rank: Dict[Rank, List[str]] = {r: [] for r in RANK_ORDER if r != Rank.NONE}
    for snap in year_end_rank_snapshots:
        if snap.highest_qualified_rank != Rank.NONE:
            members_by_rank[snap.highest_qualified_rank].append(snap.member_id)

    summary = YearEndDistributionSummary(
        fiscal_year=fiscal_year,
        total_fund=total_fund,
        company_share=company_share,
        member_share_pool=member_share_pool,
    )

    running_company_addition = Decimal("0")

    for rank in [Rank.BRONZE, Rank.SILVER, Rank.GOLD, Rank.PLATINUM, Rank.DIAMOND]:
        tier_share_pct = plan.year_end_share_for(rank)
        tier_amount = (member_share_pool * tier_share_pct / Decimal("100")).quantize(Decimal("0.01"))
        summary.per_rank_tier_amount[rank] = tier_amount

        recipients = members_by_rank[rank]
        summary.per_rank_recipient_count[rank] = len(recipients)

        if not recipients:
            # Empty-tier rule: entire tier amount -> Company Account.
            summary.empty_tiers.append(rank)
            running_company_addition += tier_amount
            ledger.append_entry(
                member_id=COMPANY_ACCOUNT_ID,
                amount=tier_amount,
                reference_type="YEAR_END_DISTRIBUTION",
                reference_id=f"FY{fiscal_year}",
                reason=f"empty_tier:{rank.value}",
                fiscal_year=fiscal_year,
                plan_version_id=plan.plan_version_id,
            )
            continue

        # Equal split within tier.
        per_member_raw = tier_amount / Decimal(len(recipients))
        per_member = per_member_raw.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        distributed_total = per_member * len(recipients)
        remainder = tier_amount - distributed_total

        for member_id in recipients:
            ledger.append_entry(
                member_id=member_id,
                amount=per_member,
                reference_type="YEAR_END_DISTRIBUTION",
                reference_id=f"FY{fiscal_year}",
                reason=f"year_end_rank_distribution:{rank.value}",
                fiscal_year=fiscal_year,
                plan_version_id=plan.plan_version_id,
            )

        if remainder > 0:
            summary.rounding_swept_to_company += remainder
            running_company_addition += remainder
            ledger.append_entry(
                member_id=COMPANY_ACCOUNT_ID,
                amount=remainder,
                reference_type="YEAR_END_DISTRIBUTION",
                reference_id=f"FY{fiscal_year}",
                reason=f"rounding_remainder:{rank.value}",
                fiscal_year=fiscal_year,
                plan_version_id=plan.plan_version_id,
            )

    # Base 50% company transfer (plus any empty-tier / rounding additions
    # already recorded above as their own distinct, auditable entries).
    ledger.append_entry(
        member_id=COMPANY_ACCOUNT_ID,
        amount=company_share,
        reference_type="YEAR_END_DISTRIBUTION",
        reference_id=f"FY{fiscal_year}",
        reason="company_retained_share_50pct",
        fiscal_year=fiscal_year,
        plan_version_id=plan.plan_version_id,
    )

    return summary
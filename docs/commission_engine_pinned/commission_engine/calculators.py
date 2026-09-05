"""
Commission calculators — one function per locked commission type.

===========================================================================
MATCHING BONUS — NOT IMPLEMENTED (INTENTIONAL STUB)
===========================================================================
The Matching Bonus (3.50% allocation) has NO approved rate structure or
payee scope as of this build. Per explicit instruction, no business logic
is assumed or invented for it here.

Per approved Business Decision 1 (see calculate_matching_bonus_STUB()'s
own docstring), the reserved 3.50% is no longer silently dropped:

calculate_matching_bonus_STUB() below does the following and NOTHING else:
  - creates zero CommissionLineItems (no payee/qualification logic exists)
  - creates exactly ONE UndistributedAmount per order, for the full
    reserved 3.50%, reason="matching_bonus_not_yet_implemented" — so it
    flows into the Undistributed Commission Fund / Year-End Distribution
    instead of vanishing with no audit trail
  - returns a MatchingBonusResult flagging itself as NOT_IMPLEMENTED

This means the engine's current maximum achievable PAID payout is still
31.50% (35.00% - 3.50% Matching), not the full 35.00%, until Matching
Bonus rules are approved and this stub is replaced with real logic. The
remaining 3.50% is not paid, but is now accounted for as undistributed.
===========================================================================

UNILEVEL "QUALIFIED" DEFINITION — ASSUMPTION FLAGGED FOR CONFIRMATION
===========================================================================
The locked rules state an unqualified/non-existent generation does not
get redistributed, but never define what "qualified" means for a plain
Unilevel generation (as opposed to Rank/Team Bonus, which have explicit
AND-condition criteria). This implementation uses the minimal, most
defensible interpretation: a generation is "qualified" if that upline
member's account is ACTIVE (not suspended/terminated) — the same
account-good-standing rule already used for order eligibility (Phase 6).
No rank-based or volume-based gate is applied to Unilevel payees. If this
is not the intended interpretation, this must be corrected by an explicit
business decision before production use.
===========================================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Tuple

from .models import (
    Order,
    UplineSnapshot,
    CommissionLineItem,
    CommissionType,
    UndistributedAmount,
    Rank,
    AccountStatus,
    RankQualificationSnapshot,
)
from .plan_version import PlanVersion
from .rank import highest_qualified_rank, is_team_bonus_qualified, RankSnapshotProvider


def _pct_of(sale_value: Decimal, rate_percent: Decimal) -> Decimal:
    return (sale_value * rate_percent / Decimal("100")).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# 1. Personal Sales — unconditional, paid to the credited member
# ---------------------------------------------------------------------------

def calculate_personal_sales(order: Order, plan: PlanVersion) -> CommissionLineItem:
    amount = _pct_of(order.sale_value, plan.personal_sales_rate)
    return CommissionLineItem(
        order_id=order.order_id,
        plan_version_id=plan.plan_version_id,
        commission_type=CommissionType.PERSONAL_SALES,
        payee_member_id=order.credited_member_id,
        amount=amount,
    )


# ---------------------------------------------------------------------------
# 2. Direct Referral — unconditional, paid to the nearest (generation 1) sponsor
# ---------------------------------------------------------------------------

def calculate_direct_referral(
    order: Order, upline: UplineSnapshot, plan: PlanVersion, fiscal_year: int
) -> Tuple[List[CommissionLineItem], List[UndistributedAmount]]:
    entry = upline.entry_at(1)
    if entry is None:
        undistributed = UndistributedAmount(
            order_id=order.order_id,
            plan_version_id=plan.plan_version_id,
            commission_type=CommissionType.DIRECT_REFERRAL,
            amount=_pct_of(order.sale_value, plan.direct_referral_rate),
            reason="no_direct_sponsor_exists",
            fiscal_year=fiscal_year,
        )
        return [], [undistributed]

    amount = _pct_of(order.sale_value, plan.direct_referral_rate)
    line_item = CommissionLineItem(
        order_id=order.order_id,
        plan_version_id=plan.plan_version_id,
        commission_type=CommissionType.DIRECT_REFERRAL,
        payee_member_id=entry.member.member_id,
        amount=amount,
        generation=1,
    )
    return [line_item], []


# ---------------------------------------------------------------------------
# 3. Unilevel — fixed rate per generation, 10-generation max, no redistribution
# ---------------------------------------------------------------------------

def calculate_unilevel(
    order: Order, upline: UplineSnapshot, plan: PlanVersion, fiscal_year: int
) -> Tuple[List[CommissionLineItem], List[UndistributedAmount]]:
    line_items: List[CommissionLineItem] = []
    undistributed: List[UndistributedAmount] = []

    for generation in range(1, 11):
        rate = plan.unilevel_rate_for_generation(generation)
        amount = _pct_of(order.sale_value, rate)
        entry = upline.entry_at(generation)

        if entry is None:
            undistributed.append(UndistributedAmount(
                order_id=order.order_id,
                plan_version_id=plan.plan_version_id,
                commission_type=CommissionType.UNILEVEL,
                amount=amount,
                reason="generation_does_not_exist",
                fiscal_year=fiscal_year,
                generation=generation,
            ))
            continue

        if entry.member.account_status != AccountStatus.ACTIVE:
            undistributed.append(UndistributedAmount(
                order_id=order.order_id,
                plan_version_id=plan.plan_version_id,
                commission_type=CommissionType.UNILEVEL,
                amount=amount,
                reason="generation_member_not_active",
                fiscal_year=fiscal_year,
                generation=generation,
            ))
            continue

        line_items.append(CommissionLineItem(
            order_id=order.order_id,
            plan_version_id=plan.plan_version_id,
            commission_type=CommissionType.UNILEVEL,
            payee_member_id=entry.member.member_id,
            amount=amount,
            generation=generation,
        ))

    return line_items, undistributed


# ---------------------------------------------------------------------------
# 4 & 5. Rank Bonus / Team Bonus — nearest qualifying upline, single payee
# ---------------------------------------------------------------------------

def _nearest_qualifying_payee(
    upline: UplineSnapshot,
    rank_provider: RankSnapshotProvider,
    period: str,
    plan: PlanVersion,
    require_team_bonus_eligibility: bool,
):
    """
    Walks the upline chain from generation 1 upward. Returns the first
    (nearest) member who qualifies, along with their highest qualified
    rank, or (None, Rank.NONE) if nobody in the entire chain qualifies.
    """
    for entry in sorted(upline.chain, key=lambda e: e.generation):
        snapshot = rank_provider.get_snapshot(entry.member.member_id, period)
        if snapshot is None:
            continue
        rank = highest_qualified_rank(snapshot, plan)
        if rank == Rank.NONE:
            continue
        if require_team_bonus_eligibility and not is_team_bonus_qualified(snapshot, plan):
            continue
        return entry.member.member_id, rank
    return None, Rank.NONE


def calculate_rank_bonus(
    order: Order,
    upline: UplineSnapshot,
    rank_provider: RankSnapshotProvider,
    period: str,
    plan: PlanVersion,
    fiscal_year: int,
) -> Tuple[List[CommissionLineItem], List[UndistributedAmount]]:
    payee_id, rank = _nearest_qualifying_payee(
        upline, rank_provider, period, plan, require_team_bonus_eligibility=False
    )
    max_possible = _pct_of(order.sale_value, plan.rank_bonus_allocation)

    if payee_id is None:
        return [], [UndistributedAmount(
            order_id=order.order_id,
            plan_version_id=plan.plan_version_id,
            commission_type=CommissionType.RANK_BONUS,
            amount=max_possible,
            reason="no_qualifying_rank_payee_in_upline",
            fiscal_year=fiscal_year,
        )]

    rate = plan.rank_bonus_rate_for(rank)
    amount = _pct_of(order.sale_value, rate)
    line_item = CommissionLineItem(
        order_id=order.order_id,
        plan_version_id=plan.plan_version_id,
        commission_type=CommissionType.RANK_BONUS,
        payee_member_id=payee_id,
        amount=amount,
        rank=rank,
    )

    # NOTE: per the exact locked wording of the Undistributed Fund decision,
    # the only trigger for a Rank/Team Bonus undistributed record is
    # "no qualifying payee found" — not "a payee was found but their fixed
    # rank rate is below the 5.25%/3.50% allocation ceiling". The ceiling
    # is a maximum cap on this single-payee mechanism, not a pool that must
    # be fully exhausted every order (unlike Unilevel, where each of the 10
    # generations is independently trackable). No undistributed record is
    # created here when a payee is successfully found.
    return [line_item], []


def calculate_team_bonus(
    order: Order,
    upline: UplineSnapshot,
    rank_provider: RankSnapshotProvider,
    period: str,
    plan: PlanVersion,
    fiscal_year: int,
) -> Tuple[List[CommissionLineItem], List[UndistributedAmount]]:
    payee_id, rank = _nearest_qualifying_payee(
        upline, rank_provider, period, plan, require_team_bonus_eligibility=True
    )
    max_possible = _pct_of(order.sale_value, plan.team_bonus_allocation)

    if payee_id is None:
        return [], [UndistributedAmount(
            order_id=order.order_id,
            plan_version_id=plan.plan_version_id,
            commission_type=CommissionType.TEAM_BONUS,
            amount=max_possible,
            reason="no_qualifying_team_bonus_payee_in_upline",
            fiscal_year=fiscal_year,
        )]

    rate = plan.team_bonus_rate_for(rank)
    amount = _pct_of(order.sale_value, rate)
    line_item = CommissionLineItem(
        order_id=order.order_id,
        plan_version_id=plan.plan_version_id,
        commission_type=CommissionType.TEAM_BONUS,
        payee_member_id=payee_id,
        amount=amount,
        rank=rank,
    )

    # Same note as Rank Bonus above: no undistributed record for the gap
    # between the paid rate and the allocation ceiling — only "no payee
    # found" is a locked undistributed trigger for this bonus type.
    return [line_item], []


# ---------------------------------------------------------------------------
# 6. Matching Bonus — payee/qualification logic still NOT implemented, but
#    (per approved Business Decision 1) the reserved 3.50% is no longer
#    silently dropped: it is tracked as an UndistributedAmount so it flows
#    into the Undistributed Commission Fund / Year-End Distribution, exactly
#    like any other unpaid portion of the pool. Do NOT implement actual
#    matching business logic (rate structure / payee scope) here — that is
#    still an open business decision.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MatchingBonusResult:
    status: str = "NOT_IMPLEMENTED"
    allocation_reserved_percent: Decimal = Decimal("3.50")
    line_items: List[CommissionLineItem] = field(default_factory=list)
    undistributed: List[UndistributedAmount] = field(default_factory=list)


def calculate_matching_bonus_STUB(order: Order, plan: PlanVersion, fiscal_year: int) -> MatchingBonusResult:
    """
    Matching Bonus qualification/payee logic is still NOT implemented —
    creates no CommissionLineItem, and status stays NOT_IMPLEMENTED.

    Per approved Business Decision 1 (Matching Bonus must never silently
    disappear from accounting): the full reserved allocation
    (plan.matching_bonus_allocation, 3.50%) for this order is recorded as
    a single UndistributedAmount, reason="matching_bonus_not_yet_implemented",
    so it accumulates into the Undistributed Commission Fund and is
    resolved at Year-End Distribution like any other unpaid portion of the
    pool — instead of vanishing from the system with no audit trail.
    """
    reserved_amount = _pct_of(order.sale_value, plan.matching_bonus_allocation)
    undistributed_amount = UndistributedAmount(
        order_id=order.order_id,
        plan_version_id=plan.plan_version_id,
        commission_type=CommissionType.MATCHING_BONUS,
        amount=reserved_amount,
        reason="matching_bonus_not_yet_implemented",
        fiscal_year=fiscal_year,
    )
    return MatchingBonusResult(undistributed=[undistributed_amount])
"""
Rank Reward Pool.

Companion module to plan_version.py / commercial_allocation.py. Referenced
by commercial_allocation.py as the Rank Reward Pool component of the
locked Commercial Allocation structure (10.00% of the 100% total, per the
approved rates defined in approved_rank_reward_plan.py).

============================================================================
RECONSTRUCTION NOTICE
============================================================================
This file was NOT present in any available source upload (Phase5 PDF or
COMPLETE_SYSTEM__1_.md). It has been reconstructed under explicit
authorization from a reverse-interface analysis of its only two callers
(approved_rank_reward_plan.py and commercial_allocation.py). Every
non-trivial decision below is tagged inline as one of:

  [PROVEN]     — required by an actual caller's usage; not a guess.
  [ASSUMPTION] — not provable from any caller's code; chosen as the most
                 conservative option that adds no unproven business rule,
                 following this codebase's established
                 validate_and_publish() pattern (see plan_version.py and
                 commercial_allocation.py's own
                 validate_and_publish_commercial_allocation()).

No specific percentage target (e.g. "must total exactly 10.00%") is
enforced inside this module. The only proven total-percentage check in
this codebase's chain lives in commercial_allocation.py's own 100.00%
grand-total validation, which already reads rank_reward_pool_total() as
one of its seven components. Duplicating or inventing a different target
here would assert an unproven business rule.
============================================================================
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import List

from .models import Rank  # [PROVEN] RankRewardRate's first ctor arg is a Rank (approved_rank_reward_plan.py)


class RankRewardPoolValidationError(Exception):
    """Raised when a RankRewardPoolPlanVersion fails publish-time validation.

    [ASSUMPTION] Exact class name is not provable (never imported or caught
    anywhere in available code). Named by direct analogy with this
    codebase's established convention: PlanVersionValidationError
    (plan_version.py), CommercialAllocationValidationError
    (commercial_allocation.py)."""


@dataclass(frozen=True)
class RankRewardRate:
    # [PROVEN] Two positional fields, order (Rank, Decimal) — from
    # approved_rank_reward_plan.py's calls, e.g.
    # RankRewardRate(Rank.BRONZE, Decimal("2.00")).
    #
    # [ASSUMPTION] Field name "rank" — never accessed by attribute name
    # anywhere in available code (only used positionally). Chosen to match
    # the proven field name of the sibling class models.RankRate, which
    # plays the identical role (a Rank paired with a Decimal rate).
    rank: Rank
    # [PROVEN] Field name "rate_percent" — read directly as `r.rate_percent`
    # in commercial_allocation.py's rank_reward_pool_total():
    #   sum((r.rate_percent for r in ...rank_rates), Decimal("0"))
    rate_percent: Decimal


@dataclass(frozen=True)
class RankRewardPoolPlanVersion:
    # [PROVEN] All three fields are required keyword arguments in
    # approved_rank_reward_plan.py's construction call, and
    # `.is_published` / `.rank_rates` are both read directly by
    # commercial_allocation.py.
    reward_plan_version_id: str
    is_published: bool
    rank_rates: List[RankRewardRate]


def validate_and_publish_reward_pool(plan: RankRewardPoolPlanVersion) -> RankRewardPoolPlanVersion:
    """
    Publish-time validation.

    [PROVEN] Function signature (one draft in, a published copy out) —
    required by approved_rank_reward_plan.py's usage and the return-type
    hint of build_approved_rank_reward_plan_v1() -> RankRewardPoolPlanVersion.

    [PROVEN] Overall control-flow pattern (accumulate an errors list, raise
    one ValidationError listing all of them if any exist, otherwise return
    `dataclasses.replace(plan, is_published=True)`) — this is not proven
    for THIS specific file, but is the established, consistent pattern of
    every other validate_and_publish*() function in this codebase
    (plan_version.py, commercial_allocation.py). Followed here for
    consistency, not asserted as independently proven for this module.

    [ASSUMPTION] The two checks below are the minimum defensive
    data-integrity checks that do not assert any unproven percentage,
    threshold, or cap business rule:

      1. Every rate_percent must be >= 0. A negative reward rate is not a
         coherent business value under any plausible interpretation.
      2. No duplicate Rank entries in rank_rates. A duplicate would cause
         silent double-counting in commercial_allocation.py's
         rank_reward_pool_total() sum — this is a data-integrity
         safeguard, not a new percentage/threshold rule.

    Explicitly NOT enforced here (each would be inventing an unproven rule):
      - No specific total percentage target (e.g. "must equal exactly
        10.00%"). The only proven total-percentage check in this
        codebase's chain lives one layer up, in commercial_allocation.py's
        own 100.00% grand-total validation.
      - No requirement that all 5 canonical Rank tiers be present. No
        caller proves or requires full-tier coverage.
      - No per-rank rate ceiling/cap. No evidence of one exists anywhere
        in the available source.
    """
    errors: List[str] = []

    for r in plan.rank_rates:
        if r.rate_percent < Decimal("0"):
            errors.append(
                f"Rank Reward rate for {r.rank} is negative ({r.rate_percent}%); "
                f"rates must be >= 0."
            )

    seen_ranks = set()
    for r in plan.rank_rates:
        if r.rank in seen_ranks:
            errors.append(
                f"Duplicate Rank Reward rate entry for {r.rank} -- each rank "
                f"may appear at most once."
            )
        seen_ranks.add(r.rank)

    if errors:
        raise RankRewardPoolValidationError(
            f"RankRewardPoolPlanVersion '{plan.reward_plan_version_id}' "
            f"failed publish validation:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    return replace(plan, is_published=True)

"""
Rank qualification.

Locked business rule: a member qualifies for a rank only if ALL THREE
conditions are met simultaneously (AND, not OR):
  - Personal Monthly Qualifying Sales >= threshold
  - Team Monthly Sales Volume >= threshold
  - Active Direct Members >= threshold

Rank is evaluated per period (monthly for ongoing qualification, or a
year-end snapshot for the Year-End Distribution) — never against a "live"
current value computed on the fly during commission calculation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional

from .models import Rank, RANK_ORDER, RankQualificationSnapshot
from .plan_version import PlanVersion


class RankSnapshotProvider(ABC):
    @abstractmethod
    def get_snapshot(self, member_id: str, period: str) -> Optional[RankQualificationSnapshot]:
        raise NotImplementedError


class InMemoryRankSnapshotProvider(RankSnapshotProvider):
    def __init__(self, snapshots: Dict[str, RankQualificationSnapshot]):
        # keyed by f"{member_id}:{period}"
        self.snapshots = snapshots

    def get_snapshot(self, member_id: str, period: str) -> Optional[RankQualificationSnapshot]:
        return self.snapshots.get(f"{member_id}:{period}")


def highest_qualified_rank(snapshot: RankQualificationSnapshot, plan: PlanVersion) -> Rank:
    """
    Returns the highest rank for which ALL THREE conditions are met.
    Returns Rank.NONE if the member doesn't meet even Bronze.
    """
    qualified = Rank.NONE
    for rank in RANK_ORDER[1:]:  # skip NONE
        threshold = plan.rank_threshold(rank)
        meets_personal = snapshot.personal_sales >= threshold.personal_monthly_sales
        meets_team = snapshot.team_volume >= threshold.team_monthly_volume
        meets_directs = snapshot.active_directs >= threshold.min_active_directs
        if meets_personal and meets_team and meets_directs:
            qualified = rank  # keep climbing; ranks are checked low-to-high
        else:
            # Ranks are structured so thresholds increase monotonically;
            # once a tier fails, higher tiers (with even higher thresholds)
            # cannot pass either. Safe to stop early.
            break
    return qualified


MIN_ACTIVE_DIRECTS_FOR_TEAM_BONUS = 2


def is_team_bonus_qualified(snapshot: RankQualificationSnapshot, plan: PlanVersion) -> bool:
    """
    Baseline Team Bonus eligibility (locked rule): minimum 2 Active Direct
    Members, and team_volume must already reflect genuine qualifying sales
    only (enforced upstream at the data-source level, per Phase 5's
    commissionable-product flagging — not re-derived here).

    Enrollment/signup alone (active_directs > 0 but no sales activity)
    must never create eligibility on its own — this is why team_volume
    is checked, not just active_directs.
    """
    return (
        snapshot.active_directs >= MIN_ACTIVE_DIRECTS_FOR_TEAM_BONUS
        and snapshot.team_volume > 0
    )
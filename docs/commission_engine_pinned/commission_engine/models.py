"""
Core data models for the MLM Commission Engine.

These are plain, framework-agnostic Python dataclasses so this module can
later be wired to any persistence layer (SQL, NoSQL, etc.) without changing
business logic. No SQL/schema decisions are made here.

Money precision: all monetary/percentage math uses Decimal, never float,
to avoid rounding-error drift in financial calculations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from datetime import datetime, timezone
from typing import List, Optional, Dict


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CommissionType(str, Enum):
    PERSONAL_SALES = "PERSONAL_SALES"
    DIRECT_REFERRAL = "DIRECT_REFERRAL"
    UNILEVEL = "UNILEVEL"
    RANK_BONUS = "RANK_BONUS"
    TEAM_BONUS = "TEAM_BONUS"
    MATCHING_BONUS = "MATCHING_BONUS"  # STUB ONLY — not implemented
    YEAR_END_RANK_DISTRIBUTION = "YEAR_END_RANK_DISTRIBUTION"


class Rank(str, Enum):
    NONE = "NONE"
    BRONZE = "BRONZE"
    SILVER = "SILVER"
    GOLD = "GOLD"
    PLATINUM = "PLATINUM"
    DIAMOND = "DIAMOND"


# Ordered lowest -> highest. Used to determine "highest qualified rank".
RANK_ORDER: List[Rank] = [
    Rank.NONE, Rank.BRONZE, Rank.SILVER, Rank.GOLD, Rank.PLATINUM, Rank.DIAMOND
]


class AccountStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"


class CommissionLineItemStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CREDITED = "CREDITED"
    VOID = "VOID"
    REVERSED = "REVERSED"


# ---------------------------------------------------------------------------
# Plan version building blocks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnilevelGenerationRate:
    generation: int  # 1..10
    rate_percent: Decimal


@dataclass(frozen=True)
class RankRate:
    rank: Rank
    rate_percent: Decimal


@dataclass(frozen=True)
class RankQualificationThreshold:
    rank: Rank
    personal_monthly_sales: Decimal
    team_monthly_volume: Decimal
    min_active_directs: int


@dataclass(frozen=True)
class YearEndRankShare:
    rank: Rank
    share_percent_of_member_pool: Decimal  # e.g. Bronze=10, Diamond=30 (sums to 100)


# ---------------------------------------------------------------------------
# Members / Tree / Rank snapshots
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Member:
    member_id: str
    account_status: AccountStatus = AccountStatus.ACTIVE


@dataclass(frozen=True)
class UplineEntry:
    """One position in a frozen, point-in-time upline chain."""
    generation: int  # 1 = nearest/direct sponsor
    member: Member


@dataclass(frozen=True)
class UplineSnapshot:
    """Immutable snapshot of a member's upline chain as of a given time."""
    member_id: str
    as_of: datetime
    chain: List[UplineEntry]  # ordered generation 1..N, nearest first

    def entry_at(self, generation: int) -> Optional[UplineEntry]:
        for e in self.chain:
            if e.generation == generation:
                return e
        return None


@dataclass(frozen=True)
class RankQualificationSnapshot:
    """A member's rank-relevant metrics for a specific qualifying period."""
    member_id: str
    period: str  # e.g. "2026-08" for monthly, or "2026" for year-end
    personal_sales: Decimal
    team_volume: Decimal  # must already reflect "genuine qualifying sales only"
    active_directs: int


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Order:
    order_id: str
    credited_member_id: str  # whose personal sales this counts toward
    sale_value: Decimal
    is_commissionable: bool
    is_paid: bool
    order_timestamp: datetime


# ---------------------------------------------------------------------------
# Commission output records
# ---------------------------------------------------------------------------

@dataclass
class CommissionLineItem:
    order_id: str
    plan_version_id: str
    commission_type: CommissionType
    payee_member_id: str
    amount: Decimal
    generation: Optional[int] = None
    rank: Optional[Rank] = None
    status: CommissionLineItemStatus = CommissionLineItemStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class UndistributedAmount:
    order_id: str
    plan_version_id: str
    commission_type: CommissionType
    amount: Decimal
    reason: str
    fiscal_year: int
    generation: Optional[int] = None
    rank: Optional[Rank] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class LedgerEntry:
    entry_id: str
    member_id: str  # "COMPANY" for the company account
    amount: Decimal  # positive = credit, negative = debit
    reference_type: str  # e.g. "COMMISSION", "YEAR_END_DISTRIBUTION", "REVERSAL"
    reference_id: str
    reason: str
    fiscal_year: Optional[int]
    plan_version_id: Optional[str]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
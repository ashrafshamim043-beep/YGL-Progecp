"""
Undistributed Commission Fund tracking.

Locked principle: any portion of the 35% pool that goes unpaid on a given
order (unqualified Unilevel generation, no qualifying Rank/Team payee,
etc.) is never silently dropped. It is recorded with its order, commission
type, reason, fiscal year, and plan version, and accumulates toward the
annual Year-End Distribution.

Per approved Business Decision 1: Matching Bonus's reserved 3.50%
allocation IS now recorded here too (see calculators.py,
calculate_matching_bonus_STUB) — its qualification/payee logic remains
unimplemented, so nothing is ever paid out for it, but the reserved
amount is tracked as undistributed (reason="matching_bonus_not_yet_implemented")
on every order rather than being dropped from accounting entirely.
"""
from __future__ import annotations

from typing import List

from .models import UndistributedAmount


class UndistributedFundTracker:
    def __init__(self):
        self._records: List[UndistributedAmount] = []

    def record(self, amount: UndistributedAmount) -> None:
        self._records.append(amount)

    def for_fiscal_year(self, fiscal_year: int) -> List[UndistributedAmount]:
        return [r for r in self._records if r.fiscal_year == fiscal_year]

    def all_records(self) -> List[UndistributedAmount]:
        return list(self._records)
"""
Immutable Ledger.

Locked principle: financial records are NEVER updated or deleted. Every
correction is a new, offsetting entry. This module enforces that at the
API level — there is deliberately no update_entry() or delete_entry()
method anywhere in this class. The only mutation operation is append.

Balances are always computed by summing entries, never stored/cached in a
way that could drift from the ledger itself.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import List, Optional

from .models import LedgerEntry


class ImmutableLedger:
    def __init__(self):
        self._entries: List[LedgerEntry] = []

    def append_entry(
        self,
        member_id: str,
        amount: Decimal,
        reference_type: str,
        reference_id: str,
        reason: str,
        fiscal_year: Optional[int] = None,
        plan_version_id: Optional[str] = None,
    ) -> LedgerEntry:
        entry = LedgerEntry(
            entry_id=str(uuid.uuid4()),
            member_id=member_id,
            amount=amount,
            reference_type=reference_type,
            reference_id=reference_id,
            reason=reason,
            fiscal_year=fiscal_year,
            plan_version_id=plan_version_id,
        )
        self._entries.append(entry)
        return entry

    def entries_for(self, member_id: str) -> List[LedgerEntry]:
        return [e for e in self._entries if e.member_id == member_id]

    def balance_of(self, member_id: str) -> Decimal:
        return sum((e.amount for e in self.entries_for(member_id)), Decimal("0"))

    def all_entries(self) -> List[LedgerEntry]:
        # Returns a copy so callers cannot mutate the internal list directly.
        return list(self._entries)

    # NOTE: intentionally no update_entry() / delete_entry() methods exist.
    # A reversal is performed by calling append_entry() again with a
    # negative amount and reference_type="REVERSAL", referencing the
    # original entry's reference_id in `reason`.
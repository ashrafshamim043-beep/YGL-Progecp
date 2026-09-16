"""
DB-backed adapters implementing the EXACT public method signatures of
commission_engine's IdempotencyStore, ImmutableLedger, and
UndistributedFundTracker (duck-typed substitution -- verified safe in
Specification Refinement v2 Section 1: engine.py never does isinstance()
checks on these dependencies).

This file is the ONLY correct home for these adapters, because it is the
only module permitted to import commission_engine (enforced by CI).
"""
import json
import uuid
from dataclasses import asdict
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from commission_engine.models import (
    CommissionLineItem, UndistributedAmount, LedgerEntry, CommissionType, Rank, CommissionLineItemStatus,
)
from commission_engine.idempotency import IdempotencyRecord, PlanVersionLockedError
from commission_engine.engine import OrderCommissionResult

from app.services.commission_service.db_models import (
    CommissionProcessingRecordRow, PlanVersionLockRow,
    CommissionLineItemRow, LedgerEntryRow, UndistributedAmountRow,
)


def _decimal_to_str(d: Decimal) -> str:
    return str(d)


class DBIdempotencyStore:
    """Implements IdempotencyStore's public interface: get(), reserve(),
    has_processed(), check_plan_version_lock(), lock_plan_version()."""

    def __init__(self, db: Session):
        self.db = db

    def _split_key(self, key: str):
        return key.split("::")

    def has_processed(self, key: str) -> bool:
        order_id, plan_version_id, event_type = self._split_key(key)
        stmt = select(CommissionProcessingRecordRow.id).where(
            CommissionProcessingRecordRow.order_id == order_id,
            CommissionProcessingRecordRow.plan_version_id == plan_version_id,
            CommissionProcessingRecordRow.event_type == event_type,
        )
        return self.db.execute(stmt).first() is not None

    def get(self, key: str) -> Optional[IdempotencyRecord]:
        order_id, plan_version_id, event_type = self._split_key(key)
        stmt = select(CommissionProcessingRecordRow).where(
            CommissionProcessingRecordRow.order_id == order_id,
            CommissionProcessingRecordRow.plan_version_id == plan_version_id,
            CommissionProcessingRecordRow.event_type == event_type,
        )
        row = self.db.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        result = self._deserialize_result(row.result_reference)
        return IdempotencyRecord(key=key, result=result)

    def reserve(self, key: str, result: OrderCommissionResult) -> None:
        if self.has_processed(key):
            raise ValueError(f"Idempotency key already reserved: {key}")
        order_id, plan_version_id, event_type = self._split_key(key)
        row = CommissionProcessingRecordRow(
            id=uuid.uuid4(), order_id=order_id, plan_version_id=plan_version_id, event_type=event_type,
            result_reference=self._serialize_result(result),
        )
        self.db.add(row)
        self.db.flush()

    def check_plan_version_lock(self, order_id: str, event_type: str, plan_version_id: str) -> None:
        stmt = select(PlanVersionLockRow).where(
            PlanVersionLockRow.order_id == order_id, PlanVersionLockRow.event_type == event_type,
        )
        row = self.db.execute(stmt).scalar_one_or_none()
        if row is not None and row.locked_plan_version_id != plan_version_id:
            raise PlanVersionLockedError(
                f"Order '{order_id}' was already processed under PlanVersion "
                f"'{row.locked_plan_version_id}'. It cannot be re-processed under a "
                f"different PlanVersion '{plan_version_id}'."
            )

    def lock_plan_version(self, order_id: str, event_type: str, plan_version_id: str) -> None:
        stmt = select(PlanVersionLockRow).where(
            PlanVersionLockRow.order_id == order_id, PlanVersionLockRow.event_type == event_type,
        )
        existing = self.db.execute(stmt).scalar_one_or_none()
        if existing is not None:
            return  # already locked (idempotent no-op, matches frozen setdefault() behavior)
        self.db.add(PlanVersionLockRow(
            id=uuid.uuid4(), order_id=order_id, event_type=event_type, locked_plan_version_id=plan_version_id,
        ))
        self.db.flush()

    # -- Result (de)serialization -------------------------------------------

    @staticmethod
    def _serialize_result(result: OrderCommissionResult) -> str:
        return json.dumps({
            "order_id": result.order_id, "plan_version_id": result.plan_version_id,
            "matching_bonus_status": result.matching_bonus_status,
            "line_items": [
                {**asdict(li), "amount": str(li.amount), "commission_type": li.commission_type.value,
                 "rank": li.rank.value if li.rank else None, "status": li.status.value,
                 "created_at": li.created_at.isoformat()}
                for li in result.line_items
            ],
            "undistributed": [
                {**asdict(u), "amount": str(u.amount), "commission_type": u.commission_type.value,
                 "rank": u.rank.value if u.rank else None, "created_at": u.created_at.isoformat()}
                for u in result.undistributed
            ],
        })

    @staticmethod
    def _deserialize_result(raw: str) -> OrderCommissionResult:
        from datetime import datetime
        data = json.loads(raw)
        line_items = [
            CommissionLineItem(
                order_id=li["order_id"], plan_version_id=li["plan_version_id"],
                commission_type=CommissionType(li["commission_type"]), payee_member_id=li["payee_member_id"],
                amount=Decimal(li["amount"]), generation=li.get("generation"),
                rank=Rank(li["rank"]) if li.get("rank") else None,
                status=CommissionLineItemStatus(li["status"]),
                created_at=datetime.fromisoformat(li["created_at"]),
            ) for li in data["line_items"]
        ]
        undistributed = [
            UndistributedAmount(
                order_id=u["order_id"], plan_version_id=u["plan_version_id"],
                commission_type=CommissionType(u["commission_type"]), amount=Decimal(u["amount"]),
                reason=u["reason"], fiscal_year=u["fiscal_year"], generation=u.get("generation"),
                rank=Rank(u["rank"]) if u.get("rank") else None,
                created_at=datetime.fromisoformat(u["created_at"]),
            ) for u in data["undistributed"]
        ]
        return OrderCommissionResult(
            order_id=data["order_id"], plan_version_id=data["plan_version_id"],
            line_items=line_items, undistributed=undistributed,
            matching_bonus_status=data["matching_bonus_status"],
        )


class DBImmutableLedger:
    """
    Implements ImmutableLedger's public interface: append_entry(),
    entries_for(), balance_of(), all_entries().

    Per Specification Refinement v2 Section 2.2: `commission_line_item_id`
    linking is NOT a parameter the frozen engine passes to append_entry()
    (it only knows order_id/reference_id) -- so this adapter exposes a
    call-scoped `current_commission_line_item_id` attribute that
    CommissionService (the orchestrator, not the frozen Engine) sets
    immediately before calling engine.approve_and_credit() for a specific
    line item, and clears immediately after. This is the documented
    orchestration-context mechanism, not a change to the Engine's interface.
    """

    def __init__(self, db: Session):
        self.db = db
        self.current_commission_line_item_id: Optional[str] = None

    def append_entry(
        self, member_id: str, amount: Decimal, reference_type: str, reference_id: str, reason: str,
        fiscal_year: Optional[int] = None, plan_version_id: Optional[str] = None,
    ) -> LedgerEntry:
        row = LedgerEntryRow(
            id=uuid.uuid4(), member_id=member_id, amount=_decimal_to_str(amount),
            reference_type=reference_type, reference_id=reference_id,
            commission_line_item_id=(
                uuid.UUID(self.current_commission_line_item_id)
                if reference_type == "COMMISSION" and self.current_commission_line_item_id else None
            ),
            reason=reason, fiscal_year=fiscal_year, plan_version_id=plan_version_id,
        )
        self.db.add(row)
        self.db.flush()
        return LedgerEntry(
            entry_id=str(row.id), member_id=member_id, amount=amount, reference_type=reference_type,
            reference_id=reference_id, reason=reason, fiscal_year=fiscal_year, plan_version_id=plan_version_id,
            created_at=row.created_at,
        )

    def entries_for(self, member_id: str) -> List[LedgerEntry]:
        stmt = select(LedgerEntryRow).where(LedgerEntryRow.member_id == member_id)
        rows = self.db.execute(stmt).scalars().all()
        return [self._row_to_domain(r) for r in rows]

    def balance_of(self, member_id: str) -> Decimal:
        stmt = select(func.coalesce(func.sum(LedgerEntryRow.amount), 0)).where(LedgerEntryRow.member_id == member_id)
        total = self.db.execute(stmt).scalar_one()
        return Decimal(str(total))

    def all_entries(self) -> List[LedgerEntry]:
        rows = self.db.execute(select(LedgerEntryRow)).scalars().all()
        return [self._row_to_domain(r) for r in rows]

    @staticmethod
    def _row_to_domain(row: LedgerEntryRow) -> LedgerEntry:
        return LedgerEntry(
            entry_id=str(row.id), member_id=row.member_id, amount=Decimal(str(row.amount)),
            reference_type=row.reference_type, reference_id=row.reference_id, reason=row.reason,
            fiscal_year=row.fiscal_year, plan_version_id=row.plan_version_id, created_at=row.created_at,
        )


class DBUndistributedFundTracker:
    """Implements UndistributedFundTracker's public interface: record(),
    for_fiscal_year(), all_records()."""

    def __init__(self, db: Session):
        self.db = db

    def record(self, amount: UndistributedAmount) -> None:
        row = UndistributedAmountRow(
            id=uuid.uuid4(), order_id=amount.order_id, plan_version_id=amount.plan_version_id,
            commission_type=amount.commission_type.value, amount=_decimal_to_str(amount.amount),
            reason=amount.reason, fiscal_year=amount.fiscal_year, generation=amount.generation,
            rank=amount.rank.value if amount.rank else None,
        )
        self.db.add(row)
        self.db.flush()

    def for_fiscal_year(self, fiscal_year: int) -> List[UndistributedAmount]:
        stmt = select(UndistributedAmountRow).where(UndistributedAmountRow.fiscal_year == fiscal_year)
        rows = self.db.execute(stmt).scalars().all()
        return [self._row_to_domain(r) for r in rows]

    def all_records(self) -> List[UndistributedAmount]:
        rows = self.db.execute(select(UndistributedAmountRow)).scalars().all()
        return [self._row_to_domain(r) for r in rows]

    @staticmethod
    def _row_to_domain(row: UndistributedAmountRow) -> UndistributedAmount:
        return UndistributedAmount(
            order_id=row.order_id, plan_version_id=row.plan_version_id,
            commission_type=CommissionType(row.commission_type), amount=Decimal(str(row.amount)),
            reason=row.reason, fiscal_year=row.fiscal_year, generation=row.generation,
            rank=Rank(row.rank) if row.rank else None, created_at=row.created_at,
        )

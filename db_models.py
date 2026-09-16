"""
Commission persistence models -- SQLAlchemy tables mirroring
commission_engine's own dataclasses (CommissionLineItem, LedgerEntry,
UndistributedAmount) plus the idempotency/plan-lock tables designed in
Specification Refinement v2/v3 (commission_processing_records,
plan_version_locks).

This file lives inside commission_service/ specifically because it is the
ONLY module permitted to import commission_engine (enforced by
scripts/verify_import_boundary.py) -- these tables exist purely to
persist what the frozen Engine computes, never to duplicate or
reinterpret its business logic.

Matching Bonus note: no separate "matching bonus" table exists, by
design. Per the approved Business Decision 1, the reserved 3.50% is
tracked as an ordinary UndistributedAmount (commission_type=MATCHING_BONUS,
reason="matching_bonus_not_yet_implemented") -- undistributed_amounts
below IS the "reserved structure" for it. No new business rule/table is
invented here.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Integer, Numeric, ForeignKey, UniqueConstraint, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PlanVersionRow(Base):
    """
    Metadata-only pointer table -- does NOT store rate values (those
    always come from commission_engine.approved_plan.build_approved_plan_v1(),
    the frozen, authoritative source). Storing rates here in a form that
    could drift from the Engine would itself be a business-logic
    duplication risk; this table only tracks WHICH plan_version_id is
    currently published, per Specification v3 Fix A5.

    The partial unique index enforcing "at most one published row" is
    declared below, AFTER this class (see uq_plan_versions_single_published)
    -- SQLAlchemy's declarative style requires referencing the bound
    column attribute (PlanVersionRow.is_published), which only exists once
    the class body has finished executing.
    """
    __tablename__ = "plan_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_version_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_published: Mapped[bool] = mapped_column(nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Partial unique index -- at most one row may have is_published=true at any
# time. This mirrors the migration's actual DDL
# ("CREATE UNIQUE INDEX ... ON plan_versions ((true)) WHERE is_published = true")
# exactly, using postgresql_where so an ORM-driven introspection (or a
# future `alembic revision --autogenerate`) sees the same constraint the
# migration already created, instead of a misleading full-table one.
Index(
    "uq_plan_versions_single_published", PlanVersionRow.is_published,
    unique=True, postgresql_where=(PlanVersionRow.is_published.is_(True)),
)


class CommissionProcessingRecordRow(Base):
    """Backs commission_engine.idempotency.IdempotencyStore._records --
    exact-key replay cache."""
    __tablename__ = "commission_processing_records"
    __table_args__ = (
        UniqueConstraint("order_id", "plan_version_id", "event_type",
                          name="uq_commission_processing_records_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    result_reference: Mapped[str] = mapped_column(String, nullable=False)  # JSON-serialized OrderCommissionResult reference
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanVersionLockRow(Base):
    """Backs commission_engine.idempotency.IdempotencyStore._locked_plan_version
    -- Plan Version Lock enforcement (Business Decision 2)."""
    __tablename__ = "plan_version_locks"
    __table_args__ = (
        UniqueConstraint("order_id", "event_type", name="uq_plan_version_locks_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    locked_plan_version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CommissionLineItemRow(Base):
    __tablename__ = "commission_line_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    commission_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payee_member_id: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[str] = mapped_column(Numeric(18, 2), nullable=False)
    generation: Mapped[int | None] = mapped_column(Integer)
    rank: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    source_event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="PAYMENT_CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("admins.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LedgerEntryRow(Base):
    """
    Append-only. No updated_at column, no application-level UPDATE/DELETE
    grant (same policy as every other financial/audit table in this
    system). Corrections are new REVERSAL/ADJUSTMENT rows, never edits.
    """
    __tablename__ = "ledger_entries"
    __table_args__ = (
        # Partial unique index (WHERE reference_type='COMMISSION') applied
        # in the migration -- Specification v3 Fix A2, duplicate-credit
        # protection at the DB level.
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[str] = mapped_column(String(64), nullable=False)  # "COMPANY" or a member UUID string
    amount: Mapped[str] = mapped_column(Numeric(18, 2), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(64), nullable=False)
    commission_line_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commission_line_items.id")
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    plan_version_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UndistributedAmountRow(Base):
    __tablename__ = "undistributed_amounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    commission_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[str] = mapped_column(Numeric(18, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int | None] = mapped_column(Integer)
    rank: Mapped[str | None] = mapped_column(String(32))
    source_event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="PAYMENT_CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

"""
Commission Engine orchestrator.

Ties together: idempotency check -> tree/rank snapshot loading ->
per-type calculation -> runtime ceiling validation -> pending line item
+ undistributed-amount output.

Deliberately does NOT credit the ledger directly. Per the locked workflow
(pending -> review/approval -> credited), crediting happens through a
separate approve_and_credit() call, so the review/approval step is a real
gate, not bypassed by the calculation step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .models import (
    Order,
    CommissionLineItem,
    UndistributedAmount,
    CommissionLineItemStatus,
)
from .plan_version import PlanVersion
from .tree import UplineProvider
from .rank import RankSnapshotProvider
from .idempotency import IdempotencyStore, make_idempotency_key
from .ceiling_validator import validate_order_ceiling
from .undistributed import UndistributedFundTracker
from .ledger import ImmutableLedger
from . import calculators


@dataclass
class OrderCommissionResult:
    order_id: str
    plan_version_id: str
    line_items: List[CommissionLineItem]
    undistributed: List[UndistributedAmount]
    matching_bonus_status: str
    was_duplicate: bool = False


class CommissionEngine:
    def __init__(
        self,
        plan: PlanVersion,
        upline_provider: UplineProvider,
        rank_provider: RankSnapshotProvider,
        idempotency_store: IdempotencyStore,
        undistributed_tracker: UndistributedFundTracker,
        ledger: ImmutableLedger,
    ):
        if not plan.is_published:
            raise ValueError("CommissionEngine requires a published PlanVersion.")
        self.plan = plan
        self.upline_provider = upline_provider
        self.rank_provider = rank_provider
        self.idempotency_store = idempotency_store
        self.undistributed_tracker = undistributed_tracker
        self.ledger = ledger

    def process_order_payment_confirmed(
        self, order: Order, rank_period: str, fiscal_year: int
    ) -> OrderCommissionResult:
        event_type = "PAYMENT_CONFIRMED"

        # Per approved Business Decision 2 (Plan Version Lock): reject
        # immediately, before any calculation, if this order was already
        # processed under a DIFFERENT PlanVersion. Same-PlanVersion calls
        # (including replays) pass through unaffected.
        self.idempotency_store.check_plan_version_lock(
            order.order_id, event_type, self.plan.plan_version_id
        )

        key = make_idempotency_key(order.order_id, self.plan.plan_version_id, event_type)

        if self.idempotency_store.has_processed(key):
            cached: OrderCommissionResult = self.idempotency_store.get(key).result
            return OrderCommissionResult(
                order_id=cached.order_id,
                plan_version_id=cached.plan_version_id,
                line_items=cached.line_items,
                undistributed=cached.undistributed,
                matching_bonus_status=cached.matching_bonus_status,
                was_duplicate=True,
            )

        if not order.is_paid or not order.is_commissionable:
            result = OrderCommissionResult(
                order_id=order.order_id,
                plan_version_id=self.plan.plan_version_id,
                line_items=[],
                undistributed=[],
                matching_bonus_status="NOT_IMPLEMENTED",
            )
            self.idempotency_store.reserve(key, result)
            self.idempotency_store.lock_plan_version(order.order_id, event_type, self.plan.plan_version_id)
            return result

        upline = self.upline_provider.get_upline_snapshot(
            order.credited_member_id, order.order_timestamp, max_depth=10
        )

        all_line_items: List[CommissionLineItem] = []
        all_undistributed: List[UndistributedAmount] = []

        # 1. Personal Sales
        all_line_items.append(calculators.calculate_personal_sales(order, self.plan))

        # 2. Direct Referral
        li, ud = calculators.calculate_direct_referral(order, upline, self.plan, fiscal_year)
        all_line_items += li
        all_undistributed += ud

        # 3. Unilevel
        li, ud = calculators.calculate_unilevel(order, upline, self.plan, fiscal_year)
        all_line_items += li
        all_undistributed += ud

        # 4. Rank Bonus
        li, ud = calculators.calculate_rank_bonus(
            order, upline, self.rank_provider, rank_period, self.plan, fiscal_year
        )
        all_line_items += li
        all_undistributed += ud

        # 5. Team Bonus
        li, ud = calculators.calculate_team_bonus(
            order, upline, self.rank_provider, rank_period, self.plan, fiscal_year
        )
        all_line_items += li
        all_undistributed += ud

        # 6. Matching Bonus — payee logic still STUB (contributes no paid
        # line items), but per approved Business Decision 1 its reserved
        # allocation is now tracked as undistributed rather than dropped.
        matching_result = calculators.calculate_matching_bonus_STUB(order, self.plan, fiscal_year)
        all_undistributed += matching_result.undistributed

        # Runtime ceiling assertion — defense in depth.
        validate_order_ceiling(order, all_line_items)

        # Persist undistributed amounts to the tracker (feeds Year-End Fund).
        for u in all_undistributed:
            self.undistributed_tracker.record(u)

        result = OrderCommissionResult(
            order_id=order.order_id,
            plan_version_id=self.plan.plan_version_id,
            line_items=all_line_items,
            undistributed=all_undistributed,
            matching_bonus_status=matching_result.status,
        )
        self.idempotency_store.reserve(key, result)
        self.idempotency_store.lock_plan_version(order.order_id, event_type, self.plan.plan_version_id)
        return result

    def approve_and_credit(self, line_item: CommissionLineItem, reason: str = "commission_approved") -> None:
        """
        Separate, explicit step: moves a PENDING commission line item to
        APPROVED then CREDITED, writing the corresponding ledger entry.
        This is where a human/admin review gate (Phase 7's "pending ->
        review/approval" workflow) would plug in before this is called.
        """
        if line_item.status != CommissionLineItemStatus.PENDING:
            raise ValueError(
                f"Cannot approve/credit a line item with status {line_item.status}; "
                f"expected PENDING."
            )
        line_item.status = CommissionLineItemStatus.APPROVED
        self.ledger.append_entry(
            member_id=line_item.payee_member_id,
            amount=line_item.amount,
            reference_type="COMMISSION",
            reference_id=line_item.order_id,
            reason=f"{line_item.commission_type.value}:{reason}",
            plan_version_id=line_item.plan_version_id,
        )
        line_item.status = CommissionLineItemStatus.CREDITED
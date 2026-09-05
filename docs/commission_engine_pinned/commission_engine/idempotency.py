"""
Idempotency protection.

Locked principle: the same (order_id, plan_version_id, event_type) must
never generate commission line items more than once, even under retries,
duplicate webhook deliveries, or crashed/restarted batch jobs.

Per approved Business Decision 2 (Plan Version Lock): an order's
applicable PlanVersion is permanently fixed at its first
payment-confirmed processing. IdempotencyStore additionally tracks this
lock (order_id + event_type -> the plan_version_id first used for it),
separately from the existing exact-key idempotency records above, because
the existing key already embeds plan_version_id and so cannot by itself
detect a *different* plan_version being used for an order it has already
seen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Any


def make_idempotency_key(order_id: str, plan_version_id: str, event_type: str) -> str:
    return f"{order_id}::{plan_version_id}::{event_type}"


def _plan_lock_key(order_id: str, event_type: str) -> str:
    return f"{order_id}::{event_type}"


@dataclass
class IdempotencyRecord:
    key: str
    result: Any  # the previously computed result, so retries can return it unchanged


class PlanVersionLockedError(Exception):
    """Raised when an order that was already processed under one
    PlanVersion is processed again under a DIFFERENT PlanVersion. Per
    approved Business Decision 2, an order's applicable PlanVersion locks
    permanently at its first payment-confirmed processing and can never
    change afterward -- this must be an explicit rejection, never a
    silent duplicate and never a silent recalculation."""


class IdempotencyStore:
    """In-memory implementation. A production version would back this with
    a database unique constraint so the check-and-reserve is atomic even
    across concurrent workers."""

    def __init__(self):
        self._records: Dict[str, IdempotencyRecord] = {}
        self._locked_plan_version: Dict[str, str] = {}

    def get(self, key: str) -> Optional[IdempotencyRecord]:
        return self._records.get(key)

    def reserve(self, key: str, result: Any) -> None:
        if key in self._records:
            raise ValueError(f"Idempotency key already reserved: {key}")
        self._records[key] = IdempotencyRecord(key=key, result=result)

    def has_processed(self, key: str) -> bool:
        return key in self._records

    def check_plan_version_lock(self, order_id: str, event_type: str, plan_version_id: str) -> None:
        """Raises PlanVersionLockedError if this order_id+event_type was
        already locked to a DIFFERENT plan_version_id. Does nothing (and
        does not itself lock anything) on the first-ever call for a given
        order_id+event_type, or on a call matching the already-locked
        plan_version_id -- this keeps existing same-version idempotency
        behavior completely unaffected."""
        locked = self._locked_plan_version.get(_plan_lock_key(order_id, event_type))
        if locked is not None and locked != plan_version_id:
            raise PlanVersionLockedError(
                f"Order '{order_id}' was already processed under PlanVersion "
                f"'{locked}'. It cannot be re-processed under a different "
                f"PlanVersion '{plan_version_id}'."
            )

    def lock_plan_version(self, order_id: str, event_type: str, plan_version_id: str) -> None:
        """Permanently records the plan_version_id an order+event was
        first successfully processed under. Idempotent: a repeat call
        with the SAME plan_version_id is a harmless no-op."""
        self._locked_plan_version.setdefault(_plan_lock_key(order_id, event_type), plan_version_id)
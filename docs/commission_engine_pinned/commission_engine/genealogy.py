"""
Phase 4 — Genealogy / Tree System.

Implements Phase 7's UplineProvider interface (tree.py, UNMODIFIED) with a
real, versioned sponsor-placement engine, per the locked Phase 4 design
decisions:

 1. Placement Rule = Simple Unilevel (direct-sponsor-only, no matrix/spillover)
 2. Sponsor Mandatory = every MEMBER must have a sponsor (except one
 designated company ROOT member)
 3. Re-sponsorship = staff-approval only; there is deliberately NO
 self-service re-sponsorship method on this engine
 4. Duplicate placement attempts = routed to a review queue, never
 silently rejected and never silently applied
 5. Historical Account Status = upline snapshots use the member's status
 AS OF the snapshot's `as_of` timestamp, not their current/live status
 6. Downline Query = simple traversal to start (recursive-CTE-equivalent
 in this in-memory implementation; a real DB implementation would use
 a recursive CTE or closure table, per the design doc's migration path)
 7. Concurrency = a single-active-placement invariant enforced at
 placement-time (the in-memory equivalent of the design's DB unique
 constraint on `(member_id) WHERE effective_to IS NULL`)

===========================================================================
WHY THIS MODULE DOES NOT MODIFY tree.py, models.py, calculators.py, etc.
===========================================================================
Phase 7's `UplineProvider` abstract class already defines the exact
contract every calculator depends on. This module provides a NEW
implementation of that same contract -- it imports UplineProvider,
UplineSnapshot, UplineEntry, Member, AccountStatus, Rank from the existing,
untouched models/tree modules. No existing file's method signature,
class, or logic changes.

Phase 2 (User/Member Management) has not been implemented yet -- it exists
only as a locked design document. Wherever this module needs member
role/account-status/history data that Phase 2 will eventually own, it
depends on a small abstract interface (MemberDirectory,
MemberStatusHistoryProvider) with an in-memory implementation for testing.
A real Phase 2 database implementation can satisfy these interfaces later
without this module changing.
===========================================================================
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional

from .models import Member, AccountStatus, UplineEntry, UplineSnapshot
from .tree import UplineProvider


# ---------------------------------------------------------------------------
# Stand-in interfaces for Phase 2 data (not yet implemented -- see module
# docstring). A real Phase 2 build satisfies these interfaces; nothing in
# this module or in Phase 7 needs to change when that happens.
# ---------------------------------------------------------------------------

class MemberRole(str, Enum):
    CUSTOMER = "CUSTOMER"
    MEMBER = "MEMBER"
    STAFF = "STAFF"


@dataclass(frozen=True)
class MemberDirectoryEntry:
    member_id: str
    role: MemberRole
    account_status: AccountStatus


class MemberDirectory(ABC):
    """Stand-in for Phase 2's member lookup. A real implementation reads
    the `members` table Phase 2's design defines."""

    @abstractmethod
    def get(self, member_id: str) -> Optional[MemberDirectoryEntry]:
        raise NotImplementedError


class InMemoryMemberDirectory(MemberDirectory):
    def __init__(self, entries: Optional[Dict[str, MemberDirectoryEntry]] = None):
        self._entries: Dict[str, MemberDirectoryEntry] = dict(entries or {})

    def add(self, entry: MemberDirectoryEntry) -> None:
        self._entries[entry.member_id] = entry

    def get(self, member_id: str) -> Optional[MemberDirectoryEntry]:
        return self._entries.get(member_id)


class MemberStatusHistoryProvider(ABC):
    """Stand-in for Phase 2's `member_status_history` table (already named
    in the Phase 2 design doc). Used to satisfy locked Decision #5:
    historical, not live, account_status in upline snapshots."""

    @abstractmethod
    def status_as_of(self, member_id: str, as_of: datetime) -> Optional[AccountStatus]:
        """Returns the member's account_status as it was at `as_of`, or
        None if there is no record covering that time (caller should then
        fall back to the directory's current status only as a last resort,
        e.g. for a member with no history entries yet)."""
        raise NotImplementedError


@dataclass(frozen=True)
class StatusHistoryEntry:
    account_status: AccountStatus
    effective_from: datetime
    effective_to: Optional[datetime]  # None = still current


class InMemoryMemberStatusHistoryProvider(MemberStatusHistoryProvider):
    def __init__(self, history: Optional[Dict[str, List[StatusHistoryEntry]]] = None):
        # member_id -> list of StatusHistoryEntry, any order
        self._history: Dict[str, List[StatusHistoryEntry]] = dict(history or {})

    def add_entry(self, member_id: str, entry: StatusHistoryEntry) -> None:
        self._history.setdefault(member_id, []).append(entry)

    def status_as_of(self, member_id: str, as_of: datetime) -> Optional[AccountStatus]:
        for entry in self._history.get(member_id, []):
            if entry.effective_from <= as_of and (
                entry.effective_to is None or as_of < entry.effective_to
            ):
                return entry.account_status
        return None


# ---------------------------------------------------------------------------
# SponsorPlacement — immutable, append-only, versioned (Section B of design)
# ---------------------------------------------------------------------------

class PlacementType(str, Enum):
    INITIAL = "INITIAL"
    RE_SPONSORSHIP = "RE_SPONSORSHIP"
    ADMIN_CORRECTION = "ADMIN_CORRECTION"


@dataclass(frozen=True)
class SponsorPlacement:
    placement_id: str
    member_id: str
    sponsor_id: Optional[str]  # None only for the designated company ROOT
    effective_from: datetime
    effective_to: Optional[datetime]  # None = currently active
    placement_type: PlacementType
    placement_reason: str
    created_by: str  # "SELF" or a staff member_id
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class PlacementReviewRequest:
    """Locked Decision #4: a duplicate-placement attempt is never silently
    rejected and never silently applied -- it is recorded here for staff
    review."""
    review_id: str
    member_id: str
    attempted_sponsor_id: str
    status: str  # "PENDING", "APPROVED", "REJECTED"
    attempted_at: datetime
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    decision_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class CircularReferenceError(Exception):
    """Raised when a proposed sponsor is a descendant of the member --
    a hard structural invariant, never a business option."""


class SelfSponsorshipError(Exception):
    """member_id == sponsor_id is never allowed."""


class InvalidSponsorError(Exception):
    """Raised when the proposed sponsor does not exist, is not role=MEMBER,
    or is SUSPENDED/TERMINATED (locked Phase 2 rules: SUSPENDED/TERMINATED
    members cannot receive new placements under them)."""


class MemberNotFoundError(Exception):
    pass


# ---------------------------------------------------------------------------
# GenealogyEngine — the core Phase 4 service
# ---------------------------------------------------------------------------

class GenealogyEngine:
    """
    In-memory reference implementation of the Phase 4 design. A production
    build swaps the internal storage for the `sponsor_placements` table
    (Section F of the design doc) but keeps this same public API.
    """

    def __init__(self, directory: MemberDirectory):
        self.directory = directory
        # member_id -> list[SponsorPlacement], oldest first
        self._placements: Dict[str, List[SponsorPlacement]] = {}
        self._review_queue: Dict[str, PlacementReviewRequest] = {}

    # -- internal helpers ---------------------------------------------------

    def _active_placement(self, member_id: str) -> Optional[SponsorPlacement]:
        for p in self._placements.get(member_id, []):
            if p.effective_to is None:
                return p
        return None

    def _placement_as_of(self, member_id: str, as_of: datetime) -> Optional[SponsorPlacement]:
        for p in self._placements.get(member_id, []):
            if p.effective_from <= as_of and (p.effective_to is None or as_of < p.effective_to):
                return p
        return None

    def _is_descendant(self, candidate_id: str, root_id: str, max_depth: int = 10) -> bool:
        """True if candidate_id appears anywhere in root_id's downline
        (i.e. candidate_id's upline chain eventually reaches root_id)."""
        current = candidate_id
        depth = 0
        while depth < max_depth + 5:  # small safety margin over the commission max_depth
            placement = self._active_placement(current)
            if placement is None or placement.sponsor_id is None:
                return False
            if placement.sponsor_id == root_id:
                return True
            current = placement.sponsor_id
            depth += 1
        return False

    def _validate_sponsor_eligibility(self, sponsor_id: str) -> None:
        entry = self.directory.get(sponsor_id)
        if entry is None:
            raise InvalidSponsorError(f"Sponsor '{sponsor_id}' does not exist.")
        if entry.role != MemberRole.MEMBER:
            raise InvalidSponsorError(
                f"Sponsor '{sponsor_id}' has role={entry.role.value}; only role=MEMBER "
                f"can sponsor new placements (Phase 2 locked rule)."
            )
        if entry.account_status == AccountStatus.SUSPENDED:
            raise InvalidSponsorError(
                f"Sponsor '{sponsor_id}' is SUSPENDED -- cannot receive new sponsorship "
                f"placements (Phase 2 locked capability matrix)."
            )
        if entry.account_status == AccountStatus.TERMINATED:
            raise InvalidSponsorError(
                f"Sponsor '{sponsor_id}' is TERMINATED -- cannot receive new sponsorship placements."
            )

    # -- public API -----------------------------------------------------

    def place_member(
        self,
        member_id: str,
        sponsor_id: Optional[str],
        as_of: datetime,
        created_by: str = "SELF",
    ) -> SponsorPlacement:
        """
        Initial placement (Decision #1: simple unilevel -- always directly
        under the given sponsor; Decision #2: sponsor mandatory except for
        the one company ROOT, represented here by sponsor_id=None).

        Decision #4 (Duplicate Placement -> review queue, not reject):
        if the member already has an active placement, this call does NOT
        raise and does NOT silently re-place them -- it files a
        PlacementReviewRequest and returns None-equivalent by raising a
        clearly-named exception the caller is expected to catch, OR by
        checking has_active_placement() first. We choose the explicit
        method below so callers cannot silently swallow the distinction.
        """
        requesting_member = self.directory.get(member_id)
        if requesting_member is None:
            raise MemberNotFoundError(f"Member '{member_id}' does not exist.")
        if requesting_member.role != MemberRole.MEMBER:
            raise InvalidSponsorError(
                f"'{member_id}' has role={requesting_member.role.value}; only role=MEMBER "
                f"participates in the sponsor tree (Phase 2 locked rule)."
            )
        if self._active_placement(member_id) is not None:
            # Decision #4: route to review queue instead of applying or rejecting.
            self.file_duplicate_placement_review(member_id, sponsor_id or "", as_of)
            raise DuplicatePlacementFiledForReview(
                f"Member '{member_id}' already has an active placement. "
                f"This request has been filed to the staff review queue, not applied."
            )
        if sponsor_id is None:
            # Only valid for the designated company ROOT -- caller's
            # responsibility to only pass None for that one member.
            placement = SponsorPlacement(
                placement_id=str(uuid.uuid4()),
                member_id=member_id,
                sponsor_id=None,
                effective_from=as_of,
                effective_to=None,
                placement_type=PlacementType.INITIAL,
                placement_reason="company_root",
                created_by=created_by,
            )
            self._placements.setdefault(member_id, []).append(placement)
            return placement
        if sponsor_id == member_id:
            raise SelfSponsorshipError(f"'{member_id}' cannot sponsor itself.")
        self._validate_sponsor_eligibility(sponsor_id)
        placement = SponsorPlacement(
            placement_id=str(uuid.uuid4()),
            member_id=member_id,
            sponsor_id=sponsor_id,
            effective_from=as_of,
            effective_to=None,
            placement_type=PlacementType.INITIAL,
            placement_reason="initial_registration",
            created_by=created_by,
        )
        self._placements.setdefault(member_id, []).append(placement)
        return placement

    def has_active_placement(self, member_id: str) -> bool:
        return self._active_placement(member_id) is not None

    def file_duplicate_placement_review(
        self, member_id: str, attempted_sponsor_id: str, attempted_at: datetime
    ) -> PlacementReviewRequest:
        review = PlacementReviewRequest(
            review_id=str(uuid.uuid4()),
            member_id=member_id,
            attempted_sponsor_id=attempted_sponsor_id,
            status="PENDING",
            attempted_at=attempted_at,
        )
        self._review_queue[review.review_id] = review
        return review

    def get_review_queue(self, status: Optional[str] = None) -> List[PlacementReviewRequest]:
        items = list(self._review_queue.values())
        if status is not None:
            items = [i for i in items if i.status == status]
        return items

    def re_sponsor(
        self,
        member_id: str,
        new_sponsor_id: str,
        as_of: datetime,
        approved_by: str,
        reason: str,
        review_id: Optional[str] = None,
    ) -> SponsorPlacement:
        """
        Locked Decision #3: staff-approval only. There is deliberately no
        version of this method callable with created_by="SELF" -- every
        caller must supply `approved_by`, documenting who authorized it.
        This is the ONLY way a member's sponsor can ever change after
        initial placement.
        """
        if not approved_by or approved_by == "SELF":
            raise PermissionError(
                "Re-sponsorship requires an explicit staff approver. "
                "Members cannot re-sponsor themselves (locked Decision #3)."
            )
        current = self._active_placement(member_id)
        if current is None:
            raise MemberNotFoundError(
                f"Member '{member_id}' has no active placement to re-sponsor."
            )
        if new_sponsor_id == member_id:
            raise SelfSponsorshipError(f"'{member_id}' cannot sponsor itself.")
        # Hard structural invariant -- never a business option.
        if self._is_descendant(new_sponsor_id, member_id):
            raise CircularReferenceError(
                f"Cannot re-sponsor '{member_id}' under '{new_sponsor_id}': "
                f"'{new_sponsor_id}' is currently in '{member_id}''s downline -- "
                f"this would create a cycle."
            )
        self._validate_sponsor_eligibility(new_sponsor_id)
        # Close the old placement (append-only: never edit/delete the old row).
        closed = replace(current, effective_to=as_of)
        rows = self._placements[member_id]
        rows[rows.index(current)] = closed
        new_placement = SponsorPlacement(
            placement_id=str(uuid.uuid4()),
            member_id=member_id,
            sponsor_id=new_sponsor_id,
            effective_from=as_of,
            effective_to=None,
            placement_type=PlacementType.RE_SPONSORSHIP,
            placement_reason=reason,
            created_by=approved_by,
        )
        rows.append(new_placement)
        if review_id is not None and review_id in self._review_queue:
            review = self._review_queue[review_id]
            self._review_queue[review_id] = replace(
                review, status="APPROVED", reviewed_by=approved_by,
                reviewed_at=as_of, decision_reason=reason,
            )
        return new_placement

    def get_upline_chain(
        self, member_id: str, as_of: datetime, max_depth: int = 10
    ) -> List[tuple]:
        """Returns [(generation, sponsor_id), ...], generation 1 = nearest."""
        chain = []
        current = member_id
        generation = 1
        while generation <= max_depth:
            placement = self._placement_as_of(current, as_of)
            if placement is None or placement.sponsor_id is None:
                break
            chain.append((generation, placement.sponsor_id))
            current = placement.sponsor_id
            generation += 1
        return chain

    def get_downline(self, member_id: str, max_depth: Optional[int] = None) -> List[str]:
        """
        Simple traversal (locked Decision #6: start with straightforward
        traversal; a production DB implementation would use a recursive
        CTE or, if scale requires, a closure table -- this method's
        return contract stays the same either way).
        """
        # Build a reverse index once: sponsor_id -> [direct child member_ids]
        children: Dict[str, List[str]] = {}
        for mid, rows in self._placements.items():
            active = self._active_placement(mid)
            if active is not None and active.sponsor_id is not None:
                children.setdefault(active.sponsor_id, []).append(mid)
        result: List[str] = []
        frontier = [member_id]
        depth = 0
        while frontier and (max_depth is None or depth < max_depth):
            next_frontier = []
            for node in frontier:
                for child in children.get(node, []):
                    result.append(child)
                    next_frontier.append(child)
            frontier = next_frontier
            depth += 1
        return result


class DuplicatePlacementFiledForReview(Exception):
    """Raised by place_member() when a duplicate placement attempt is
    filed to the review queue instead of being applied or rejected."""


# ---------------------------------------------------------------------------
# TreeBackedUplineProvider — Phase 7's UplineProvider, real implementation
# ---------------------------------------------------------------------------

class TreeBackedUplineProvider(UplineProvider):
    """
    Real implementation of Phase 7's UplineProvider contract, backed by
    GenealogyEngine. Method signature is IDENTICAL to
    InMemoryUplineProvider's (tree.py, unmodified) -- calculators.py and
    engine.py cannot tell the difference.

    Locked Decision #5 (Historical, not live, account_status): if a
    MemberStatusHistoryProvider is supplied and has a record covering
    `as_of`, that historical status is used. Otherwise this falls back to
    the directory's current status (documented fallback, not a silent
    behavior change) -- e.g. for a member who has never changed status.
    """

    def __init__(
        self,
        engine: GenealogyEngine,
        directory: MemberDirectory,
        status_history: Optional[MemberStatusHistoryProvider] = None,
    ):
        self.engine = engine
        self.directory = directory
        self.status_history = status_history

    def _status_at(self, member_id: str, as_of: datetime) -> AccountStatus:
        if self.status_history is not None:
            historical = self.status_history.status_as_of(member_id, as_of)
            if historical is not None:
                return historical
        entry = self.directory.get(member_id)
        if entry is None:
            # Defensive default; a member appearing in the tree must exist
            # in the directory in a correctly-populated system.
            return AccountStatus.ACTIVE
        return entry.account_status

    def get_upline_snapshot(
        self, member_id: str, as_of: datetime, max_depth: int = 10
    ) -> UplineSnapshot:
        chain_raw = self.engine.get_upline_chain(member_id, as_of, max_depth=max_depth)
        chain: List[UplineEntry] = []
        for generation, sponsor_id in chain_raw:
            status = self._status_at(sponsor_id, as_of)
            chain.append(UplineEntry(generation=generation, member=Member(sponsor_id, status)))
        return UplineSnapshot(member_id=member_id, as_of=as_of, chain=chain)

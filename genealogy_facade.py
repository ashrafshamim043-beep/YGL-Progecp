"""
GenealogyFacade -- the ONLY place in this codebase (besides db_adapters.py)
that imports commission_engine.genealogy. Sponsor Management (app/modules/
sponsor/) NEVER imports commission_engine directly -- it calls into this
facade, which wraps the frozen GenealogyEngine and re-exports its
exceptions unmodified. This keeps the import-boundary rule intact while
letting Sponsor reuse the Engine's real placement-validation logic
(self-sponsorship, circular-reference, duplicate-placement-review,
invalid-sponsor checks) instead of re-implementing any of it.

No business logic lives here -- this file only constructs a GenealogyEngine
from data the caller supplies and forwards calls to it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from commission_engine.genealogy import (
    GenealogyEngine, InMemoryMemberDirectory, MemberDirectoryEntry, MemberRole,
    AccountStatus, SponsorPlacement, PlacementReviewRequest,
    CircularReferenceError, SelfSponsorshipError, InvalidSponsorError,
    MemberNotFoundError, DuplicatePlacementFiledForReview,
)

# Re-exported so Sponsor module can catch these without importing
# commission_engine itself.
__all__ = [
    "GenealogyFacade", "CircularReferenceError", "SelfSponsorshipError",
    "InvalidSponsorError", "MemberNotFoundError", "DuplicatePlacementFiledForReview",
    "SponsorPlacement", "PlacementReviewRequest",
]


class GenealogyFacade:
    """
    Constructed fresh per operation (mirrors how CommissionEngine itself is
    constructed fresh, never held as long-lived global state -- see
    Specification Refinement v2). The caller (SponsorService) supplies:
      - member_directory_entries: every member's (id, role, account_status)
        needed for validation
      - existing_placements: every prior placement, replayed into a fresh
        GenealogyEngine so its internal state matches the database exactly
        before the new operation is attempted.
    """

    def __init__(
        self,
        member_directory_entries: List[Tuple[str, str, str]],  # (member_id, role, account_status)
        existing_placements: List[SponsorPlacement],
    ):
        directory = InMemoryMemberDirectory()
        for member_id, role, account_status in member_directory_entries:
            directory.add(MemberDirectoryEntry(
                member_id, MemberRole(role), AccountStatus(account_status),
            ))
        self._engine = GenealogyEngine(directory)
        # Replay existing placements so the engine's internal state
        # (which drives duplicate/circular-reference detection) reflects
        # the real, persisted history -- never re-validated on replay,
        # only the NEW operation below is validated.
        for placement in existing_placements:
            self._engine._placements.setdefault(placement.member_id, []).append(placement)

    def place_member(self, member_id: str, sponsor_id: Optional[str], as_of: datetime,
                      created_by: str = "SELF") -> SponsorPlacement:
        return self._engine.place_member(member_id, sponsor_id, as_of, created_by)

    def re_sponsor(self, member_id: str, new_sponsor_id: str, as_of: datetime,
                    approved_by: str, reason: str, review_id: Optional[str] = None) -> SponsorPlacement:
        return self._engine.re_sponsor(member_id, new_sponsor_id, as_of, approved_by, reason, review_id)

    def get_review_queue(self, status: Optional[str] = None) -> List[PlacementReviewRequest]:
        return self._engine.get_review_queue(status)

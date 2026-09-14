"""
Upline tree snapshotting.

Commission calculations must NEVER walk a "live" genealogy tree — they
must use a frozen, point-in-time snapshot as of the order's timestamp.
This module defines that contract as an abstract interface so the real
implementation (backed by a genealogy database) can be swapped in later
without touching calculator logic.

An in-memory implementation is provided for testing and demonstration.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List

from .models import Member, UplineEntry, UplineSnapshot, AccountStatus


class UplineProvider(ABC):
    @abstractmethod
    def get_upline_snapshot(self, member_id: str, as_of: datetime, max_depth: int = 10) -> UplineSnapshot:
        """Return a frozen upline snapshot, nearest sponsor = generation 1."""
        raise NotImplementedError


class InMemoryUplineProvider(UplineProvider):
    """
    Simple in-memory tree for tests/demos.

    sponsor_map: member_id -> sponsor_member_id (direct upline).
    members: member_id -> Member (with account status).
    """

    def __init__(self, sponsor_map: Dict[str, str], members: Dict[str, Member]):
        self.sponsor_map = sponsor_map
        self.members = members

    def get_upline_snapshot(self, member_id: str, as_of: datetime, max_depth: int = 10) -> UplineSnapshot:
        chain: List[UplineEntry] = []
        current = member_id
        generation = 1
        while generation <= max_depth:
            sponsor_id = self.sponsor_map.get(current)
            if not sponsor_id:
                break
            sponsor = self.members.get(sponsor_id, Member(sponsor_id, AccountStatus.ACTIVE))
            chain.append(UplineEntry(generation=generation, member=sponsor))
            current = sponsor_id
            generation += 1
        return UplineSnapshot(member_id=member_id, as_of=as_of, chain=chain)
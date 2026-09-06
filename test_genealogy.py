import unittest
from datetime import datetime, timezone

from commission_engine.models import AccountStatus
from commission_engine.genealogy import (
    GenealogyEngine,
    InMemoryMemberDirectory,
    MemberDirectoryEntry,
    MemberRole,
    InMemoryMemberStatusHistoryProvider,
    StatusHistoryEntry,
    TreeBackedUplineProvider,
    DuplicatePlacementFiledForReview,
    SelfSponsorshipError,
    InvalidSponsorError,
    CircularReferenceError,
)

NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


def make_directory(*member_ids_roles_statuses):
    directory = InMemoryMemberDirectory()
    for member_id, role, status in member_ids_roles_statuses:
        directory.add(MemberDirectoryEntry(member_id=member_id, role=role, account_status=status))
    return directory


class TestPlaceMember(unittest.TestCase):
    def test_root_placement_has_no_sponsor(self):
        directory = make_directory(("root", MemberRole.MEMBER, AccountStatus.ACTIVE))
        engine = GenealogyEngine(directory)
        placement = engine.place_member("root", None, NOW)
        self.assertIsNone(placement.sponsor_id)

    def test_initial_placement_under_sponsor(self):
        directory = make_directory(
            ("root", MemberRole.MEMBER, AccountStatus.ACTIVE),
            ("m1", MemberRole.MEMBER, AccountStatus.ACTIVE),
        )
        engine = GenealogyEngine(directory)
        engine.place_member("root", None, NOW)
        placement = engine.place_member("m1", "root", NOW)
        self.assertEqual(placement.sponsor_id, "root")

    def test_self_sponsorship_rejected(self):
        directory = make_directory(("m1", MemberRole.MEMBER, AccountStatus.ACTIVE))
        engine = GenealogyEngine(directory)
        with self.assertRaises(SelfSponsorshipError):
            engine.place_member("m1", "m1", NOW)

    def test_suspended_sponsor_rejected(self):
        directory = make_directory(
            ("sponsor", MemberRole.MEMBER, AccountStatus.SUSPENDED),
            ("m1", MemberRole.MEMBER, AccountStatus.ACTIVE),
        )
        engine = GenealogyEngine(directory)
        with self.assertRaises(InvalidSponsorError):
            engine.place_member("m1", "sponsor", NOW)

    def test_duplicate_placement_filed_for_review_not_applied(self):
        directory = make_directory(
            ("root", MemberRole.MEMBER, AccountStatus.ACTIVE),
            ("m1", MemberRole.MEMBER, AccountStatus.ACTIVE),
        )
        engine = GenealogyEngine(directory)
        engine.place_member("root", None, NOW)
        engine.place_member("m1", "root", NOW)
        with self.assertRaises(DuplicatePlacementFiledForReview):
            engine.place_member("m1", "root", NOW)
        pending = engine.get_review_queue(status="PENDING")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].member_id, "m1")


class TestReSponsor(unittest.TestCase):
    def test_re_sponsor_requires_staff_approver(self):
        directory = make_directory(
            ("root", MemberRole.MEMBER, AccountStatus.ACTIVE),
            ("m1", MemberRole.MEMBER, AccountStatus.ACTIVE),
            ("m2", MemberRole.MEMBER, AccountStatus.ACTIVE),
        )
        engine = GenealogyEngine(directory)
        engine.place_member("root", None, NOW)
        engine.place_member("m1", "root", NOW)
        engine.place_member("m2", "root", NOW)
        with self.assertRaises(PermissionError):
            engine.re_sponsor("m1", "m2", NOW, approved_by="SELF", reason="test")

    def test_re_sponsor_circular_reference_blocked(self):
        directory = make_directory(
            ("root", MemberRole.MEMBER, AccountStatus.ACTIVE),
            ("m1", MemberRole.MEMBER, AccountStatus.ACTIVE),
            ("m2", MemberRole.MEMBER, AccountStatus.ACTIVE),
        )
        engine = GenealogyEngine(directory)
        engine.place_member("root", None, NOW)
        engine.place_member("m1", "root", NOW)
        engine.place_member("m2", "m1", NOW)
        # m2 is in m1's downline; sponsoring m1 under m2 would create a cycle.
        with self.assertRaises(CircularReferenceError):
            engine.re_sponsor("m1", "m2", NOW, approved_by="staff_1", reason="test")


class TestUplineChainAndDownline(unittest.TestCase):
    def setUp(self):
        self.directory = make_directory(
            ("root", MemberRole.MEMBER, AccountStatus.ACTIVE),
            ("m1", MemberRole.MEMBER, AccountStatus.ACTIVE),
            ("m2", MemberRole.MEMBER, AccountStatus.ACTIVE),
        )
        self.engine = GenealogyEngine(self.directory)
        self.engine.place_member("root", None, NOW)
        self.engine.place_member("m1", "root", NOW)
        self.engine.place_member("m2", "m1", NOW)

    def test_upline_chain_nearest_first(self):
        chain = self.engine.get_upline_chain("m2", NOW)
        self.assertEqual(chain, [(1, "m1"), (2, "root")])

    def test_downline_traversal(self):
        downline = self.engine.get_downline("root")
        self.assertIn("m1", downline)
        self.assertIn("m2", downline)


class TestTreeBackedUplineProvider(unittest.TestCase):
    def test_uses_historical_status_when_available(self):
        directory = make_directory(
            ("root", MemberRole.MEMBER, AccountStatus.ACTIVE),
            ("sponsor", MemberRole.MEMBER, AccountStatus.ACTIVE),  # current status: ACTIVE
            ("m1", MemberRole.MEMBER, AccountStatus.ACTIVE),
        )
        engine = GenealogyEngine(directory)
        engine.place_member("root", None, NOW)
        engine.place_member("sponsor", "root", NOW)
        engine.place_member("m1", "sponsor", NOW)

        # Historical record says sponsor was SUSPENDED at the order timestamp,
        # even though their current status (in `directory`) is ACTIVE.
        history = InMemoryMemberStatusHistoryProvider()
        history.add_entry(
            "sponsor",
            StatusHistoryEntry(
                account_status=AccountStatus.SUSPENDED,
                effective_from=NOW,
                effective_to=None,
            ),
        )
        provider = TreeBackedUplineProvider(engine, directory, status_history=history)
        snapshot = provider.get_upline_snapshot("m1", NOW)
        sponsor_entry = snapshot.entry_at(1)
        self.assertEqual(sponsor_entry.member.account_status, AccountStatus.SUSPENDED)

    def test_falls_back_to_directory_status_when_no_history(self):
        directory = make_directory(
            ("root", MemberRole.MEMBER, AccountStatus.ACTIVE),
            ("m1", MemberRole.MEMBER, AccountStatus.ACTIVE),
        )
        engine = GenealogyEngine(directory)
        engine.place_member("root", None, NOW)
        engine.place_member("m1", "root", NOW)

        provider = TreeBackedUplineProvider(engine, directory, status_history=None)
        snapshot = provider.get_upline_snapshot("m1", NOW)
        self.assertEqual(snapshot.entry_at(1).member.account_status, AccountStatus.ACTIVE)


if __name__ == "__main__":
    unittest.main()

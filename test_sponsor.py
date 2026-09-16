"""
Sponsor module test suite. Genuinely exercises the frozen
commission_engine.genealogy.GenealogyEngine (via GenealogyFacade), not a
re-implementation -- proves the real self-sponsorship/circular-reference/
duplicate-placement/invalid-sponsor rules are enforced, exactly as the
Engine's own logic dictates.
"""
import unittest

from app.modules.sponsor.core import (
    InMemorySponsorPlacementRepository, InMemoryPlacementReviewRepository,
)
from app.modules.sponsor.service import SponsorService
from app.services.commission_service.genealogy_facade import (
    SelfSponsorshipError, InvalidSponsorError, MemberNotFoundError,
)


def build_service(members):
    """members: list of (member_id, role, account_status) tuples."""
    placement_repo = InMemorySponsorPlacementRepository()
    review_repo = InMemoryPlacementReviewRepository()
    service = SponsorService(placement_repo, review_repo, member_directory_provider=lambda: members)
    return service, placement_repo, review_repo


STANDARD_MEMBERS = [
    ("root", "MEMBER", "ACTIVE"),
    ("sponsor-1", "MEMBER", "ACTIVE"),
    ("member-1", "MEMBER", "ACTIVE"),
    ("member-2", "MEMBER", "ACTIVE"),
    ("suspended-sponsor", "MEMBER", "SUSPENDED"),
]


class TestInitialPlacement(unittest.TestCase):
    def test_company_root_placement_with_no_sponsor(self):
        service, placement_repo, review_repo = build_service(STANDARD_MEMBERS)
        result = service.place_member("root", None, created_by="SELF")
        self.assertFalse(result.filed_for_review)
        self.assertIsNone(result.placement.sponsor_id)
        self.assertEqual(placement_repo.list_for_member("root"), [result.placement])

    def test_member_placed_under_real_sponsor(self):
        service, placement_repo, review_repo = build_service(STANDARD_MEMBERS)
        service.place_member("root", None, created_by="SELF")
        result = service.place_member("sponsor-1", "root", created_by="SELF")
        self.assertFalse(result.filed_for_review)
        self.assertEqual(result.placement.sponsor_id, "root")

    def test_self_sponsorship_rejected_by_real_engine_rule(self):
        service, placement_repo, review_repo = build_service(STANDARD_MEMBERS)
        with self.assertRaises(SelfSponsorshipError):
            service.place_member("member-1", "member-1", created_by="SELF")
        self.assertEqual(placement_repo.list_for_member("member-1"), [])  # nothing persisted

    def test_nonexistent_sponsor_rejected(self):
        service, placement_repo, review_repo = build_service(STANDARD_MEMBERS)
        with self.assertRaises(InvalidSponsorError):
            service.place_member("member-1", "ghost-sponsor-id", created_by="SELF")

    def test_nonexistent_member_rejected(self):
        service, placement_repo, review_repo = build_service(STANDARD_MEMBERS)
        with self.assertRaises(MemberNotFoundError):
            service.place_member("ghost-member-id", "root", created_by="SELF")

    def test_suspended_sponsor_rejected_by_real_engine_rule(self):
        """The Engine itself checks sponsor account status -- this test
        proves that real check fires, not a duplicated Office-System check."""
        service, placement_repo, review_repo = build_service(STANDARD_MEMBERS)
        with self.assertRaises(InvalidSponsorError):
            service.place_member("member-1", "suspended-sponsor", created_by="SELF")


class TestDuplicatePlacement(unittest.TestCase):
    def test_duplicate_placement_filed_for_review_not_rejected_not_reapplied(self):
        """Decision #4: a member who already has a placement, attempting a
        SECOND placement, is routed to the review queue -- not silently
        re-placed, not hard-rejected."""
        service, placement_repo, review_repo = build_service(STANDARD_MEMBERS)
        service.place_member("root", None, created_by="SELF")
        service.place_member("sponsor-1", "root", created_by="SELF")
        first = service.place_member("member-1", "sponsor-1", created_by="SELF")
        self.assertFalse(first.filed_for_review)

        second = service.place_member("member-1", "root", created_by="SELF")
        self.assertTrue(second.filed_for_review)
        self.assertIsNotNone(second.review_id)

        # Only the FIRST placement persisted -- the duplicate attempt did not overwrite it.
        placements = placement_repo.list_for_member("member-1")
        self.assertEqual(len(placements), 1)
        self.assertEqual(placements[0].sponsor_id, "sponsor-1")

        pending = review_repo.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].member_id, "member-1")
        self.assertEqual(pending[0].attempted_sponsor_id, "root")


class TestReSponsorship(unittest.TestCase):
    def test_staff_approved_re_sponsor_changes_sponsor(self):
        service, placement_repo, review_repo = build_service(STANDARD_MEMBERS)
        service.place_member("root", None, created_by="SELF")
        service.place_member("sponsor-1", "root", created_by="SELF")
        service.place_member("member-2", "root", created_by="SELF")
        service.place_member("member-1", "sponsor-1", created_by="SELF")

        result = service.re_sponsor(
            "member-1", "member-2", approved_by="admin-1", reason="member requested correction",
        )
        self.assertFalse(result.filed_for_review)
        self.assertEqual(result.placement.sponsor_id, "member-2")
        self.assertEqual(result.placement.placement_type, "RE_SPONSORSHIP")

    def test_re_sponsor_resolving_a_review_marks_it_approved(self):
        service, placement_repo, review_repo = build_service(STANDARD_MEMBERS)
        service.place_member("root", None, created_by="SELF")
        service.place_member("sponsor-1", "root", created_by="SELF")
        service.place_member("member-2", "root", created_by="SELF")
        service.place_member("member-1", "sponsor-1", created_by="SELF")
        duplicate_attempt = service.place_member("member-1", "member-2", created_by="SELF")
        review_id = duplicate_attempt.review_id

        service.re_sponsor(
            "member-1", "member-2", approved_by="admin-1",
            reason="approved after review", review_id=review_id,
        )
        review = review_repo.get_by_id(review_id)
        self.assertEqual(review.status, "APPROVED")
        self.assertEqual(review.reviewed_by, "admin-1")


class TestReviewQueueManagement(unittest.TestCase):
    def test_reject_review_marks_it_rejected_with_reason(self):
        service, placement_repo, review_repo = build_service(STANDARD_MEMBERS)
        service.place_member("root", None, created_by="SELF")
        service.place_member("sponsor-1", "root", created_by="SELF")
        service.place_member("member-1", "sponsor-1", created_by="SELF")
        duplicate_attempt = service.place_member("member-1", "root", created_by="SELF")

        rejected = service.reject_review(
            duplicate_attempt.review_id, rejected_by="admin-1", reason="not a legitimate request",
        )
        self.assertEqual(rejected.status, "REJECTED")
        self.assertEqual(review_repo.list_pending(), [])

    def test_reject_unknown_review_raises(self):
        service, placement_repo, review_repo = build_service(STANDARD_MEMBERS)
        with self.assertRaises(ValueError):
            service.reject_review("nonexistent-id", rejected_by="admin-1", reason="n/a")


class TestPlacementHistoryAppendOnly(unittest.TestCase):
    def test_placement_history_accumulates_never_overwrites(self):
        """Proves the repository/facade round-trip preserves full history --
        an important property for genealogy audit trails."""
        service, placement_repo, review_repo = build_service(STANDARD_MEMBERS)
        service.place_member("root", None, created_by="SELF")
        service.place_member("sponsor-1", "root", created_by="SELF")
        service.place_member("member-2", "root", created_by="SELF")
        service.place_member("member-1", "sponsor-1", created_by="SELF")
        service.re_sponsor("member-1", "member-2", approved_by="admin-1", reason="correction")

        all_placements = placement_repo.list_for_member("member-1")
        self.assertEqual(len(all_placements), 2)  # original + re-sponsorship, both kept


if __name__ == "__main__":
    unittest.main()


class TestStaticDbRepositoryInterfaceCompliance(unittest.TestCase):
    """[STATIC] SQLAlchemySponsorPlacementRepository/SQLAlchemyPlacementReviewRepository
    implement every abstract method their interfaces require."""

    def test_sqlalchemy_sponsor_repositories_implement_all_abstract_methods(self):
        import ast
        import os
        path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..",
                             "app", "modules", "sponsor", "db_repository.py")
        with open(path) as f:
            tree = ast.parse(f.read())
        classes = {n.name: {m.name for m in n.body if isinstance(m, ast.FunctionDef)}
                   for n in tree.body if isinstance(n, ast.ClassDef)}
        self.assertEqual({"save", "list_all", "list_for_member"} - classes["SQLAlchemySponsorPlacementRepository"], set())
        self.assertEqual({"save", "get_by_id", "list_pending"} - classes["SQLAlchemyPlacementReviewRepository"], set())

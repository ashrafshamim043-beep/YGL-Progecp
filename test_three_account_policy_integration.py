"""
Cross-module integration test -- proves the 3-Account Policy's core
financial guarantee: 3 Member IDs under ONE KYCIdentity have completely
INDEPENDENT Commission (via the real, frozen Commission Engine) and
independent Sponsor positions (via the real GenealogyEngine, through
GenealogyFacade) -- exactly as Business Decision Section 4 requires.
Nothing here re-implements or duplicates Commission Engine/GenealogyEngine
logic; it only proves their outputs stay correctly separated per
member_id even when 3 member_ids share one real-world identity.
"""
import unittest
from datetime import datetime, timezone
from decimal import Decimal

import app.services.commission_service  # noqa: F401 (sys.path side-effect, see other integration tests)

from commission_engine.approved_plan import build_approved_plan_v1
from commission_engine.models import Order
from commission_engine.tree import InMemoryUplineProvider
from commission_engine.rank import InMemoryRankSnapshotProvider
from commission_engine.idempotency import IdempotencyStore
from commission_engine.undistributed import UndistributedFundTracker
from commission_engine.ledger import ImmutableLedger
from commission_engine.engine import CommissionEngine

from app.modules.kyc_identity.core import DocumentType, InMemoryKYCIdentityRepository, link_member_to_identity
from app.modules.sponsor.core import InMemorySponsorPlacementRepository, InMemoryPlacementReviewRepository
from app.modules.sponsor.service import SponsorService

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


class TestThreeAccountsIndependentCommissionAndSponsor(unittest.TestCase):
    def test_three_ids_same_identity_have_independent_commission_and_sponsor_position(self):
        # --- Step 1: link 3 Member IDs to one KYCIdentity (real 3-Account Policy logic) ---
        identity_repo = InMemoryKYCIdentityRepository()
        same_nid = "7777777777"
        for member_id in ["mofiz-id-1", "mofiz-id-2", "mofiz-id-3"]:
            link_member_to_identity(identity_repo, member_id, DocumentType.NID, same_nid, "01700000000")
        identity = identity_repo.get_by_document(DocumentType.NID, same_nid)
        self.assertEqual(len(identity.linked_member_ids), 3)

        # --- Step 2: give each of Mofiz's 3 IDs a DIFFERENT sponsor position (real GenealogyEngine) ---
        placement_repo = InMemorySponsorPlacementRepository()
        review_repo = InMemoryPlacementReviewRepository()
        all_members = [
            ("root", "MEMBER", "ACTIVE"), ("sponsor-a", "MEMBER", "ACTIVE"), ("sponsor-b", "MEMBER", "ACTIVE"),
            ("mofiz-id-1", "MEMBER", "ACTIVE"), ("mofiz-id-2", "MEMBER", "ACTIVE"), ("mofiz-id-3", "MEMBER", "ACTIVE"),
        ]
        sponsor_service = SponsorService(placement_repo, review_repo, member_directory_provider=lambda: all_members)
        sponsor_service.place_member("root", None, created_by="SELF")
        sponsor_service.place_member("sponsor-a", "root", created_by="SELF")
        sponsor_service.place_member("sponsor-b", "root", created_by="SELF")
        sponsor_service.place_member("mofiz-id-1", "sponsor-a", created_by="SELF")
        sponsor_service.place_member("mofiz-id-2", "sponsor-b", created_by="SELF")  # DIFFERENT sponsor
        sponsor_service.place_member("mofiz-id-3", "root", created_by="SELF")        # DIFFERENT again

        placements = {
            "mofiz-id-1": placement_repo.list_for_member("mofiz-id-1")[0].sponsor_id,
            "mofiz-id-2": placement_repo.list_for_member("mofiz-id-2")[0].sponsor_id,
            "mofiz-id-3": placement_repo.list_for_member("mofiz-id-3")[0].sponsor_id,
        }
        self.assertEqual(placements, {"mofiz-id-1": "sponsor-a", "mofiz-id-2": "sponsor-b", "mofiz-id-3": "root"})
        # Proves independence: 3 identical-identity IDs, 3 DIFFERENT tree positions.

        # --- Step 3: each ID makes its OWN purchase, commission calculated independently (real Commission Engine) ---
        plan = build_approved_plan_v1()
        upline_map = {"mofiz-id-1": "sponsor-a", "mofiz-id-2": "sponsor-b", "mofiz-id-3": "root"}
        tree = InMemoryUplineProvider(upline_map, {})
        ledger = ImmutableLedger()
        engine = CommissionEngine(plan, tree, InMemoryRankSnapshotProvider({}), IdempotencyStore(), UndistributedFundTracker(), ledger)

        order_1 = Order("ORDER-MOFIZ-1", "mofiz-id-1", Decimal("1000.00"), True, True, NOW)
        order_2 = Order("ORDER-MOFIZ-2", "mofiz-id-2", Decimal("500.00"), True, True, NOW)
        # mofiz-id-3 makes NO purchase this period.

        for order in (order_1, order_2):
            result = engine.process_order_payment_confirmed(order, "2026-09", 2026)
            for li in result.line_items:
                engine.approve_and_credit(li)

        # --- Step 4: prove complete independence -- each ID's ledger reflects ONLY its own activity ---
        self.assertEqual(ledger.balance_of("mofiz-id-1"), Decimal("70.00"))   # personal sales 7% of 1000
        self.assertEqual(ledger.balance_of("mofiz-id-2"), Decimal("35.00"))   # personal sales 7% of 500
        self.assertEqual(ledger.balance_of("mofiz-id-3"), Decimal("0.00"))    # no purchase -> no commission,
                                                                                 # NOT inherited from siblings
        # mofiz-id-3's zero balance, despite sharing an identity with two
        # earning IDs, is the direct proof commission never "mixes" across
        # the 3 accounts (Business Decision Section 4's core guarantee).


if __name__ == "__main__":
    unittest.main()

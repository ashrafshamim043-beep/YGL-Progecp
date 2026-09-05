"""
Real end-to-end integration test.

Flow under test:
    real genealogy tree (GenealogyEngine)
        -> TreeBackedUplineProvider (genealogy.py, reconstructed)
        -> CommissionEngine (engine.py, FROZEN baseline, unmodified)
        -> commission calculation (calculators.py, FROZEN)
        -> ledger (ledger.py, FROZEN)

This is the first test that plugs genealogy.py's real UplineProvider
implementation into the actual frozen CommissionEngine, instead of the
tree.py InMemoryUplineProvider test double used elsewhere. No frozen file
is modified — CommissionEngine only requires an UplineProvider (abstract
interface from tree.py, frozen), and TreeBackedUplineProvider already
implements that exact contract.
"""
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from commission_engine.approved_plan import build_approved_plan_v1
from commission_engine.models import (
    Order, Rank, AccountStatus, RankQualificationSnapshot,
)
from commission_engine.rank import InMemoryRankSnapshotProvider
from commission_engine.idempotency import IdempotencyStore
from commission_engine.undistributed import UndistributedFundTracker
from commission_engine.ledger import ImmutableLedger
from commission_engine.engine import CommissionEngine
from commission_engine.ceiling_validator import POOL_CEILING_PERCENT, ROUNDING_TOLERANCE

from commission_engine.genealogy import (
    GenealogyEngine,
    InMemoryMemberDirectory,
    MemberDirectoryEntry,
    MemberRole,
    TreeBackedUplineProvider,
)

NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


def build_real_genealogy_tree():
    """
    Builds a 4-generation real sponsor tree via GenealogyEngine:
        root -> sponsor_g3 -> sponsor_g2 -> sponsor_g1 -> buyer
    (buyer is the order's credited_member_id; sponsor_g1 = nearest/gen 1)
    """
    directory = InMemoryMemberDirectory()
    for member_id in ["root", "sponsor_g3", "sponsor_g2", "sponsor_g1", "buyer"]:
        directory.add(MemberDirectoryEntry(
            member_id=member_id, role=MemberRole.MEMBER, account_status=AccountStatus.ACTIVE,
        ))

    engine = GenealogyEngine(directory)
    engine.place_member("root", None, NOW)
    engine.place_member("sponsor_g3", "root", NOW)
    engine.place_member("sponsor_g2", "sponsor_g3", NOW)
    engine.place_member("sponsor_g1", "sponsor_g2", NOW)
    engine.place_member("buyer", "sponsor_g1", NOW)

    return engine, directory


class TestGenealogyBackedCommissionEngineIntegration(unittest.TestCase):
    def setUp(self):
        self.genealogy_engine, self.directory = build_real_genealogy_tree()
        self.upline_provider = TreeBackedUplineProvider(self.genealogy_engine, self.directory)
        self.plan = build_approved_plan_v1()
        self.rank_provider = InMemoryRankSnapshotProvider({})  # nobody rank-qualified in this scenario
        self.idempotency_store = IdempotencyStore()
        self.undistributed_tracker = UndistributedFundTracker()
        self.ledger = ImmutableLedger()
        self.engine = CommissionEngine(
            plan=self.plan,
            upline_provider=self.upline_provider,
            rank_provider=self.rank_provider,
            idempotency_store=self.idempotency_store,
            undistributed_tracker=self.undistributed_tracker,
            ledger=self.ledger,
        )

    # 1. genealogy থেকে upline সঠিকভাবে resolve হচ্ছে + 2. generation/depth সঠিক
    def test_genealogy_upline_resolves_correctly_with_right_generations(self):
        snapshot = self.upline_provider.get_upline_snapshot("buyer", NOW, max_depth=10)
        chain_ids = [(e.generation, e.member.member_id) for e in snapshot.chain]
        self.assertEqual(chain_ids, [
            (1, "sponsor_g1"),
            (2, "sponsor_g2"),
            (3, "sponsor_g3"),
            (4, "root"),
        ])

    # 3. CommissionEngine সেই provider গ্রহণ করছে + 4. commission calculation সঠিক
    def test_commission_engine_accepts_genealogy_provider_and_calculates(self):
        order = Order(
            order_id="ORDER-1",
            credited_member_id="buyer",
            sale_value=Decimal("10000.00"),
            is_commissionable=True,
            is_paid=True,
            order_timestamp=NOW,
        )
        result = self.engine.process_order_payment_confirmed(order, rank_period="2026-08", fiscal_year=2026)

        # Personal sales (7%) always paid to the buyer themselves.
        personal = [li for li in result.line_items if li.commission_type.value == "PERSONAL_SALES"]
        self.assertEqual(len(personal), 1)
        self.assertEqual(personal[0].payee_member_id, "buyer")
        self.assertEqual(personal[0].amount, Decimal("700.00"))  # 7% of 10000

        # Direct referral (7%) paid to nearest sponsor = sponsor_g1 (generation 1).
        direct = [li for li in result.line_items if li.commission_type.value == "DIRECT_REFERRAL"]
        self.assertEqual(len(direct), 1)
        self.assertEqual(direct[0].payee_member_id, "sponsor_g1")
        self.assertEqual(direct[0].amount, Decimal("700.00"))

        # Unilevel generation 1 also paid to sponsor_g1 (same person, different commission type).
        unilevel_gen1 = [li for li in result.line_items
                          if li.commission_type.value == "UNILEVEL" and li.generation == 1]
        self.assertEqual(len(unilevel_gen1), 1)
        self.assertEqual(unilevel_gen1[0].payee_member_id, "sponsor_g1")

        # Generations 2-4 resolve to sponsor_g2, sponsor_g3, root respectively.
        unilevel_by_gen = {li.generation: li.payee_member_id for li in result.line_items
                            if li.commission_type.value == "UNILEVEL"}
        self.assertEqual(unilevel_by_gen[2], "sponsor_g2")
        self.assertEqual(unilevel_by_gen[3], "sponsor_g3")
        self.assertEqual(unilevel_by_gen[4], "root")

        # Generations 5-10 have no upline member -> undistributed, not paid.
        for gen in range(5, 11):
            self.assertNotIn(gen, unilevel_by_gen)
        undistributed_gens = {u.generation for u in result.undistributed if u.commission_type.value == "UNILEVEL"}
        self.assertEqual(undistributed_gens, {5, 6, 7, 8, 9, 10})

    # 5. ledger entries সঠিক (via approve_and_credit, using the real genealogy-resolved payees)
    def test_ledger_entries_correct_after_approve_and_credit(self):
        order = Order(
            order_id="ORDER-2",
            credited_member_id="buyer",
            sale_value=Decimal("10000.00"),
            is_commissionable=True,
            is_paid=True,
            order_timestamp=NOW,
        )
        result = self.engine.process_order_payment_confirmed(order, rank_period="2026-08", fiscal_year=2026)
        for line_item in result.line_items:
            self.engine.approve_and_credit(line_item)

        sponsor_g1_balance = self.ledger.balance_of("sponsor_g1")
        # sponsor_g1 receives: Direct Referral (700.00) + Unilevel gen-1 (200.00 = 2% of 10000)
        self.assertEqual(sponsor_g1_balance, Decimal("900.00"))

        buyer_balance = self.ledger.balance_of("buyer")
        self.assertEqual(buyer_balance, Decimal("700.00"))  # personal sales only

    # 6. 35% ceiling ভাঙছে না
    def test_ceiling_not_breached_with_full_genealogy_chain(self):
        order = Order(
            order_id="ORDER-3",
            credited_member_id="buyer",
            sale_value=Decimal("10000.00"),
            is_commissionable=True,
            is_paid=True,
            order_timestamp=NOW,
        )
        result = self.engine.process_order_payment_confirmed(order, rank_period="2026-08", fiscal_year=2026)
        total_paid = sum((li.amount for li in result.line_items), Decimal("0"))
        max_allowed = (order.sale_value * POOL_CEILING_PERCENT / Decimal("100")) + ROUNDING_TOLERANCE
        self.assertLessEqual(total_paid, max_allowed)

    # 7. idempotency কাজ করছে (same order processed twice via the genealogy-backed engine)
    def test_idempotency_holds_with_genealogy_backed_engine(self):
        order = Order(
            order_id="ORDER-4",
            credited_member_id="buyer",
            sale_value=Decimal("5000.00"),
            is_commissionable=True,
            is_paid=True,
            order_timestamp=NOW,
        )
        first = self.engine.process_order_payment_confirmed(order, rank_period="2026-08", fiscal_year=2026)
        second = self.engine.process_order_payment_confirmed(order, rank_period="2026-08", fiscal_year=2026)

        self.assertFalse(first.was_duplicate)
        self.assertTrue(second.was_duplicate)
        self.assertEqual(len(first.line_items), len(second.line_items))

        for li in first.line_items:
            self.engine.approve_and_credit(li)
        # Ledger must reflect only ONE round of crediting, not two.
        sponsor_g1_balance = self.ledger.balance_of("sponsor_g1")
        self.assertEqual(sponsor_g1_balance, Decimal("450.00"))  # 350 direct + 100 unilevel-gen1, 5000 sale

    # 8. কোনো unexpected mutation হচ্ছে না (genealogy tree state stable across calls)
    def test_genealogy_tree_unmutated_by_commission_processing(self):
        chain_before = self.upline_provider.get_upline_snapshot("buyer", NOW, max_depth=10).chain
        order = Order(
            order_id="ORDER-5",
            credited_member_id="buyer",
            sale_value=Decimal("2000.00"),
            is_commissionable=True,
            is_paid=True,
            order_timestamp=NOW,
        )
        self.engine.process_order_payment_confirmed(order, rank_period="2026-08", fiscal_year=2026)
        chain_after = self.upline_provider.get_upline_snapshot("buyer", NOW, max_depth=10).chain

        ids_before = [(e.generation, e.member.member_id) for e in chain_before]
        ids_after = [(e.generation, e.member.member_id) for e in chain_after]
        self.assertEqual(ids_before, ids_after)

        # The genealogy engine's internal placement count must not change either.
        self.assertEqual(len(self.genealogy_engine._placements), 5)  # root, sponsor_g3/g2/g1, buyer


if __name__ == "__main__":
    unittest.main()

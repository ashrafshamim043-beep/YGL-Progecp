import unittest
from decimal import Decimal

from commission_engine.ledger import ImmutableLedger


class TestLedgerImmutability(unittest.TestCase):
    def test_no_update_or_delete_methods_exist(self):
        ledger = ImmutableLedger()
        self.assertFalse(hasattr(ledger, "update_entry"))
        self.assertFalse(hasattr(ledger, "delete_entry"))
        self.assertFalse(hasattr(ledger, "edit_entry"))
        self.assertFalse(hasattr(ledger, "remove_entry"))

    def test_all_entries_returns_a_copy_not_the_internal_list(self):
        ledger = ImmutableLedger()
        ledger.append_entry("member1", Decimal("10.00"), "COMMISSION", "ORDER-1", "test")
        entries = ledger.all_entries()
        entries.clear()  # mutate the returned copy
        # Internal state must be unaffected.
        self.assertEqual(len(ledger.all_entries()), 1)

    def test_correction_is_a_new_offsetting_entry_not_a_mutation(self):
        ledger = ImmutableLedger()
        original = ledger.append_entry("member1", Decimal("100.00"), "COMMISSION", "ORDER-1", "commission_credited")
        # Simulate a refund clawback: a NEW negative entry, original untouched.
        ledger.append_entry(
            "member1", Decimal("-100.00"), "REVERSAL", "ORDER-1",
            f"refund_clawback_of_{original.entry_id}"
        )
        entries = ledger.entries_for("member1")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].amount, Decimal("100.00"))  # original untouched
        self.assertEqual(entries[1].amount, Decimal("-100.00"))
        self.assertEqual(ledger.balance_of("member1"), Decimal("0.00"))

    def test_balance_is_always_computed_from_entries_never_stored(self):
        ledger = ImmutableLedger()
        ledger.append_entry("member1", Decimal("50.00"), "COMMISSION", "O1", "r1")
        ledger.append_entry("member1", Decimal("25.00"), "COMMISSION", "O2", "r2")
        ledger.append_entry("member1", Decimal("-10.00"), "REVERSAL", "O1", "r3")
        self.assertEqual(ledger.balance_of("member1"), Decimal("65.00"))


if __name__ == "__main__":
    unittest.main()
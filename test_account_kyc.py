"""
Account KYC test suite -- proves Initial/lightweight KYC (mobile + NID-or-
Birth-Reg, no face/liveness) correctly activates a Member for Product
Purchase eligibility, and correctly enforces the 3-Account Policy at the
point of KYC submission (not just at kyc_identity's own unit-test level --
this proves the FULL submit() -> activation pipeline).
"""
import unittest

from app.modules.account_kyc.core import (
    InMemoryAccountKYCRepository, MemberActivationPort, MobileNotVerifiedError, AccountKYCStatus,
)
from app.modules.account_kyc.service import AccountKYCService
from app.modules.kyc_identity.core import (
    DocumentType, InMemoryKYCIdentityRepository, MaxMemberIdsExceededError,
)
from app.modules.member.core import (
    MemberAccountStatus, InMemoryMemberRepository, InMemoryMemberStatusHistoryRepository,
)
from app.modules.member.service import MemberService


class RealMemberActivationAdapter(MemberActivationPort):
    """Uses the REAL MemberService (not a fake/mock) to actually flip
    PENDING -> ACTIVE, proving genuine cross-module integration -- not
    just a boolean the test controls."""

    def __init__(self, member_service: MemberService):
        self.member_service = member_service

    def activate_member(self, member_id: str) -> bool:
        member = self.member_service.member_repo.get_by_id(member_id)
        if member is None or member.account_status != MemberAccountStatus.PENDING:
            return False
        from dataclasses import replace
        updated = replace(member, account_status=MemberAccountStatus.ACTIVE)
        self.member_service.member_repo.save(updated)
        return True


def build_service():
    member_repo = InMemoryMemberRepository()
    status_repo = InMemoryMemberStatusHistoryRepository()
    member_service = MemberService(member_repo, status_repo)

    account_kyc_repo = InMemoryAccountKYCRepository()
    kyc_identity_repo = InMemoryKYCIdentityRepository()
    activation = RealMemberActivationAdapter(member_service)
    service = AccountKYCService(account_kyc_repo, kyc_identity_repo, activation)
    return service, member_service, account_kyc_repo, kyc_identity_repo


class TestNidInitialKyc(unittest.TestCase):
    def test_nid_submission_activates_member(self):
        service, member_service, account_kyc_repo, kyc_identity_repo = build_service()
        reg = member_service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        self.assertEqual(reg.member.account_status, MemberAccountStatus.PENDING)

        result = service.submit(
            member_id=reg.member.id, document_type=DocumentType.NID,
            raw_document_number="1234567890", document_image_reference="s3://kyc/nid-1.jpg",
            mobile_number="01700000000", mobile_otp_verified=True,
        )
        self.assertTrue(result.member_activated)
        self.assertEqual(result.record.status, AccountKYCStatus.INITIAL_KYC_VERIFIED)

        refreshed = member_service.get_profile(reg.member.id)
        self.assertEqual(refreshed.account_status, MemberAccountStatus.ACTIVE)  # -> purchase-eligible

    def test_status_terminology_never_claims_government_verification(self):
        """Direct test of the CONFIRMED terminology rule: the status value
        itself must be the Y.G.L-defined term, never something implying
        official government-database verification occurred."""
        service, member_service, account_kyc_repo, kyc_identity_repo = build_service()
        reg = member_service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        result = service.submit(
            member_id=reg.member.id, document_type=DocumentType.NID,
            raw_document_number="1234567890", document_image_reference="s3://kyc/nid-1.jpg",
            mobile_number="01700000000", mobile_otp_verified=True,
        )
        self.assertEqual(result.record.status, "INITIAL_KYC_VERIFIED")
        for forbidden_term in ("GOVERNMENT_VERIFIED", "NID_VERIFIED", "OFFICIAL", "APPROVED"):
            self.assertNotEqual(result.record.status, forbidden_term)


class TestBirthRegistrationInitialKyc(unittest.TestCase):
    def test_birth_registration_submission_activates_member(self):
        """Per Business Decision: a member WITHOUT an NID must never be
        blocked from purchasing -- Birth Registration is an equally valid path."""
        service, member_service, account_kyc_repo, kyc_identity_repo = build_service()
        reg = member_service.register("Fatema Begum", "fatema@ygl.example", "Password1!")

        result = service.submit(
            member_id=reg.member.id, document_type=DocumentType.BIRTH_REGISTRATION,
            raw_document_number="BR-2026-0001", document_image_reference="s3://kyc/br-1.jpg",
            mobile_number="01800000000", mobile_otp_verified=True,
        )
        self.assertTrue(result.member_activated)
        refreshed = member_service.get_profile(reg.member.id)
        self.assertEqual(refreshed.account_status, MemberAccountStatus.ACTIVE)


class TestMobileVerificationRequired(unittest.TestCase):
    def test_submission_without_mobile_verification_rejected(self):
        service, member_service, account_kyc_repo, kyc_identity_repo = build_service()
        reg = member_service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        with self.assertRaises(MobileNotVerifiedError):
            service.submit(
                member_id=reg.member.id, document_type=DocumentType.NID,
                raw_document_number="1234567890", document_image_reference="s3://kyc/nid-1.jpg",
                mobile_number="01700000000", mobile_otp_verified=False,
            )
        # Member must remain PENDING -- not activated on a rejected submission.
        refreshed = member_service.get_profile(reg.member.id)
        self.assertEqual(refreshed.account_status, MemberAccountStatus.PENDING)


class TestThreeAccountPolicyEnforcedAtSubmission(unittest.TestCase):
    def test_fourth_member_under_same_identity_rejected_and_not_activated(self):
        service, member_service, account_kyc_repo, kyc_identity_repo = build_service()
        same_nid = "9999999999"

        for i in range(1, 4):
            reg = member_service.register(f"Mofiz-{i}", f"mofiz{i}@ygl.example", "Password1!")
            result = service.submit(
                member_id=reg.member.id, document_type=DocumentType.NID,
                raw_document_number=same_nid, document_image_reference=f"s3://kyc/nid-{i}.jpg",
                mobile_number="01700000000", mobile_otp_verified=True,
            )
            self.assertTrue(result.member_activated, f"ID #{i} should be allowed and activated")

        fourth_reg = member_service.register("Mofiz-4", "mofiz4@ygl.example", "Password1!")
        with self.assertRaises(MaxMemberIdsExceededError):
            service.submit(
                member_id=fourth_reg.member.id, document_type=DocumentType.NID,
                raw_document_number=same_nid, document_image_reference="s3://kyc/nid-4.jpg",
                mobile_number="01700000000", mobile_otp_verified=True,
            )
        # The 4th member must remain PENDING -- never activated.
        refreshed = member_service.get_profile(fourth_reg.member.id)
        self.assertEqual(refreshed.account_status, MemberAccountStatus.PENDING)

    def test_three_ids_all_linked_to_same_kyc_identity(self):
        service, member_service, account_kyc_repo, kyc_identity_repo = build_service()
        same_nid = "8888888888"
        member_ids = []
        for i in range(1, 4):
            reg = member_service.register(f"Person-{i}", f"person{i}@ygl.example", "Password1!")
            service.submit(
                member_id=reg.member.id, document_type=DocumentType.NID,
                raw_document_number=same_nid, document_image_reference=f"s3://kyc/nid-{i}.jpg",
                mobile_number="01700000000", mobile_otp_verified=True,
            )
            member_ids.append(reg.member.id)

        identity = kyc_identity_repo.get_by_document(DocumentType.NID, same_nid)
        self.assertEqual(set(identity.linked_member_ids), set(member_ids))
        self.assertEqual(len(identity.linked_member_ids), 3)


if __name__ == "__main__":
    unittest.main()

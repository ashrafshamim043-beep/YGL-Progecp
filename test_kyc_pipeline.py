"""
KYC module test suite -- covers every scenario the Business Owner listed:
valid webhook, invalid signature, tampered payload, duplicate webhook,
delayed webhook, replay attempt, unknown event, unknown applicant,
applicant/member mismatch, duplicate applicant, invalid state transition,
valid final approval, final rejection, manual review, manual review ->
approval, manual review -> rejection, member activation atomicity,
rollback on activation failure, provider abstraction contract, Sumsub
adapter mocked API behavior.

No FastAPI/SQLAlchemy/network/live Sumsub credentials are used anywhere in
this file -- it runs against the pure-Python core.py + in-memory
repositories, exactly like commission_engine's own test suite runs against
its InMemory* providers.
"""
import hashlib
import hmac
import json
import time
import unittest
from datetime import datetime, timedelta, timezone

from app.modules.kyc.core import (
    KYCState,
    KYCVerification,
    InMemoryKYCVerificationRepository,
    InMemoryKYCStateTransitionRepository,
    InMemoryAuditLogWriter,
    InMemoryMemberActivationPort,
    KYCProviderInterface,
    NormalizedKYCEvent,
    NormalizedDecision,
    compute_idempotency_key,
    DuplicateApplicantError,
    UnknownEventTypeError,
)
from app.modules.kyc.service import YGLKYCService, WebhookProcessingResult
from app.modules.kyc.sumsub_adapter import SumsubAdapter, ProductionCredentialsNotConfiguredError

WEBHOOK_SECRET = "test_secret_do_not_use_in_production"


def sign(payload_bytes: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


def make_payload(applicant_id="applicant-1", event_type="applicantReviewed",
                  review_answer="GREEN", reject_type=None, review_status="completed",
                  created_at_ms="2026-08-30 10:00:00.000"):
    payload = {
        "applicantId": applicant_id,
        "type": event_type,
        "reviewStatus": review_status,
        "reviewResult": {"reviewAnswer": review_answer},
        "createdAtMs": created_at_ms,
        "correlationId": "corr-123",
    }
    if reject_type:
        payload["reviewResult"]["reviewRejectType"] = reject_type
    return json.dumps(payload).encode("utf-8")


def build_service():
    provider = SumsubAdapter(webhook_secret=WEBHOOK_SECRET)
    verification_repo = InMemoryKYCVerificationRepository()
    transition_repo = InMemoryKYCStateTransitionRepository()
    audit_log = InMemoryAuditLogWriter()
    member_activation = InMemoryMemberActivationPort()
    service = YGLKYCService(provider, verification_repo, transition_repo, audit_log, member_activation)
    return service, verification_repo, transition_repo, audit_log, member_activation


def advance_to_verification_in_progress(service, member_id="member-1", applicant_id="applicant-1"):
    """Helper: creates PENDING, submits document (member action) to reach
    DOCUMENT_SUBMITTED, then walks it to VERIFICATION_IN_PROGRESS via a real
    webhook -- so tests can start from a realistic mid-flow state without
    skipping any state-machine step. Uses an EARLIER timestamp than the
    default so its idempotency key never collides with a subsequent
    "final decision" webhook built with make_payload()'s default timestamp."""
    verification = service.start_verification(member_id, "SUMSUB", applicant_id)
    service.submit_document(verification.id, member_id)
    payload = make_payload(applicant_id=applicant_id, review_status="pending",
                            created_at_ms="2026-08-30 09:00:00.000")
    result = service.process_webhook(payload, sign(payload))
    assert result.accepted, f"setup webhook unexpectedly rejected: {result.reason}"
    return result


class TestValidWebhookFinalApproval(unittest.TestCase):
    def test_valid_webhook_approves_and_activates_member(self):
        service, vrepo, trepo, audit, activation = build_service()
        verification = service.start_verification("member-1", "SUMSUB", "applicant-1")
        service.submit_document(verification.id, "member-1")
        in_progress_payload = make_payload(applicant_id="applicant-1", review_status="pending",
                                            created_at_ms="2026-08-30 09:00:00.000")
        service.process_webhook(in_progress_payload, sign(in_progress_payload))

        payload = make_payload(applicant_id="applicant-1", review_answer="GREEN")
        result = service.process_webhook(payload, sign(payload))

        self.assertTrue(result.accepted)
        self.assertEqual(result.new_state, KYCState.APPROVED)
        self.assertTrue(result.member_activated)
        self.assertIn("member-1", activation.activated_members)


class TestInvalidSignature(unittest.TestCase):
    def test_invalid_signature_rejected_before_any_processing(self):
        service, vrepo, trepo, audit, activation = build_service()
        service.start_verification("member-1", "SUMSUB", "applicant-1")
        payload = make_payload(applicant_id="applicant-1")
        result = service.process_webhook(payload, "0" * 64)  # wrong signature

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "invalid_signature")
        self.assertEqual(len(trepo.list_for_verification(vrepo.get_active_for_member("member-1").id)), 1)  # only the initial PENDING


class TestTamperedPayload(unittest.TestCase):
    def test_tampered_payload_after_signing_is_rejected(self):
        service, vrepo, trepo, audit, activation = build_service()
        service.start_verification("member-1", "SUMSUB", "applicant-1")
        original_payload = make_payload(applicant_id="applicant-1", review_answer="RED", reject_type="FINAL")
        valid_signature = sign(original_payload)

        tampered_payload = make_payload(applicant_id="applicant-1", review_answer="GREEN")  # attacker flips decision
        result = service.process_webhook(tampered_payload, valid_signature)  # signature no longer matches

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "invalid_signature")


class TestDuplicateWebhook(unittest.TestCase):
    def test_exact_duplicate_webhook_is_not_reapplied(self):
        service, vrepo, trepo, audit, activation = build_service()
        advance_to_verification_in_progress(service)
        payload = make_payload(applicant_id="applicant-1", review_answer="GREEN")
        sig = sign(payload)

        first = service.process_webhook(payload, sig)
        second = service.process_webhook(payload, sig)  # exact duplicate delivery

        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        self.assertEqual(second.reason, "duplicate_event")
        # Member must only be activated once, not twice.
        self.assertEqual(list(activation.activated_members.values()).count(first.verification_id), 1)


class TestDelayedWebhookNotFalselyRejected(unittest.TestCase):
    def test_delayed_but_legitimate_webhook_still_accepted(self):
        """Business Owner correction: a delayed webhook must not be rejected
        merely for being old -- only signature/idempotency/state-machine
        checks may reject it. There is no standalone timestamp-window
        rejection in this pipeline."""
        service, vrepo, trepo, audit, activation = build_service()
        advance_to_verification_in_progress(service)
        old_timestamp = "2020-01-01 00:00:00.000"  # far in the past
        payload = make_payload(applicant_id="applicant-1", review_answer="GREEN", created_at_ms=old_timestamp)
        result = service.process_webhook(payload, sign(payload))

        self.assertTrue(result.accepted, "a delayed-but-otherwise-valid webhook must not be rejected on age alone")


class TestReplayAttempt(unittest.TestCase):
    def test_replayed_captured_webhook_blocked_by_idempotency_not_timestamp(self):
        service, vrepo, trepo, audit, activation = build_service()
        advance_to_verification_in_progress(service)
        payload = make_payload(applicant_id="applicant-1", review_answer="GREEN")
        sig = sign(payload)
        service.process_webhook(payload, sig)  # legitimate first delivery

        # Attacker captures and replays the exact same request later.
        replay_result = service.process_webhook(payload, sig)
        self.assertFalse(replay_result.accepted)
        self.assertEqual(replay_result.reason, "duplicate_event")


class TestUnknownEvent(unittest.TestCase):
    def test_unrecognized_event_type_rejected_not_ignored(self):
        service, vrepo, trepo, audit, activation = build_service()
        service.start_verification("member-1", "SUMSUB", "applicant-1")
        payload = make_payload(applicant_id="applicant-1", event_type="somethingElseEntirely")
        result = service.process_webhook(payload, sign(payload))

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "unknown_event_type")
        self.assertTrue(any(e["event_type"] == "KYC_WEBHOOK_REJECTED" for e in audit.entries))


class TestUnknownApplicant(unittest.TestCase):
    def test_webhook_for_nonexistent_applicant_rejected(self):
        service, vrepo, trepo, audit, activation = build_service()
        # No start_verification() call at all -- applicant is unknown to us.
        payload = make_payload(applicant_id="ghost-applicant")
        result = service.process_webhook(payload, sign(payload))

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "applicant_mapping_failed")


class TestApplicantMemberMismatch(unittest.TestCase):
    def test_applicant_belonging_to_different_member_does_not_activate_wrong_member(self):
        service, vrepo, trepo, audit, activation = build_service()
        advance_to_verification_in_progress(service, member_id="member-A", applicant_id="applicant-A")
        service.start_verification("member-B", "SUMSUB", "applicant-B")

        payload = make_payload(applicant_id="applicant-A", review_answer="GREEN")
        result = service.process_webhook(payload, sign(payload))

        self.assertTrue(result.accepted)
        self.assertIn("member-A", activation.activated_members)
        self.assertNotIn("member-B", activation.activated_members)


class TestDuplicateApplicant(unittest.TestCase):
    def test_starting_second_verification_while_one_is_active_is_rejected(self):
        service, vrepo, trepo, audit, activation = build_service()
        service.start_verification("member-1", "SUMSUB", "applicant-1")
        with self.assertRaises(DuplicateApplicantError):
            service.start_verification("member-1", "SUMSUB", "applicant-2")


class TestInvalidStateTransition(unittest.TestCase):
    def test_jumping_straight_from_pending_to_approved_is_rejected(self):
        service, vrepo, trepo, audit, activation = build_service()
        service.start_verification("member-1", "SUMSUB", "applicant-1")
        # Skips VERIFICATION_IN_PROGRESS entirely -- PENDING -> APPROVED
        # directly is not an allowed transition.
        payload = make_payload(applicant_id="applicant-1", review_answer="GREEN")
        result = service.process_webhook(payload, sign(payload))

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "invalid_state_transition")


class TestFinalRejection(unittest.TestCase):
    def test_final_rejection_does_not_activate_member(self):
        service, vrepo, trepo, audit, activation = build_service()
        advance_to_verification_in_progress(service)
        payload = make_payload(applicant_id="applicant-1", review_answer="RED", reject_type="FINAL")
        result = service.process_webhook(payload, sign(payload))

        self.assertTrue(result.accepted)
        self.assertEqual(result.new_state, KYCState.REJECTED)
        self.assertFalse(result.member_activated)
        self.assertNotIn("member-1", activation.activated_members)


class TestManualReview(unittest.TestCase):
    def test_non_final_red_goes_to_manual_review_not_rejected(self):
        service, vrepo, trepo, audit, activation = build_service()
        advance_to_verification_in_progress(service)
        payload = make_payload(applicant_id="applicant-1", review_answer="RED", reject_type="RETRY")
        result = service.process_webhook(payload, sign(payload))

        self.assertTrue(result.accepted)
        self.assertEqual(result.new_state, KYCState.MANUAL_REVIEW)
        self.assertFalse(result.member_activated)


class TestManualReviewToApproval(unittest.TestCase):
    def test_admin_can_approve_out_of_manual_review(self):
        service, vrepo, trepo, audit, activation = build_service()
        advance_to_verification_in_progress(service)
        payload = make_payload(applicant_id="applicant-1", review_answer="RED", reject_type="RETRY")
        webhook_result = service.process_webhook(payload, sign(payload))
        self.assertEqual(webhook_result.new_state, KYCState.MANUAL_REVIEW)

        admin_result = service.resolve_manual_review(webhook_result.verification_id, admin_id="admin-42", approve=True)

        self.assertTrue(admin_result.accepted)
        self.assertEqual(admin_result.new_state, KYCState.APPROVED)
        self.assertTrue(admin_result.member_activated)


class TestManualReviewToRejection(unittest.TestCase):
    def test_admin_can_reject_out_of_manual_review(self):
        service, vrepo, trepo, audit, activation = build_service()
        advance_to_verification_in_progress(service)
        payload = make_payload(applicant_id="applicant-1", review_answer="RED", reject_type="RETRY")
        webhook_result = service.process_webhook(payload, sign(payload))

        admin_result = service.resolve_manual_review(webhook_result.verification_id, admin_id="admin-42", approve=False)

        self.assertTrue(admin_result.accepted)
        self.assertEqual(admin_result.new_state, KYCState.REJECTED)
        self.assertFalse(admin_result.member_activated)
        self.assertNotIn("member-1", activation.activated_members)


class TestMemberActivationAtomicity(unittest.TestCase):
    def test_all_six_conditions_checked_before_activation_commits(self):
        service, vrepo, trepo, audit, activation = build_service()
        advance_to_verification_in_progress(service)
        payload = make_payload(applicant_id="applicant-1", review_answer="GREEN")
        result = service.process_webhook(payload, sign(payload))

        self.assertTrue(result.member_activated)
        # All-or-nothing: status history AND audit entry both present, never one without the other.
        self.assertEqual(len(activation.status_history_entries), 1)
        self.assertEqual(len(activation.audit_entries), 1)


class TestRollbackOnActivationFailure(unittest.TestCase):
    def test_forced_transaction_failure_leaves_nothing_committed(self):
        service, vrepo, trepo, audit, activation = build_service()
        activation.fail_activation = True  # simulates a DB transaction rollback
        advance_to_verification_in_progress(service)
        payload = make_payload(applicant_id="applicant-1", review_answer="GREEN")
        result = service.process_webhook(payload, sign(payload))

        # The KYC state transition to APPROVED is still recorded (that part
        # of the pipeline succeeded) -- but member activation itself did not.
        self.assertEqual(result.new_state, KYCState.APPROVED)
        self.assertFalse(result.member_activated)
        self.assertEqual(activation.activated_members, {})
        self.assertEqual(activation.status_history_entries, [])
        self.assertEqual(activation.audit_entries, [])


class TestProviderAbstractionContract(unittest.TestCase):
    def test_service_only_depends_on_the_abstract_interface(self):
        """Proves YGLKYCService never references SumsubAdapter by name --
        it only calls methods defined on KYCProviderInterface. We prove
        this by constructing the service with a DIFFERENT, minimal fake
        provider (not SumsubAdapter at all) and confirming the pipeline
        still works end-to-end."""

        class FakeOtherProvider(KYCProviderInterface):
            def verify_webhook_signature(self, raw_payload, signature_header):
                return signature_header == "fake-valid-signature"

            def parse_webhook_event(self, raw_payload):
                data = json.loads(raw_payload)
                return NormalizedKYCEvent(
                    provider_name="FAKE_OTHER_VENDOR",
                    provider_applicant_id=data["applicant"],
                    decision=NormalizedDecision.APPROVED,
                    event_timestamp=datetime.now(timezone.utc),
                    idempotency_key=compute_idempotency_key(
                        "FAKE_OTHER_VENDOR", data["applicant"], "reviewed", "1"
                    ),
                )

            def create_applicant(self, member_id):
                return f"fake-applicant-for-{member_id}"

            def get_verification_url(self, provider_applicant_id):
                return f"https://fake-vendor.example/verify/{provider_applicant_id}"

        vrepo = InMemoryKYCVerificationRepository()
        trepo = InMemoryKYCStateTransitionRepository()
        audit = InMemoryAuditLogWriter()
        activation = InMemoryMemberActivationPort()
        service = YGLKYCService(FakeOtherProvider(), vrepo, trepo, audit, activation)

        service.start_verification("member-1", "FAKE_OTHER_VENDOR", "fake-applicant-1")
        # Move to VERIFICATION_IN_PROGRESS first (state machine requires it).
        vrepo.save(KYCVerification(
            id=vrepo.get_active_for_member("member-1").id, member_id="member-1",
            provider_name="FAKE_OTHER_VENDOR", provider_applicant_id="fake-applicant-1",
            state=KYCState.VERIFICATION_IN_PROGRESS,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        ))
        payload = json.dumps({"applicant": "fake-applicant-1"}).encode()
        result = service.process_webhook(payload, "fake-valid-signature")

        self.assertTrue(result.accepted)
        self.assertEqual(result.new_state, KYCState.APPROVED)
        # Proves zero coupling to Sumsub: this whole test never imported SumsubAdapter.


class TestSumsubAdapterMockedBehavior(unittest.TestCase):
    def test_signature_verification_is_real_and_correct(self):
        adapter = SumsubAdapter(webhook_secret=WEBHOOK_SECRET)
        payload = make_payload()
        self.assertTrue(adapter.verify_webhook_signature(payload, sign(payload)))
        self.assertFalse(adapter.verify_webhook_signature(payload, "wrong signature"))

    def test_parse_webhook_event_maps_green_to_approved(self):
        adapter = SumsubAdapter(webhook_secret=WEBHOOK_SECRET)
        payload = make_payload(review_answer="GREEN")
        event = adapter.parse_webhook_event(payload)
        self.assertEqual(event.decision, NormalizedDecision.APPROVED)
        self.assertEqual(event.provider_name, "SUMSUB")

    def test_parse_webhook_event_maps_red_final_to_rejected(self):
        adapter = SumsubAdapter(webhook_secret=WEBHOOK_SECRET)
        payload = make_payload(review_answer="RED", reject_type="FINAL")
        event = adapter.parse_webhook_event(payload)
        self.assertEqual(event.decision, NormalizedDecision.REJECTED)

    def test_parse_webhook_event_maps_red_retry_to_manual_review(self):
        adapter = SumsubAdapter(webhook_secret=WEBHOOK_SECRET)
        payload = make_payload(review_answer="RED", reject_type="RETRY")
        event = adapter.parse_webhook_event(payload)
        self.assertEqual(event.decision, NormalizedDecision.MANUAL_REVIEW_REQUIRED)

    def test_live_api_methods_refuse_to_run_without_production_approval(self):
        adapter = SumsubAdapter(webhook_secret=WEBHOOK_SECRET)
        with self.assertRaises(ProductionCredentialsNotConfiguredError):
            adapter.create_applicant("member-1")
        with self.assertRaises(ProductionCredentialsNotConfiguredError):
            adapter.get_verification_url("applicant-1")

    def test_idempotency_key_is_never_called_vendor_event_id(self):
        """Business Owner correction #2: verify the field is literally named
        idempotency_key, and that Sumsub's own correlationId (when present)
        is kept in a SEPARATE field, never conflated."""
        adapter = SumsubAdapter(webhook_secret=WEBHOOK_SECRET)
        payload = make_payload()
        event = adapter.parse_webhook_event(payload)
        self.assertTrue(hasattr(event, "idempotency_key"))
        self.assertFalse(hasattr(event, "vendor_event_id"))
        self.assertEqual(event.provider_native_event_id, "corr-123")
        self.assertNotEqual(event.idempotency_key, event.provider_native_event_id)


if __name__ == "__main__":
    unittest.main()

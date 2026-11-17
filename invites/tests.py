"""Batch sending: one bad line must not stop everyone else from getting their test."""

from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from assessments.models import Test
from users.models import User

from .models import Invite


class BatchInviteTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user("hr@example.com", "pw-12345678"))
        self.test = Test.objects.create(title="Business development test", duration_mins=30)

    @mock.patch("invites.views.run_in_background")
    def test_bad_lines_are_skipped_not_fatal(self, _bg):
        r = self.client.post("/api/v1/invites/batch/", {
            "test_id": self.test.id,
            "opens_at": "2026-11-09T09:00:00Z",
            "expires_at": "2026-11-09T11:00:00Z",
            "candidates": [
                {"name": "Tijani Yetunde", "email": "zainabyetunde89@gmail.com"},
                {"name": "Balogun Ozaveshe", "email": "balogunozaveshe53sj9_pwo@indeedemail.com"},
                {"name": "Okoduwa Liberty", "email": "libertyokoduwa1@gmail.com."},  # trailing dot
                {"name": "Broken", "email": "broken@gmail"},
                {"name": "Tijani Yetunde", "email": "ZAINABYETUNDE89@gmail.com"},  # duplicate
            ],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()["data"]
        self.assertEqual(body["created"], 3)
        self.assertEqual([s["line"] for s in body["skipped"]], ["Broken, broken@gmail"])
        self.assertEqual(Invite.objects.count(), 3)

    @mock.patch("invites.views.run_in_background")
    def test_all_bad_gives_a_readable_error(self, _bg):
        r = self.client.post("/api/v1/invites/batch/", {
            "test_id": self.test.id, "candidates": [{"name": "X", "email": "nope"}],
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("not a valid email", r.json()["error"])


class EmailReliabilityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user("hr@example.com", "pw-12345678"))
        self.test = Test.objects.create(title="BDE", duration_mins=40)

    @mock.patch("config.emails.RETRY_WAITS", (0, 0))
    def test_flaky_mail_server_succeeds_on_retry(self):
        from config.emails import send_branded
        calls = {"n": 0}

        def flaky(self, fail_silently=False):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("421 too many connections")
            return 1
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", flaky):
            self.assertTrue(send_branded("a@example.com", "Hi", heading="Hi", paragraphs=["x"]))
        self.assertEqual(calls["n"], 3)

    @mock.patch("config.emails.RETRY_WAITS", (0, 0))
    def test_failed_email_flags_invite_but_link_stays_valid(self):
        from datetime import timedelta
        from django.utils import timezone
        from .emails import send_invite_emails
        inv = Invite.objects.create(test=self.test, candidate_name="Grace", candidate_email="g@example.com",
                                    expires_at=timezone.now() + timedelta(days=2))
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=ConnectionError("down")):
            send_invite_emails([inv.id])
        inv.refresh_from_db()
        self.assertEqual(inv.status, "pending")          # not "expired"
        self.assertIsNotNone(inv.email_failed_at)
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", return_value=1):
            send_invite_emails([inv.id])
        inv.refresh_from_db()
        self.assertIsNone(inv.email_failed_at)

    @mock.patch("invites.views.run_in_background")
    def test_resend_revives_invite_expired_by_failed_email(self, bg):
        from datetime import timedelta
        from django.utils import timezone
        inv = Invite.objects.create(test=self.test, candidate_name="Oladayo", candidate_email="o@example.com",
                                    status="expired", expires_at=timezone.now() + timedelta(days=2))
        r = self.client.post(f"/api/v1/invites/{inv.id}/resend/")
        self.assertEqual(r.status_code, 200, r.content)
        inv.refresh_from_db()
        self.assertEqual(inv.status, "pending")
        self.assertTrue(bg.called)
        # its link works again
        self.assertNotEqual(self.client.get(f"/api/v1/invites/validate/{inv.token}/").json().get("code"), "expired")

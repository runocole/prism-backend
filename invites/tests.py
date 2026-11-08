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

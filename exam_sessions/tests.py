"""
Tests for: violations carrying their place in the recording, recording parts
(a page refresh never overwrites earlier video), and one-click attendance
confirmation. Files go to a temporary folder, never the real media folders.
"""

import shutil
import tempfile

from django.core import mail, signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from assessments.models import Test
from invites.models import Invite

from .emails import send_passed
from .models import Session, Violation
from .serializers import SessionSerializer
from .storage import recording_parts, part_start_seconds

TMP = tempfile.mkdtemp(prefix="pas-tests-")


@override_settings(
    MEDIA_ROOT=TMP + "/media", PRIVATE_MEDIA_ROOT=TMP + "/private", STORAGE_ENDPOINT_URL="",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class Round9Tests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        test = Test.objects.create(title="Field test", duration_mins=30)
        self.invite = Invite.objects.create(test=test, candidate_name="Ada Obi", candidate_email="ada@example.com")
        self.session = Session.objects.create(invite=self.invite)
        self.token = str(self.invite.token)

    def upload(self, index, part=None):
        data = {"chunk": SimpleUploadedFile("c.webm", b"x" * 10, "video/webm"), "chunk_index": index, "token": self.token}
        if part:
            data["part"] = part
        return self.client.post(f"/api/v1/sessions/{self.session.id}/recording/", data,
                                HTTP_X_INVITE_TOKEN=self.token)

    def test_refresh_does_not_overwrite_and_violation_time_is_in_video(self):
        p1, p2 = 1_762_500_000_000, 1_762_500_100_000
        for i in range(3):
            self.assertLess(self.upload(i, p1).status_code, 300)
        for i in range(2):  # page refreshed: indexes restart at 0, new part
            self.assertLess(self.upload(i, p2).status_code, 300)
        parts = recording_parts(self.session.id)
        self.assertEqual([(p, len(k)) for p, k in parts], [(p1, 3), (p2, 2)])
        self.assertEqual(part_start_seconds(self.session.id), {p1: 0.0, p2: 90.0})

        r = self.client.post(f"/api/v1/sessions/{self.session.id}/violations/",
                             {"token": self.token, "type": "looking_away", "part": p2, "video_offset": 12.5},
                             content_type="application/json", HTTP_X_INVITE_TOKEN=self.token)
        self.assertLess(r.status_code, 300, r.content)
        v = Violation.objects.get(session=self.session)
        self.assertEqual((v.recording_part, v.video_offset_seconds), (p2, 12.5))
        data = SessionSerializer(self.session).data
        self.assertEqual(data["violations"][0]["video_seconds"], 102.5)

    def test_old_violation_without_position(self):
        Violation.objects.create(session=self.session, type="tab_switch")
        data = SessionSerializer(self.session).data
        self.assertIsNone(data["violations"][0]["video_seconds"])

    def test_confirm_attendance_once_and_hr_emailed(self):
        self.assertTrue(send_passed(self.session, "Monday, 10 Nov", "10:00 AM", "OTIC office", ""))
        self.assertIn("/confirm/", mail.outbox[0].alternatives[0][0])
        self.assertNotIn("mailto:", mail.outbox[0].alternatives[0][0].split("Confirm your attendance")[0][-400:])
        token = signing.TimestampSigner(salt="attendance").sign(str(self.session.pk))

        r = self.client.post("/api/v1/sessions/confirm-attendance/", {"token": token}, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()["data"]["already"])
        self.assertEqual(r.json()["data"]["date"], "Monday, 10 Nov")
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("Ada Obi", mail.outbox[1].subject)

        r = self.client.post("/api/v1/sessions/confirm-attendance/", {"token": token}, content_type="application/json")
        self.assertTrue(r.json()["data"]["already"])
        self.assertEqual(len(mail.outbox), 2)  # HR isn't emailed twice
        self.session.refresh_from_db()
        self.assertIsNotNone(self.session.attendance_confirmed_at)

    def test_confirm_attendance_bad_token(self):
        r = self.client.post("/api/v1/sessions/confirm-attendance/", {"token": "1:forged"}, content_type="application/json")
        self.assertEqual(r.status_code, 404)
        r = self.client.get("/api/v1/sessions/confirm-attendance/")
        self.assertEqual(r.status_code, 405)  # GET (link scanners) never confirms

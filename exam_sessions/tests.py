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


@override_settings(MEDIA_ROOT=TMP + "/media", PRIVATE_MEDIA_ROOT=TMP + "/private", STORAGE_ENDPOINT_URL="")
class AddTimeTests(TestCase):
    """Network dropped: HR adds time, or reopens a test that ran out."""

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from rest_framework.test import APIClient
        from users.models import User
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user("hr@example.com", "pw-12345678"))
        test = Test.objects.create(title="BD test", duration_mins=30)
        self.invite = Invite.objects.create(test=test, candidate_name="Zakari Haruna", candidate_email="z@example.com")
        # started 29 min 48 s ago: 12 seconds left, like the Live screenshot
        self.session = Session.objects.create(
            invite=self.invite, status=Session.Status.IN_PROGRESS,
            started_at=timezone.now() - timedelta(minutes=29, seconds=48))

    def add(self, minutes):
        return self.client.post(f"/api/v1/sessions/{self.session.id}/add-time/", {"minutes": minutes}, format="json")

    def test_running_test_gets_more_time(self):
        r = self.add(10)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertAlmostEqual(r.json()["data"]["remaining_seconds"], 612, delta=3)
        self.assertFalse(r.json()["data"]["reopened"])

    def test_timed_out_test_reopens_with_exact_minutes(self):
        from datetime import timedelta
        from django.utils import timezone
        Session.objects.filter(pk=self.session.pk).update(
            status=Session.Status.SUBMITTED, submitted_at=timezone.now(),
            started_at=timezone.now() - timedelta(minutes=70))
        Invite.objects.filter(pk=self.invite.pk).update(status="submitted")
        r = self.add(15)
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()["data"]
        self.assertTrue(d["reopened"])
        self.assertEqual(d["status"], "in_progress")
        self.assertAlmostEqual(d["remaining_seconds"], 900, delta=3)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.status, "active")
        # the candidate's link works again and resumes the same session
        r = self.client.get(f"/api/v1/invites/validate/{self.invite.token}/")
        self.assertEqual(r.status_code, 200, r.content)

    def test_decided_result_cannot_reopen(self):
        Session.objects.filter(pk=self.session.pk).update(status=Session.Status.SUBMITTED, decision="fail")
        self.assertEqual(self.add(10).status_code, 400)


class RescoreTests(TestCase):
    """Changing an MCQ's correct option re-marks everyone who already answered it."""

    def test_answer_key_change_remarks_answers_and_scores(self):
        from django.core.management import call_command
        from rest_framework.test import APIClient
        from assessments.models import Question
        from users.models import User
        from .models import Answer
        from .services import submit_session

        user = User.objects.create_user("hr@example.com", "pw-12345678")
        client = APIClient()
        client.force_authenticate(user)
        test = Test.objects.create(title="BD test", duration_mins=30, created_by=user)
        q = Question.objects.create(test=test, type="mcq", body="2+2?", points=1,
                                    payload={"options": ["3", "4", "5"], "correct_index": 0})
        invite = Invite.objects.create(test=test, candidate_name="A", candidate_email="a@example.com")
        session = Session.objects.create(invite=invite, status=Session.Status.IN_PROGRESS)
        Answer.objects.create(session=session, question=q, response={"selected_index": 1})
        submit_session(session)
        a = Answer.objects.get(session=session)
        self.assertFalse(a.is_correct)
        Session.objects.filter(pk=session.pk).update(score_pct=0, decision="fail")

        r = client.patch(f"/api/v1/assessments/{test.id}/questions/{q.id}/",
                         {"payload": {"options": ["3", "4", "5"], "correct_index": 1}}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        a.refresh_from_db()
        self.assertTrue(a.is_correct)
        self.assertEqual(a.auto_score, 1)
        session.refresh_from_db()
        self.assertEqual(session.score_pct, 100)

        # the one-off command for keys edited before this existed
        Answer.objects.filter(pk=a.pk).update(is_correct=False, auto_score=0)
        call_command("rescore_mcq", stdout=__import__("io").StringIO())
        a.refresh_from_db()
        self.assertTrue(a.is_correct)

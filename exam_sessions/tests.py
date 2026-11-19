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


@override_settings(MEDIA_ROOT=TMP + "/media", PRIVATE_MEDIA_ROOT=TMP + "/private", STORAGE_ENDPOINT_URL="")
class ExpiryTests(TestCase):
    """Link expiry (e.g. 12:00) stops new people, never someone already inside."""

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        test = Test.objects.create(title="BD test", duration_mins=60)
        past = timezone.now() - timedelta(minutes=1)  # "Expires 12:00" and it's now 12:01
        self.inside = Invite.objects.create(test=test, candidate_name="Afo Martina", candidate_email="afo@example.com",
                                            expires_at=past, status="active")
        self.session = Session.objects.create(invite=self.inside, status=Session.Status.IN_PROGRESS,
                                              started_at=timezone.now() - timedelta(minutes=20))
        self.newcomer = Invite.objects.create(test=test, candidate_name="Late", candidate_email="late@example.com",
                                              expires_at=past)

    def beat(self, invite, sid):
        return self.client.post(f"/api/v1/sessions/{sid}/heartbeat/", {"answered": 3},
                                HTTP_X_INVITE_TOKEN=str(invite.token))

    def test_candidate_inside_keeps_going_after_expiry(self):
        r = self.beat(self.inside, self.session.id)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.client.get(f"/api/v1/invites/validate/{self.inside.token}/").status_code, 200)
        # even if an older build already flipped the invite to expired
        Invite.objects.filter(pk=self.inside.pk).update(status="expired")
        self.assertEqual(self.beat(self.inside, self.session.id).status_code, 200)
        self.inside.refresh_from_db()
        self.assertEqual(self.inside.status, "active")

    def test_new_person_blocked_after_expiry(self):
        r = self.client.get(f"/api/v1/invites/validate/{self.newcomer.token}/")
        self.assertEqual(r.status_code, 410)
        self.assertEqual(r.json()["code"], "expired")

    def test_out_of_time_test_shows_in_results(self):
        from datetime import timedelta
        from django.utils import timezone
        from rest_framework.test import APIClient
        from users.models import User
        Session.objects.filter(pk=self.session.pk).update(started_at=timezone.now() - timedelta(minutes=90))
        client = APIClient()
        client.force_authenticate(User.objects.create_user("hr@example.com", "pw-12345678"))
        r = client.get("/api/v1/sessions/")
        self.assertEqual(r.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "submitted")


class SectionsAndMarkingTests(TestCase):
    """Sections, list + right/wrong questions, tick/cross marking, hidden answers."""

    def setUp(self):
        from rest_framework.test import APIClient
        from assessments.models import Question
        from users.models import User
        from .models import Answer
        user = User.objects.create_user("hr@example.com", "pw-12345678")
        self.hr = APIClient()
        self.hr.force_authenticate(user)
        self.test = Test.objects.create(title="BDE", duration_mins=30, sections=[
            {"key": "A", "title": "Section A", "description": "General", "quantitative": False},
            {"key": "B", "title": "Section B", "description": "Maths", "quantitative": True},
        ])
        # created out of order on purpose: section A must come first
        self.rw = Question.objects.create(test=self.test, type="right_wrong", section="B", body="25 x 25 =", points=2,
                                          payload={"answer": "625", "answer_type": "number"}, order_index=0)
        self.lst = Question.objects.create(test=self.test, type="list", section="A", body="List 3 traits", points=3,
                                           payload={"count": 3}, order_index=1)
        self.invite = Invite.objects.create(test=self.test, candidate_name="Ada", candidate_email="ada@example.com")
        self.session = Session.objects.create(invite=self.invite, status=Session.Status.IN_PROGRESS)
        self.a_rw = Answer.objects.create(session=self.session, question=self.rw, response={"text": "625"})
        self.a_list = Answer.objects.create(session=self.session, question=self.lst, response={"items": ["a", "b", "c"]})

    def test_candidate_gets_sections_in_order_without_answers(self):
        r = self.client.get(f"/api/v1/sessions/{self.session.id}/questions/", HTTP_X_INVITE_TOKEN=str(self.invite.token))
        self.assertEqual(r.status_code, 200, r.content)
        qs = r.json()["data"]
        self.assertEqual([q["section"] for q in qs], ["A", "B"])
        self.assertNotIn("answer", qs[1]["payload"])  # the 625 never reaches the candidate

    def test_tick_cross_marking(self):
        url = f"/api/v1/sessions/{self.session.id}/answers/"
        r = self.hr.post(f"{url}{self.a_rw.id}/score/", {"is_correct": True}, format="json")
        self.assertEqual(r.json()["data"]["manual_score"], 2.0)
        r = self.hr.post(f"{url}{self.a_list.id}/score/", {"item_marks": [True, False, True]}, format="json")
        self.assertEqual(r.json()["data"]["manual_score"], 2.0)  # 2 of 3 rows x 3 points
        self.assertEqual(r.json()["data"]["item_marks"], [True, False, True])
        # clearing a right/wrong mark removes its score
        r = self.hr.post(f"{url}{self.a_rw.id}/score/", {"is_correct": None}, format="json")
        self.assertIsNone(r.json()["data"]["manual_score"])

    def test_violation_section_and_review_payload(self):
        r = self.client.post(f"/api/v1/sessions/{self.session.id}/violations/",
                             {"token": str(self.invite.token), "type": "tab_switch", "section": "B"},
                             content_type="application/json", HTTP_X_INVITE_TOKEN=str(self.invite.token))
        self.assertLess(r.status_code, 300, r.content)
        data = self.hr.get(f"/api/v1/sessions/{self.session.id}/").json()["data"]
        self.assertEqual(data["violations"][0]["section"], "B")
        self.assertEqual([s["key"] for s in data["test_sections"]], ["A", "B"])
        self.assertTrue(all("question_section" in a for a in data["answers"]))

    def test_sections_and_instructions_saved_and_shown_to_candidate(self):
        r = self.hr.patch(f"/api/v1/assessments/{self.test.id}/", {
            "instructions": "Rule one\nRule two",
            "sections": [{"key": "A", "title": "Section A"}, {"key": "B", "title": "Quant", "quantitative": True}],
        }, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        info = self.client.get(f"/api/v1/invites/validate/{self.invite.token}/").json()["data"]["test"]
        self.assertEqual(info["instructions"], "Rule one\nRule two")
        self.assertEqual(info["sections"][1]["quantitative"], True)
        self.assertEqual(info["sections"][0]["question_count"], 1)
        # a list question needs 1-20 rows
        r = self.hr.post(f"/api/v1/assessments/{self.test.id}/questions/",
                         {"type": "list", "body": "x", "payload": {"count": 0}, "points": 1}, format="json")
        self.assertEqual(r.status_code, 400)


class QuestionTypeChangeTests(SectionsAndMarkingTests):
    def test_type_change_blocked_only_while_someone_is_taking_the_test(self):
        url = f"/api/v1/assessments/{self.test.id}/questions/{self.lst.id}/"
        change = {"type": "short_answer", "payload": {"word_limit": 300}}
        # Ada is still writing: blocked
        r = self.hr.patch(url, change, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("taking this test right now", r.json()["error"])
        # She has finished (even though she answered it): allowed, her answer is kept
        Session.objects.filter(pk=self.session.pk).update(status=Session.Status.SUBMITTED)
        r = self.hr.patch(url, change, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.a_list.refresh_from_db()
        self.assertEqual(self.a_list.response, {"items": ["a", "b", "c"]})


class YesNoQuestionTests(SectionsAndMarkingTests):
    def test_yes_no_with_follow_up(self):
        url = f"/api/v1/assessments/{self.test.id}/questions/"
        ok = self.hr.post(url, {"type": "yes_no", "body": "Are you learning any new skills now?", "points": 2,
                                "payload": {"follow_up_on": "yes", "follow_up_prompt": "If yes, tell us.", "min_chars": 10}}, format="json")
        self.assertEqual(ok.status_code, 201, ok.content)
        bad = self.hr.post(url, {"type": "yes_no", "body": "x", "points": 1, "payload": {"follow_up_on": "maybe"}}, format="json")
        self.assertEqual(bad.status_code, 400)
        # the candidate sees the follow-up question (it isn't an answer key)
        qs = self.client.get(f"/api/v1/sessions/{self.session.id}/questions/", HTTP_X_INVITE_TOKEN=str(self.invite.token)).json()["data"]
        yn = [q for q in qs if q["type"] == "yes_no"][0]
        self.assertEqual(yn["payload"]["follow_up_prompt"], "If yes, tell us.")


class WaveTests(TestCase):
    def test_batch_send_records_wave_and_window(self):
        from unittest import mock
        from rest_framework.test import APIClient
        from users.models import User
        client = APIClient()
        client.force_authenticate(User.objects.create_user("hr@example.com", "pw-12345678"))
        test = Test.objects.create(title="BDE", duration_mins=40)
        with mock.patch("invites.views.run_in_background"):
            r = client.post("/api/v1/invites/batch/", {
                "test_id": test.id, "wave": 2,
                "opens_at": "2026-12-01T10:40:00+01:00", "expires_at": "2026-12-01T10:55:00+01:00",
                "candidates": [{"name": "Ada Obi", "email": "ada@example.com"}],
            }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["data"]["invites"][0]["wave"], 2)
        self.assertEqual(Invite.objects.get().wave, 2)


class EndSessionTests(TestCase):
    def test_hr_can_end_an_open_test_and_it_reaches_results(self):
        from io import StringIO
        from django.core.management import call_command
        from rest_framework.test import APIClient
        from users.models import User
        client = APIClient()
        client.force_authenticate(User.objects.create_user("hr@example.com", "pw-12345678"))
        test = Test.objects.create(title="BDE", duration_mins=40)
        a = Invite.objects.create(test=test, candidate_name="Stuck One", candidate_email="s1@example.com", status="active")
        s1 = Session.objects.create(invite=a, status=Session.Status.IN_PROGRESS)
        b = Invite.objects.create(test=test, candidate_name="Waiting One", candidate_email="s2@example.com", status="active")
        s2 = Session.objects.create(invite=b, status=Session.Status.WAITING)

        r = client.post(f"/api/v1/sessions/{s1.id}/end/")
        self.assertEqual(r.status_code, 200, r.content)
        s1.refresh_from_db(); a.refresh_from_db()
        self.assertEqual((s1.status, a.status), ("submitted", "submitted"))
        self.assertEqual(client.post(f"/api/v1/sessions/{s1.id}/end/").status_code, 400)

        out = StringIO()
        call_command("end_open_tests", "--email", "s2@example.com", stdout=out)
        s2.refresh_from_db()
        self.assertEqual(s2.status, "submitted")
        self.assertIn("Ended 1", out.getvalue())

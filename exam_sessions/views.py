"""
exam_sessions/views.py
───────────────────────
Candidate endpoints (X-Invite-Token header required):
  POST /api/v1/sessions/start/               — begin test, create session
  GET  /api/v1/sessions/<id>/questions/      — get questions (answers stripped)
  POST /api/v1/sessions/<id>/draft/          — auto-save answers every 2 min
  GET  /api/v1/sessions/<id>/timer/          — sync server countdown
  POST /api/v1/sessions/<id>/submit/         — final submission
  POST /api/v1/sessions/<id>/violations/     — log a proctoring event
  POST /api/v1/sessions/<id>/recording/      — upload a 30s video chunk

HR / Reviewer endpoints (JWT required):
  GET  /api/v1/sessions/                          — list all sessions
  GET  /api/v1/sessions/<id>/                     — full session detail
  GET  /api/v1/sessions/<id>/recording-url/       — signed recording URL
  POST /api/v1/sessions/<id>/answers/<a_id>/score/— score an answer
  POST /api/v1/sessions/<id>/result/              — submit pass/fail decision
"""

import os
from datetime import timedelta

from django.core import signing
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from django.conf import settings

from assessments.models import Question
from assessments.serializers import QuestionSerializer
from invites.models import Invite
from .authentication import InviteTokenAuthentication
from .models import Session, Answer, Violation
from .serializers import (
    SessionSerializer,
    SaveDraftSerializer,
    ViolationSerializer,
    ReviewScoreSerializer,
)
from .services import submit_session
from . import livekit
from .emails import send_outcome_emails
from .storage import upload_file, read_file, recording_chunk_keys, iter_recording, assemble_recording
from config.background import run_in_background


def success(data, code=status.HTTP_200_OK):
    return Response({"success": True, "data": data}, status=code)


# ─── Candidate Views ──────────────────────────────────────────────────────────

class StartSessionView(APIView):
    """
    Called when candidate clicks 'Begin Test'.
    Creates a Session and marks invite as active.
    Idempotent — returns existing session if the page is refreshed.
    """
    authentication_classes = [InviteTokenAuthentication]
    permission_classes = [AllowAny]

    def post(self, request):
        invite = request.auth

        if invite.opens_at and timezone.now() < invite.opens_at and not hasattr(invite, "session"):
            return Response(
                {"success": False, "code": "not_open", "error": "Your assessment hasn't opened yet."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Return existing session on page refresh
        if hasattr(invite, "session"):
            session = invite.session
            if session.status == Session.Status.SUBMITTED:
                return Response(
                    {"success": False, "error": "This test has already been submitted."},
                    status=status.HTTP_410_GONE,
                )
            return success({
                "session_id": session.id,
                "remaining_seconds": session.remaining_seconds,
                "status": session.status,
            })

        # Mark invite active and put the candidate in the waiting room:
        # camera on, terms on screen, no questions / timer until HR starts them.
        invite.status = Invite.Status.ACTIVE
        invite.save(update_fields=["status"])
        session = Session.objects.create(invite=invite, status=Session.Status.WAITING)

        return success(
            {"session_id": session.id, "remaining_seconds": session.remaining_seconds, "status": session.status},
            status.HTTP_201_CREATED,
        )


class SessionQuestionsView(APIView):
    """
    Returns the test questions for this session.
    correct_index is stripped from MCQ payloads — candidates must not see it.
    """
    authentication_classes = [InviteTokenAuthentication]
    permission_classes = [AllowAny]

    def get(self, request, pk):
        session = Session.objects.filter(pk=pk, invite=request.auth).first()
        if not session:
            return Response({"success": False, "error": "Session not found."}, status=404)
        if session.status == Session.Status.WAITING:
            return Response(
                {"success": False, "code": "waiting", "error": "Your test hasn't been started yet."},
                status=409,
            )
        if session.status != Session.Status.IN_PROGRESS:
            return Response({"success": False, "error": "This test is no longer active."}, status=410)

        questions = session.invite.test.questions.all()
        data = []
        for q in questions:
            q_data = QuestionSerializer(q).data
            if q.type == Question.Type.MCQ:
                # Remove the correct answer before sending to the candidate
                payload = dict(q_data["payload"])
                payload.pop("correct_index", None)
                q_data["payload"] = payload
            data.append(q_data)

        return success(data)


class SaveDraftView(APIView):
    """
    Auto-save endpoint — called every 2 minutes by the frontend.
    Upserts Answer rows so no work is lost on a crash or disconnect.
    """
    authentication_classes = [InviteTokenAuthentication]
    permission_classes = [AllowAny]

    def post(self, request, pk):
        try:
            session = Session.objects.get(pk=pk, invite=request.auth)
        except Session.DoesNotExist:
            return Response(
                {"success": False, "error": "Session not found."}, status=404
            )

        if session.status != Session.Status.IN_PROGRESS:
            return Response(
                {"success": False, "error": "Session is no longer active."},
                status=400,
            )

        serializer = SaveDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Only accept answers for questions that belong to this test
        valid_ids = set(
            session.invite.test.questions.values_list("id", flat=True)
        )
        saved = 0
        for item in serializer.validated_data["answers"]:
            if item["question_id"] not in valid_ids:
                continue
            Answer.objects.update_or_create(
                session=session,
                question_id=item["question_id"],
                defaults={"response": item["response"]},
            )
            saved += 1

        return success({"saved": saved})


class TimerSyncView(APIView):
    """
    Returns server-authoritative time remaining.
    Called every 60 seconds by the frontend to prevent clock drift.
    Also auto-submits if the timer has run out.
    """
    authentication_classes = [InviteTokenAuthentication]
    permission_classes = [AllowAny]

    def get(self, request, pk):
        try:
            session = Session.objects.get(pk=pk, invite=request.auth)
        except Session.DoesNotExist:
            return Response(
                {"success": False, "error": "Session not found."}, status=404
            )

        if session.is_expired and session.status == Session.Status.IN_PROGRESS:
            session.status = Session.Status.TIMED_OUT
            session.submitted_at = timezone.now()
            session.save(update_fields=["status", "submitted_at"])
            submit_session(session)
            return success({"remaining_seconds": 0, "status": "timed_out"})

        return success({
            "remaining_seconds": session.remaining_seconds,
            "status": session.status,
        })


class SubmitSessionView(APIView):
    """Final submission by the candidate."""
    authentication_classes = [InviteTokenAuthentication]
    permission_classes = [AllowAny]

    def post(self, request, pk):
        try:
            session = Session.objects.get(pk=pk, invite=request.auth)
        except Session.DoesNotExist:
            return Response(
                {"success": False, "error": "Session not found."}, status=404
            )

        if session.status == Session.Status.SUBMITTED:
            return success("Already submitted.")
        if session.status == Session.Status.WAITING:
            return Response({"success": False, "error": "Your test hasn't been started yet."}, status=409)

        submit_session(session)
        return success("Test submitted successfully.")


class LogViolationView(APIView):
    """
    Candidate browser posts a violation event here.
    Violations are append-only — never updated or deleted.
    """
    authentication_classes = [InviteTokenAuthentication]
    permission_classes = [AllowAny]

    def post(self, request, pk):
        try:
            session = Session.objects.get(pk=pk, invite=request.auth)
        except Session.DoesNotExist:
            return Response(
                {"success": False, "error": "Session not found."}, status=404
            )

        serializer = ViolationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        def number(name, cast):
            try:
                value = request.data.get(name)
                return cast(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                return None

        violation = Violation.objects.create(
            session=session,
            type=serializer.validated_data["type"],
            video_offset_seconds=number("video_offset", float),
            recording_part=number("part", int),
        )
        return success({"violation_id": violation.id}, status.HTTP_201_CREATED)


class UploadRecordingChunkView(APIView):
    """
    Receives a 30-second WebM video chunk from the candidate browser.
    Chunks are stored as: recordings/<session_id>/chunk_<index>.webm
    """
    authentication_classes = [InviteTokenAuthentication]
    permission_classes = [AllowAny]

    def post(self, request, pk):
        try:
            session = Session.objects.get(pk=pk, invite=request.auth)
        except Session.DoesNotExist:
            return Response(
                {"success": False, "error": "Session not found."}, status=404
            )

        chunk = request.FILES.get("chunk")
        chunk_index = request.data.get("chunk_index", 0)

        if not chunk:
            return Response(
                {"success": False, "error": "No chunk file provided."}, status=400
            )

        try:
            part = int(request.data.get("part") or 0)
        except (TypeError, ValueError):
            part = 0
        if part > 0:  # one part per page load; a refresh never overwrites earlier video
            object_key = f"recordings/{session.id}/chunk_{part:013d}_{int(chunk_index):05d}.webm"
        else:
            object_key = f"recordings/{session.id}/chunk_{int(chunk_index):04d}.webm"
        upload_file(chunk, object_key, content_type="video/webm")

        return success({"chunk_index": chunk_index, "key": object_key}, status.HTTP_201_CREATED)


class SnapshotUploadView(APIView):
    """
    Candidate heartbeat for the live monitor, sent every ~5 seconds (also in
    the waiting room): a small webcam JPEG (optional), how many questions are
    answered, and whether the face check is running. The reply tells the
    waiting room when HR has started the test.
    Only the latest snapshot is kept: snapshots/<session_id>/latest.jpg
    """
    authentication_classes = [InviteTokenAuthentication]
    permission_classes = [AllowAny]

    MAX_BYTES = 300 * 1024

    def post(self, request, pk):
        session = Session.objects.filter(pk=pk, invite=request.auth).first()
        if not session:
            return Response({"success": False, "error": "Session not found."}, status=404)
        if session.status not in (Session.Status.IN_PROGRESS, Session.Status.WAITING):
            return success({"status": session.status, "remaining_seconds": 0})

        now = timezone.now()
        session.last_seen_at = now
        fields = ["last_seen_at"]

        face = str(request.data.get("face") or "")[:120]
        if face and face != session.face_status:
            session.face_status = face
            fields.append("face_status")

        try:
            answered = int(request.data.get("answered", session.answered_count))
            session.answered_count = max(0, min(answered, 1000))
            fields.append("answered_count")
        except (TypeError, ValueError):
            pass

        image = request.FILES.get("image")
        if image and image.size <= self.MAX_BYTES:
            key = f"snapshots/{session.id}/latest.jpg"
            upload_file(image, key, content_type="image/jpeg")
            session.snapshot_path = key
            session.snapshot_at = now
            fields += ["snapshot_path", "snapshot_at"]

        session.save(update_fields=fields)
        return success({"status": session.status, "remaining_seconds": session.remaining_seconds})


class LiveTokenView(APIView):
    """
    Candidate: a LiveKit token for their own private room (camera + mic out,
    HR's voice in). Works in the waiting room and during the test.
    """
    authentication_classes = [InviteTokenAuthentication]
    permission_classes = [AllowAny]

    def get(self, request, pk):
        session = Session.objects.filter(pk=pk, invite=request.auth).first()
        if not session or session.status not in (Session.Status.WAITING, Session.Status.IN_PROGRESS):
            return Response({"success": False, "error": "Session not active."}, status=404)
        if not livekit.is_configured():
            return success({"enabled": False})
        room = livekit.room_name(session.id)
        return success({
            "enabled": True,
            "url": settings.LIVEKIT_URL,
            "room": room,
            "token": livekit.make_token(f"candidate-{session.id}", session.invite.candidate_name, room),
        })


# ─── HR / Reviewer Views ──────────────────────────────────────────────────────

class LiveSessionsView(APIView):
    """Staff: everyone taking a test right now, for the Live page (polled every few seconds)."""
    permission_classes = [IsAuthenticated]

    ONLINE_WINDOW = timedelta(seconds=35)

    def get(self, request):
        sessions = (
            Session.objects.filter(status__in=[Session.Status.WAITING, Session.Status.IN_PROGRESS])
            .select_related("invite__test")
            .order_by("status", "started_at")
        )
        now = timezone.now()
        data = []
        for s in sessions:
            if s.status == Session.Status.IN_PROGRESS and s.is_expired:
                continue
            recent = list(s.violations.order_by("-occurred_at")[:5])
            data.append({
                "id": s.id,
                "candidate_name": s.invite.candidate_name,
                "candidate_email": s.invite.candidate_email,
                "test_title": s.invite.test.title,
                "started_at": s.started_at,
                "remaining_seconds": s.remaining_seconds,
                "answered_count": s.answered_count,
                "question_count": s.invite.test.questions.count(),
                "violation_count": s.violations.count(),
                "recent_violations": [
                    {"type": v.type, "label": v.get_type_display(), "occurred_at": v.occurred_at}
                    for v in recent
                ],
                "last_seen_at": s.last_seen_at,
                "online": bool(s.last_seen_at and now - s.last_seen_at < self.ONLINE_WINDOW),
                "snapshot_at": s.snapshot_at,
                "status": s.status,
                "face_status": s.face_status,
                "duration_mins": s.invite.test.duration_mins,
            })
        return success({"sessions": data, "live_video": livekit.is_configured()})


class StartTestView(APIView):
    """Staff: start a waiting candidate's test (shows questions, starts the clock)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        session = Session.objects.filter(pk=pk).first()
        if not session:
            return Response({"success": False, "error": "Session not found."}, status=404)
        if session.status != Session.Status.WAITING:
            return Response({"success": False, "error": "This candidate's test has already started."}, status=400)
        session.status = Session.Status.IN_PROGRESS
        session.started_at = timezone.now()
        session.save(update_fields=["status", "started_at"])
        return success({"status": session.status, "remaining_seconds": session.remaining_seconds})


class HrLiveTokenView(APIView):
    """Staff: a LiveKit token to watch / listen to / talk to one candidate."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        session = Session.objects.filter(pk=pk).first()
        if not session:
            return Response({"success": False, "error": "Session not found."}, status=404)
        if not livekit.is_configured():
            return success({"enabled": False})
        room = livekit.room_name(session.id)
        return success({
            "enabled": True,
            "url": settings.LIVEKIT_URL,
            "room": room,
            "token": livekit.make_token(f"hr-{request.user.id}", request.user.full_name, room),
        })


class SnapshotView(APIView):
    """Staff: the latest webcam snapshot for a session (image/jpeg). Never public."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        session = Session.objects.filter(pk=pk).only("snapshot_path").first()
        if not session or not session.snapshot_path:
            return Response({"success": False, "error": "No snapshot yet."}, status=404)
        try:
            data = read_file(session.snapshot_path)
        except Exception:
            return Response({"success": False, "error": "Snapshot not available."}, status=404)
        response = HttpResponse(data, content_type="image/jpeg")
        response["Cache-Control"] = "no-store"
        return response



class SessionListView(APIView):
    """Staff: list all sessions (every staff account sees all of them)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sessions = Session.objects.select_related(
            "invite__test", "invite", "invite__job", "reviewed_by"
        ).order_by("-started_at")
        job = request.query_params.get("job")
        if job == "none":
            sessions = sessions.filter(invite__job__isnull=True)
        elif job:
            sessions = sessions.filter(invite__job_id=job)

        data = [
            {
                "id": s.id,
                "candidate_name": s.invite.candidate_name,
                "candidate_email": s.invite.candidate_email,
                "test_title": s.invite.test.title,
                "status": s.status,
                "started_at": s.started_at,
                "submitted_at": s.submitted_at,
                "violation_count": s.violations.count(),
                "decision": s.decision,
                "score_pct": s.score_pct,
                "reviewed_at": s.reviewed_at,
                "graded_by_name": s.graded_by_name,
                "job_id": s.invite.job_id,
                "job_title": s.invite.job.title if s.invite.job_id else None,
                "decision_notes": s.decision_notes,
                "outcome_email_kind": s.outcome_email_kind,
                "outcome_emailed_at": s.outcome_emailed_at,
                "attendance_confirmed_at": s.attendance_confirmed_at,
            }
            for s in sessions
        ]
        return success(data)


class SessionDetailView(APIView):
    """HR/Reviewer: full session detail with all answers and violations."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            session = Session.objects.get(
                pk=pk
            )
        except Session.DoesNotExist:
            return Response(
                {"success": False, "error": "Session not found."}, status=404
            )
        return success(SessionSerializer(session).data)


class RecordingURLView(APIView):
    """
    Staff: a short-lived link to the WHOLE recording, with sound. The 30-second
    pieces are joined into one file first (quick; repeated only if new pieces
    arrived). The link is signed, expires after an hour, and recordings are
    never public.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if not Session.objects.filter(pk=pk).exists():
            return Response({"success": False, "error": "Session not found."}, status=404)
        chunks = recording_chunk_keys(pk)
        if not chunks:
            return Response({"success": False, "error": "No recording was uploaded for this session."}, status=404)
        assemble_recording(pk)
        sig = signing.TimestampSigner(salt="recording").sign(str(pk))
        return success({
            "path": f"/api/v1/sessions/{pk}/recording/stream/?sig={sig}",
            "chunks": len(chunks),
            "approx_seconds": len(chunks) * 30,
            "expires_in_seconds": 3600,
        })


class RecordingStreamView(APIView):
    """
    The recording (video/webm) for a valid signed link. Supports byte ranges,
    so the browser fetches it a slice at a time as it plays and the seek bar
    works — no single long download (which the server would cut off).
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    SLICE = 4 * 1024 * 1024  # max bytes per range reply

    def get(self, request, pk):
        try:
            value = signing.TimestampSigner(salt="recording").unsign(request.query_params.get("sig", ""), max_age=3600)
        except signing.BadSignature:
            return Response({"success": False, "error": "This recording link has expired. Reopen the result."}, status=403)
        if value != str(pk):
            return Response({"success": False, "error": "Recording not found."}, status=404)

        path = assemble_recording(pk)
        if not path:
            # Object storage: stream the pieces (no ranges)
            if not recording_chunk_keys(pk):
                return Response({"success": False, "error": "Recording not found."}, status=404)
            response = StreamingHttpResponse(iter_recording(pk), content_type="video/webm")
            response["Cache-Control"] = "private, no-store"
            return response

        size = os.path.getsize(path)
        start, end = 0, size - 1
        header = request.headers.get("Range", "")
        partial = header.startswith("bytes=")
        if not partial:
            # Plain request (e.g. a download): the whole file
            response = FileResponse(open(path, "rb"), content_type="video/webm")
            response["Accept-Ranges"] = "bytes"
            response["Cache-Control"] = "private, max-age=3600"
            return response
        if partial:
            try:
                first, _, last = header[6:].split(",")[0].partition("-")
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                else:  # "bytes=-500": the last 500 bytes
                    start = max(0, size - int(last))
            except ValueError:
                partial = False
        if start >= size:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        end = min(end, size - 1, start + self.SLICE - 1)

        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        response = HttpResponse(data, status=206 if partial else 200, content_type="video/webm")
        if partial:
            response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Accept-Ranges"] = "bytes"
        response["Content-Length"] = str(len(data))
        response["Cache-Control"] = "private, max-age=3600"
        return response


class AddTimeView(APIView):
    """
    HR: POST {"minutes": 10}
    - Test still running (or waiting): adds the minutes to the clock.
    - Test ended (ran out of time / submitted) but not yet decided: reopens it
      so the candidate can continue from where they stopped with exactly
      that many minutes left, using the same link. Answers are kept.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            minutes = int(request.data.get("minutes") or 10)
        except (TypeError, ValueError):
            minutes = 10
        if not 1 <= minutes <= 180:
            return Response({"success": False, "error": "Choose between 1 and 180 minutes."}, status=400)
        session = Session.objects.select_related("invite__test").filter(pk=pk).first()
        if not session:
            return Response({"success": False, "error": "Session not found."}, status=404)

        reopened = False
        if session.status in (Session.Status.WAITING, Session.Status.IN_PROGRESS) and not session.is_expired:
            session.extra_seconds += minutes * 60
            session.save(update_fields=["extra_seconds"])
        else:
            if session.decision or session.status == Session.Status.REVIEWED:
                return Response({"success": False, "error": "This result has already been decided, so the test can't be reopened."}, status=400)
            if session.status == Session.Status.WAITING:
                session.extra_seconds += minutes * 60
            else:
                # exactly `minutes` left from now
                base = session.invite.test.duration_mins * 60
                elapsed = int((timezone.now() - session.started_at).total_seconds())
                session.extra_seconds = max(0, elapsed - base) + minutes * 60
                session.status = Session.Status.IN_PROGRESS
                session.submitted_at = None
                reopened = True
            session.save(update_fields=["extra_seconds", "status", "submitted_at"])
            invite = session.invite
            invite.status = Invite.Status.ACTIVE
            invite.expires_at = max(invite.expires_at, timezone.now() + timedelta(minutes=minutes + 60))
            invite.save(update_fields=["status", "expires_at"])
        return success({
            "remaining_seconds": session.remaining_seconds,
            "status": session.status,
            "reopened": reopened,
        })


class ConfirmAttendanceView(APIView):
    """
    Public (from the "Confirm your attendance" button in the next-stage email).
    POST {"token": "..."} — records the confirmation once and emails HR.
    POST, not GET, so email link-scanners can't confirm by accident.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        from config.emails import hr_address
        from django.core.mail import EmailMessage
        try:
            session_id = int(signing.TimestampSigner(salt="attendance").unsign(
                str(request.data.get("token") or ""), max_age=60 * 60 * 24 * 60))
        except (signing.BadSignature, ValueError):
            return Response({"success": False, "error": "This confirmation link isn't valid."}, status=404)
        session = Session.objects.select_related("invite__test", "invite__job").filter(pk=session_id).first()
        if not session:
            return Response({"success": False, "error": "This confirmation link isn't valid."}, status=404)

        invite = session.invite
        role = invite.job.title if invite.job_id else (invite.test.job_role or invite.test.title)
        details = session.interview_details or {}
        already = session.attendance_confirmed_at is not None
        if not already:
            session.attendance_confirmed_at = timezone.now()
            session.save(update_fields=["attendance_confirmed_at"])
            try:
                EmailMessage(
                    f"Attendance confirmed: {invite.candidate_name} – {role}",
                    (
                        f"{invite.candidate_name} ({invite.candidate_email}) has confirmed they will attend "
                        f"the next stage for {role}.\n\n"
                        f"Date: {details.get('date', '-')}\nTime: {details.get('time', '-')}\n"
                        f"Address: {details.get('address', '-')}\n\n"
                        f"Confirmed at {timezone.localtime(session.attendance_confirmed_at).strftime('%d %b %Y, %I:%M %p')}."
                    ),
                    settings.DEFAULT_FROM_EMAIL,
                    [hr_address()],
                ).send()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Attendance email to HR failed for session %s", session.id)
        return success({
            "name": invite.candidate_name.split(" ")[0] if invite.candidate_name else "",
            "role": role,
            "date": details.get("date", ""),
            "time": details.get("time", ""),
            "address": details.get("address", ""),
            "already": already,
        })


class OutcomeEmailView(APIView):
    """
    Staff: email results.
    POST /api/v1/sessions/outcome-emails/
      {
        "passed": [{"session_id": 5, "date": "Mon 10 Nov", "time": "10:00 AM"}],
        "address": "...", "notes": "...",
        "failed": [6, 7], "include_remarks": true
      }
    Only sessions with a matching decision are emailed. Sent in the background.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        passed_in = request.data.get("passed") or []
        failed_in = request.data.get("failed") or []
        address = str(request.data.get("address") or "").strip()
        notes = str(request.data.get("notes") or "")[:3000]

        passed = []
        for p in passed_in:
            try:
                sid = int(p.get("session_id"))
            except (TypeError, ValueError, AttributeError):
                continue
            date, time_ = str(p.get("date") or "").strip(), str(p.get("time") or "").strip()
            if not (date and time_):
                return Response({"success": False, "error": "Every passed candidate needs a date and time."}, status=400)
            passed.append({"session_id": sid, "date": date[:100], "time": time_[:50]})
        if passed and not address:
            return Response({"success": False, "error": "Enter the address for the next stage."}, status=400)

        ok_passed = set(Session.objects.filter(pk__in=[p["session_id"] for p in passed], decision="pass").values_list("id", flat=True))
        ok_failed = list(Session.objects.filter(pk__in=[int(i) for i in failed_in if str(i).isdigit()], decision="fail").values_list("id", flat=True))
        passed = [p for p in passed if p["session_id"] in ok_passed]
        if not passed and not ok_failed:
            return Response({"success": False, "error": "No candidates to email."}, status=400)

        run_in_background(send_outcome_emails, passed, ok_failed, address, notes, bool(request.data.get("include_remarks", True)))
        return success({"passed": len(passed), "failed": len(ok_failed)}, status.HTTP_202_ACCEPTED)


class ReviewAnswerView(APIView):
    """Reviewer: submit a score for a short-answer response."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, answer_id):
        try:
            answer = Answer.objects.get(
                pk=answer_id,
                session__pk=pk,
            )
        except Answer.DoesNotExist:
            return Response(
                {"success": False, "error": "Answer not found."}, status=404
            )

        serializer = ReviewScoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Cap score at the question's maximum point value
        max_points = answer.question.points
        score = min(serializer.validated_data["manual_score"], max_points)

        answer.manual_score = score
        answer.reviewer_comment = serializer.validated_data.get("reviewer_comment", "")
        answer.graded_by = request.user
        answer.graded_at = timezone.now()
        answer.save(update_fields=["manual_score", "reviewer_comment", "graded_by", "graded_at"])

        return success({
            "answer_id": answer.id,
            "manual_score": answer.manual_score,
            "graded_by_name": request.user.full_name,
        })


class SubmitResultView(APIView):
    """
    Reviewer submits the final pass/fail/hold decision.
    Calculates total score from MCQ auto-scores + manual essay scores.
    """
    permission_classes = [IsAuthenticated]

    DECISIONS = ("pass", "fail", "hold")

    def post(self, request, pk):
        try:
            session = Session.objects.prefetch_related("answers__question").get(
                pk=pk
            )
        except Session.DoesNotExist:
            return Response(
                {"success": False, "error": "Session not found."}, status=404
            )

        decision = request.data.get("decision")
        if decision not in self.DECISIONS:
            return Response(
                {"success": False, "error": f"Decision must be one of: {', '.join(self.DECISIONS)}"},
                status=400,
            )

        if session.status == Session.Status.IN_PROGRESS:
            return Response(
                {"success": False, "error": "The candidate hasn't submitted this test yet."},
                status=400,
            )

        # Calculate total score percentage
        total_points = session.invite.test.total_points
        earned = sum(
            (a.auto_score or 0) + (a.manual_score or 0)
            for a in session.answers.all()
        )
        score_pct = round((earned / total_points * 100), 1) if total_points else 0

        # Save the decision (re-submitting overwrites the previous one)
        session.status = Session.Status.REVIEWED
        session.decision = decision
        session.decision_notes = request.data.get("notes", "") or ""
        session.score_pct = score_pct
        session.reviewed_by = request.user
        session.reviewed_at = timezone.now()
        session.save(update_fields=[
            "status", "decision", "decision_notes", "score_pct", "reviewed_by", "reviewed_at",
        ])

        return success({
            "decision": session.decision,
            "score_pct": score_pct,
            "earned_points": earned,
            "total_points": total_points,
            "notes": session.decision_notes,
            "reviewed_by": request.user.email,
            "reviewed_at": session.reviewed_at.isoformat(),
        })
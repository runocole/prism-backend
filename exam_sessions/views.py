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

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

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
from .storage import upload_file, generate_signed_url


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
            })

        # Mark invite active and create session
        invite.status = Invite.Status.ACTIVE
        invite.save(update_fields=["status"])
        session = Session.objects.create(invite=invite)

        return success(
            {"session_id": session.id, "remaining_seconds": session.remaining_seconds},
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
        try:
            session = Session.objects.get(
                pk=pk, invite=request.auth, status=Session.Status.IN_PROGRESS
            )
        except Session.DoesNotExist:
            return Response(
                {"success": False, "error": "Session not found."}, status=404
            )

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

        violation = Violation.objects.create(
            session=session,
            type=serializer.validated_data["type"],
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

        object_key = f"recordings/{session.id}/chunk_{int(chunk_index):04d}.webm"
        upload_file(chunk, object_key, content_type="video/webm")

        return success({"chunk_index": chunk_index, "key": object_key}, status.HTTP_201_CREATED)


# ─── HR / Reviewer Views ──────────────────────────────────────────────────────

class SessionListView(APIView):
    """HR: list all sessions across all their tests."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sessions = Session.objects.filter(
            invite__test__created_by=request.user
        ).select_related("invite__test", "invite").order_by("-started_at")

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
                pk=pk, invite__test__created_by=request.user
            )
        except Session.DoesNotExist:
            return Response(
                {"success": False, "error": "Session not found."}, status=404
            )
        return success(SessionSerializer(session).data)


class RecordingURLView(APIView):
    """HR: get a time-limited signed URL to stream the session recording."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            session = Session.objects.get(
                pk=pk, invite__test__created_by=request.user
            )
        except Session.DoesNotExist:
            return Response(
                {"success": False, "error": "Session not found."}, status=404
            )

        if not session.recording_path:
            return Response(
                {"success": False, "error": "No recording available yet."}, status=404
            )

        url = generate_signed_url(session.recording_path, expires_in=3600)
        return success({"url": url, "expires_in_seconds": 3600})


class ReviewAnswerView(APIView):
    """Reviewer: submit a score for a short-answer response."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, answer_id):
        try:
            answer = Answer.objects.get(
                pk=answer_id,
                session__pk=pk,
                session__invite__test__created_by=request.user,
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
        answer.save(update_fields=["manual_score", "reviewer_comment"])

        return success({"answer_id": answer.id, "manual_score": answer.manual_score})


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
                pk=pk, invite__test__created_by=request.user
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

        # Calculate total score percentage
        total_points = session.invite.test.total_points
        earned = sum(
            (a.auto_score or 0) + (a.manual_score or 0)
            for a in session.answers.all()
        )
        score_pct = round((earned / total_points * 100), 1) if total_points else 0

        return success({
            "decision": decision,
            "score_pct": score_pct,
            "earned_points": earned,
            "total_points": total_points,
            "notes": request.data.get("notes", ""),
            "reviewed_by": request.user.email,
            "reviewed_at": timezone.now().isoformat(),
        })
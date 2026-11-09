"""
exam_sessions/urls.py
"""

from django.urls import path
from .views import (
    StartSessionView,
    SessionQuestionsView,
    SaveDraftView,
    TimerSyncView,
    SubmitSessionView,
    LogViolationView,
    UploadRecordingChunkView,
    SessionListView,
    SessionDetailView,
    RecordingURLView,
    ReviewAnswerView,
    SubmitResultView,
    SnapshotUploadView,
    LiveSessionsView,
    SnapshotView,
    LiveTokenView,
    StartTestView,
    HrLiveTokenView,
    RecordingStreamView,
    OutcomeEmailView,
    ConfirmAttendanceView,
    AddTimeView,
)

urlpatterns = [
    # ── Candidate (X-Invite-Token header) ─────────────────────────────────────
    path("start/", StartSessionView.as_view(), name="session-start"),
    path("<int:pk>/questions/", SessionQuestionsView.as_view(), name="session-questions"),
    path("<int:pk>/draft/", SaveDraftView.as_view(), name="session-draft"),
    path("<int:pk>/timer/", TimerSyncView.as_view(), name="session-timer"),
    path("<int:pk>/submit/", SubmitSessionView.as_view(), name="session-submit"),
    path("<int:pk>/violations/", LogViolationView.as_view(), name="session-violations"),
    path("<int:pk>/recording/", UploadRecordingChunkView.as_view(), name="session-recording"),
    path("<int:pk>/heartbeat/", SnapshotUploadView.as_view(), name="session-heartbeat"),
    path("<int:pk>/live-token/", LiveTokenView.as_view(), name="session-live-token"),

    # ── HR / Reviewer (Bearer JWT) ────────────────────────────────────────────
    path("", SessionListView.as_view(), name="session-list"),
    path("live/", LiveSessionsView.as_view(), name="session-live"),
    path("outcome-emails/", OutcomeEmailView.as_view(), name="session-outcome-emails"),
    path("<int:pk>/add-time/", AddTimeView.as_view(), name="session-add-time"),
    path("confirm-attendance/", ConfirmAttendanceView.as_view(), name="session-confirm-attendance"),
    path("<int:pk>/start-test/", StartTestView.as_view(), name="session-start-test"),
    path("<int:pk>/live-token/hr/", HrLiveTokenView.as_view(), name="session-live-token-hr"),
    path("<int:pk>/recording/stream/", RecordingStreamView.as_view(), name="session-recording-stream"),
    path("<int:pk>/snapshot/", SnapshotView.as_view(), name="session-snapshot"),
    path("<int:pk>/", SessionDetailView.as_view(), name="session-detail"),
    path("<int:pk>/recording-url/", RecordingURLView.as_view(), name="session-recording-url"),
    path("<int:pk>/answers/<int:answer_id>/score/", ReviewAnswerView.as_view(), name="answer-score"),
    path("<int:pk>/result/", SubmitResultView.as_view(), name="session-result"),
]
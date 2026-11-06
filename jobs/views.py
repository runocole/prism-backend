"""
jobs/views.py
──────────────
HR endpoints (JWT required):
  GET/POST   /api/v1/jobs/                        — list / create job posts
  GET/PATCH  /api/v1/jobs/<id>/                   — retrieve / update job post
  POST       /api/v1/jobs/<id>/open/              — publish job post
  POST       /api/v1/jobs/<id>/close/             — close job post
  GET        /api/v1/jobs/<id>/applications/      — list applications for a job

Public endpoints (no auth):
  GET        /api/v1/jobs/apply/<slug>/           — get job info for application form
  POST       /api/v1/jobs/apply/<slug>/           — submit application

Blacklist:
  GET/POST   /api/v1/jobs/blacklist/              — list / add to blacklist
  DELETE     /api/v1/jobs/blacklist/<id>/         — remove from blacklist
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser

from config.background import run_in_background

from .models import JobPost, Application, Blacklist
from .serializers import (
    JobPostSerializer,
    ApplicationSerializer,
    PublicApplicationSerializer,
    BlacklistSerializer,
)
from .screening import screen_applications

# A "screening" application older than this is assumed stuck (e.g. the
# server restarted mid-run) and is picked up again by the next run.
SCREENING_STALE_AFTER = timedelta(minutes=15)


def success(data, code=status.HTTP_200_OK):
    return Response({"success": True, "data": data}, status=code)


# ── HR: Job Post Management ───────────────────────────────────────────────────

class JobPostListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        jobs = JobPost.objects.filter(created_by=request.user)
        return success(JobPostSerializer(jobs, many=True).data)

    def post(self, request):
        serializer = JobPostSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = serializer.save(created_by=request.user)
        return success(JobPostSerializer(job).data, status.HTTP_201_CREATED)


class JobPostDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_job(self, pk, user):
        try:
            return JobPost.objects.get(pk=pk, created_by=user)
        except JobPost.DoesNotExist:
            return None

    def get(self, request, pk):
        job = self._get_job(pk, request.user)
        if not job:
            return Response({"success": False, "error": "Job not found."}, status=404)
        return success(JobPostSerializer(job).data)

    def patch(self, request, pk):
        job = self._get_job(pk, request.user)
        if not job:
            return Response({"success": False, "error": "Job not found."}, status=404)
        serializer = JobPostSerializer(job, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success(serializer.data)


class OpenJobView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            job = JobPost.objects.get(pk=pk, created_by=request.user)
        except JobPost.DoesNotExist:
            return Response({"success": False, "error": "Job not found."}, status=404)
        job.status = JobPost.Status.OPEN
        job.save(update_fields=["status"])
        return success(JobPostSerializer(job).data)


class CloseJobView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            job = JobPost.objects.get(pk=pk, created_by=request.user)
        except JobPost.DoesNotExist:
            return Response({"success": False, "error": "Job not found."}, status=404)
        job.status = JobPost.Status.CLOSED
        job.save(update_fields=["status"])
        return success(JobPostSerializer(job).data)


class JobApplicationsView(APIView):
    """HR views all applications for a specific job."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            job = JobPost.objects.get(pk=pk, created_by=request.user)
        except JobPost.DoesNotExist:
            return Response({"success": False, "error": "Job not found."}, status=404)

        applications = job.applications.all()

        # Optional status filter
        status_filter = request.query_params.get("status")
        if status_filter:
            applications = applications.filter(status=status_filter)

        return success(ApplicationSerializer(applications, many=True).data)


# ── Public: Application Form ──────────────────────────────────────────────────

class PublicApplicationView(APIView):
    """
    Public endpoint — no JWT needed.
    GET  → returns job info for the application form page
    POST → submits a candidate application
    """
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request, slug):
        try:
            job = JobPost.objects.get(slug=slug, status=JobPost.Status.OPEN)
        except JobPost.DoesNotExist:
           return Response(
            {"success": False, "error": "This job posting is not available."},
            status=status.HTTP_404_NOT_FOUND,
            )
        return success({
        "title": job.title,
        "department": job.department,
        "description": job.description,
        "requirements": job.requirements,
        "screening_questions": job.screening_questions,
         })

    def post(self, request, slug):
        try:
            job = JobPost.objects.get(slug=slug, status=JobPost.Status.OPEN)
        except JobPost.DoesNotExist:
            return Response(
                {"success": False, "error": "This job posting is not available."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check if already applied
        if Application.objects.filter(job=job, email=request.data.get("email")).exists():
            return Response(
                {"success": False, "error": "You have already applied for this position."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = PublicApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = serializer.save(job=job, status=Application.Status.PENDING)

        return success(
            {"message": "Application submitted successfully.", "id": application.id},
            status.HTTP_201_CREATED,
        )


# ── HR: Blacklist Management ──────────────────────────────────────────────────

class BlacklistListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        entries = Blacklist.objects.all().order_by("-created_at")
        return success(BlacklistSerializer(entries, many=True).data)

    def post(self, request):
        serializer = BlacklistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = serializer.save(added_by=request.user)
        return success(BlacklistSerializer(entry).data, status.HTTP_201_CREATED)


class BlacklistDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        try:
            entry = Blacklist.objects.get(pk=pk)
        except Blacklist.DoesNotExist:
            return Response({"success": False, "error": "Not found."}, status=404)
        entry.delete()
        return success("Removed from blacklist.")
    

class ScreenJobView(APIView):
    """
    HR triggers screening for all pending applications on a job.
    Marks them "screening" and runs blacklist → CV (Claude) → MCQ in a
    background thread, so this returns immediately. The frontend polls
    /applications/ until nothing is "screening".
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            job = JobPost.objects.get(pk=pk, created_by=request.user)
        except JobPost.DoesNotExist:
            return Response({"success": False, "error": "Job not found."}, status=404)

        now = timezone.now()
        stale_before = now - SCREENING_STALE_AFTER
        to_screen = job.applications.filter(
            Q(status=Application.Status.PENDING)
            | Q(status=Application.Status.SCREENING, screening_started_at__lt=stale_before)
            | Q(status=Application.Status.SCREENING, screening_started_at__isnull=True)
        )
        ids = list(to_screen.values_list("id", flat=True))

        if not ids:
            return Response(
                {"success": False, "error": "No pending applications to screen."},
                status=400,
            )

        Application.objects.filter(id__in=ids).update(
            status=Application.Status.SCREENING, screening_started_at=now
        )
        run_in_background(screen_applications, ids)

        return success({"queued": len(ids), "application_ids": ids}, status.HTTP_202_ACCEPTED)


class ManualScreenInView(APIView):
    """HR manually overrides a screened-out application to screened-in."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, app_id):
        try:
            application = Application.objects.get(
                pk=app_id,
                job__pk=pk,
                job__created_by=request.user,
            )
        except Application.DoesNotExist:
            return Response({"success": False, "error": "Application not found."}, status=404)

        application.status = Application.Status.SCREENED_IN
        application.ai_summary = (application.ai_summary or "") + " | Manually approved by HR."
        application.save(update_fields=["status", "ai_summary"])

        return success(ApplicationSerializer(application).data)


class BatchEmailView(APIView):
    """
    HR sends a unique exam invite to all screened-in candidates for a job.
    Invites are created (and candidates marked "invited") straight away;
    the emails go out in a background thread. If an email fails, that
    candidate goes back to "screened in" so HR can send again.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            job = JobPost.objects.get(pk=pk, created_by=request.user)
        except JobPost.DoesNotExist:
            return Response({"success": False, "error": "Job not found."}, status=404)

        test_id = request.data.get("test_id")
        if not test_id:
            return Response(
                {"success": False, "error": "test_id is required."},
                status=400,
            )

        from assessments.models import Test
        from invites.models import Invite

        try:
            test = Test.objects.get(
                pk=test_id, created_by=request.user, status=Test.Status.PUBLISHED
            )
        except Test.DoesNotExist:
            return Response({"success": False, "error": "Published test not found."}, status=404)

        screened_in = list(job.applications.filter(status=Application.Status.SCREENED_IN))
        if not screened_in:
            return Response(
                {"success": False, "error": "No screened-in candidates to email."},
                status=400,
            )

        expires_at = timezone.now() + timedelta(hours=48)
        pairs = []
        for app in screened_in:
            invite = Invite.objects.create(
                test=test,
                candidate_name=f"{app.first_name} {app.last_name}".strip(),
                candidate_email=app.email,
                created_by=request.user,
                expires_at=expires_at,
            )
            pairs.append((invite.id, app.id))

        Application.objects.filter(id__in=[a.id for a in screened_in]).update(
            status=Application.Status.INVITED
        )
        run_in_background(send_job_invite_emails, pairs, job.title)

        return success(
            {"queued": len(pairs), "total": len(pairs)},
            status.HTTP_202_ACCEPTED,
        )


def send_job_invite_emails(pairs: list[tuple[int, int]], job_title: str):
    """Background job for BatchEmailView."""
    from invites.emails import send_invite_email
    from invites.models import Invite

    for invite_id, app_id in pairs:
        try:
            invite = Invite.objects.select_related("test").get(pk=invite_id)
        except Invite.DoesNotExist:
            continue
        if not send_invite_email(invite, job_title):
            invite.status = Invite.Status.EXPIRED
            invite.save(update_fields=["status"])
            Application.objects.filter(pk=app_id).update(
                status=Application.Status.SCREENED_IN
            )


class UpdatePreferredAnswersView(APIView):
    """HR updates the preferred answers for a job's screening questions."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            job = JobPost.objects.get(pk=pk, created_by=request.user)
        except JobPost.DoesNotExist:
            return Response({"success": False, "error": "Job not found."}, status=404)

        preferred = request.data.get("preferred_answers", {})
        job.preferred_answers = preferred
        job.save(update_fields=["preferred_answers"])

        return success(JobPostSerializer(job).data)
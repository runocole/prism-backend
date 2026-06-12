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

from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser

from .models import JobPost, Application, Blacklist
from .serializers import (
    JobPostSerializer,
    ApplicationSerializer,
    PublicApplicationSerializer,
    BlacklistSerializer,
)
from .screening import run_screening


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
    Runs blacklist → CV → MCQ pipeline on each.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            job = JobPost.objects.get(pk=pk, created_by=request.user)
        except JobPost.DoesNotExist:
            return Response({"success": False, "error": "Job not found."}, status=404)

        # Only screen pending applications
        pending = job.applications.filter(status=Application.Status.PENDING)

        if not pending.exists():
            return Response(
                {"success": False, "error": "No pending applications to screen."},
                status=400,
            )

        results = {
            "screened_in": 0,
            "screened_out": 0,
            "blacklisted": 0,
            "total": pending.count(),
        }

        for application in pending:
            result = run_screening(application)
            if result["blacklisted"]:
                results["blacklisted"] += 1
            elif application.status == Application.Status.SCREENED_IN:
                results["screened_in"] += 1
            else:
                results["screened_out"] += 1

        return success(results)


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
    Accepts test_id, creates a unique Invite per candidate, emails each their link.
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
        from django.core.mail import send_mail
        from django.conf import settings
        from datetime import timedelta
        from django.utils import timezone

        try:
            test = Test.objects.get(pk=test_id)
        except Test.DoesNotExist:
            return Response({"success": False, "error": "Test not found."}, status=404)

        screened_in = job.applications.filter(status=Application.Status.SCREENED_IN)

        if not screened_in.exists():
            return Response(
                {"success": False, "error": "No screened-in candidates to email."},
                status=400,
            )

        frontend_url = getattr(settings, "FRONTEND_URL", "https://screening.oticgs.com")
        duration_mins = test.duration_mins
        sent = 0

        for app in screened_in:
            try:
                # Create a unique invite for this candidate
                invite = Invite.objects.create(
                    test=test,
                    candidate_name=f"{app.first_name} {app.last_name}".strip(),
                    candidate_email=app.email,
                    created_by=request.user,
                    expires_at=timezone.now() + timedelta(hours=48),
                )
                exam_link = f"{frontend_url}/c/{invite.token}"

                send_mail(
                    subject=f"APPLICATION ASSESSMENT — {job.title}",
                    message=(
                        f"Dear {app.first_name},\n\n"
                        f"You have been invited to take an assessment for the "
                        f"{job.title} position at OTIC Geosystems.\n\n"
                        f"Please complete your assessment using the link below:\n"
                        f"{exam_link}\n\n"
                        f"IMPORTANT DETAILS:\n"
                        f"- This link is unique to you. Do not share it.\n"
                        f"- The assessment must be completed within 48 hours of receiving this email.\n"
                        f"- The assessment duration is {duration_mins} minutes once started.\n"
                        f"- Ensure you are in a quiet environment with a stable internet connection.\n"
                        f"- Your webcam will be required throughout the assessment.\n\n"
                        f"If you have any issues, reply to this email.\n\n"
                        f"Best regards,\nOTIC Recruitment Team\nadmin@oticgs.com"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[app.email],
                    fail_silently=False,
                )
                app.status = Application.Status.INVITED
                app.save(update_fields=["status"])
                sent += 1
            except Exception as e:
                print(f"Failed to send invite to {app.email}: {e}")
                pass

        return success({"sent": sent, "total": screened_in.count()})


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
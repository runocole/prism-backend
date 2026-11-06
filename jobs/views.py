"""
jobs/views.py
──────────────
HR endpoints (JWT required):
  GET/POST   /api/v1/jobs/                        — list / create job posts
  GET/PATCH  /api/v1/jobs/<id>/                   — retrieve / update job post
  POST       /api/v1/jobs/<id>/open/              — publish job post
  POST       /api/v1/jobs/<id>/close/             — close job post
  GET        /api/v1/jobs/<id>/applications/      — list applications for a job

  POST       /api/v1/jobs/<id>/screen/            — screen pending applications (background)
  POST       /api/v1/jobs/<id>/import-cvs/        — import CV files, email screening links
  GET        /api/v1/jobs/imports/<uuid>/         — import progress
  POST       /api/v1/jobs/<id>/remind-awaiting/   — re-send screening links

Links (job links / screening links, custom questions):
  GET/POST   /api/v1/jobs/links/
  GET/PATCH/DELETE /api/v1/jobs/links/<id>/
  GET        /api/v1/jobs/links/standard-questions/

Public endpoints (no auth):
  GET        /api/v1/jobs/apply/<slug>/[?t=token] — form for a link
  POST       /api/v1/jobs/apply/<slug>/           — submit it

Blacklist:
  GET/POST   /api/v1/jobs/blacklist/              — list / add to blacklist
  DELETE     /api/v1/jobs/blacklist/<id>/         — remove from blacklist
"""

import json
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import default_storage

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from config.background import run_in_background

from .blacklist_import import BlacklistFileError, import_blacklist
from .imports import process_cv_import, send_form_email
from .models import JobPost, JobLink, Application, Blacklist, CvImport
from .questions import STANDARD_QUESTIONS, clean_answers, public_questions
from .serializers import (
    JobPostSerializer,
    ApplicationSerializer,
    PublicApplicationSerializer,
    BlacklistSerializer,
    CvImportSerializer,
    JobLinkSerializer,
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
        jobs = JobPost.objects.all()
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
            return JobPost.objects.get(pk=pk)
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
            job = JobPost.objects.get(pk=pk)
        except JobPost.DoesNotExist:
            return Response({"success": False, "error": "Job not found."}, status=404)
        job.status = JobPost.Status.OPEN
        job.save(update_fields=["status"])
        return success(JobPostSerializer(job).data)


class CloseJobView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            job = JobPost.objects.get(pk=pk)
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
            job = JobPost.objects.get(pk=pk)
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
    Public form behind every link — no login.
      GET  /api/v1/jobs/apply/<slug>/[?t=<token>]  → job + link + questions
      POST /api/v1/jobs/apply/<slug>/              → submit

    Job link (kind=apply): personal details + CV + cover letter + answers.
    Screening link (kind=screening): imported candidates only, identified by
    their personal token (t); they just answer the questions.
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    NOT_AVAILABLE = "This form is not available."

    def _get_link(self, slug):
        try:
            link = JobLink.objects.select_related("job").get(slug=slug, is_active=True)
        except JobLink.DoesNotExist:
            return None
        job_status = link.job.status
        if link.kind == JobLink.Kind.APPLY and job_status != JobPost.Status.OPEN:
            return None
        if job_status == JobPost.Status.CLOSED:
            return None
        return link

    def _get_invitee(self, link, token):
        try:
            token = uuid.UUID(str(token))
        except (TypeError, ValueError):
            return None
        return Application.objects.filter(form_token=token, job=link.job).first()

    def get(self, request, slug):
        link = self._get_link(slug)
        if not link:
            return Response({"success": False, "error": self.NOT_AVAILABLE}, status=404)

        data = {
            "title": link.job.title,
            "department": link.job.department,
            "description": link.job.description,
            "requirements": link.job.requirements,
            "company": settings.COMPANY_NAME,
            "kind": link.kind,
            "intro": link.intro,
            "questions": public_questions(link.questions or []),
            "candidate": None,
        }
        if link.kind == JobLink.Kind.SCREENING:
            invitee = self._get_invitee(link, request.query_params.get("t"))
            if not invitee:
                return Response(
                    {"success": False, "error": "This link is personal. Please use the link from your email."},
                    status=404,
                )
            data["candidate"] = {
                "first_name": invitee.first_name,
                "completed": invitee.status != Application.Status.AWAITING_FORM,
            }
        return success(data)

    def post(self, request, slug):
        link = self._get_link(slug)
        if not link:
            return Response({"success": False, "error": self.NOT_AVAILABLE}, status=404)

        raw_answers = request.data.get("screening_answers") or {}
        if isinstance(raw_answers, str):
            try:
                raw_answers = json.loads(raw_answers)
            except ValueError:
                raw_answers = {}
        answers, errors = clean_answers(link.questions or [], raw_answers)
        if errors:
            return Response({"success": False, "error": errors[0], "errors": errors}, status=400)

        now = timezone.now()

        if link.kind == JobLink.Kind.SCREENING:
            invitee = self._get_invitee(link, request.data.get("token"))
            if not invitee:
                return Response({"success": False, "error": "This link is personal. Please use the link from your email."}, status=404)
            if invitee.status != Application.Status.AWAITING_FORM:
                return Response({"success": False, "error": "You have already submitted your answers. Thank you!"}, status=400)
            invitee.screening_answers = answers
            invitee.status = Application.Status.PENDING
            invitee.form_completed_at = now
            invitee.link = link
            invitee.save(update_fields=["screening_answers", "status", "form_completed_at", "link"])
            return success({"message": "Answers submitted.", "id": invitee.id}, status.HTTP_201_CREATED)

        email = (request.data.get("email") or "").strip()
        if Application.objects.filter(job=link.job, email__iexact=email).exists():
            return Response(
                {"success": False, "error": "You have already applied for this position."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = PublicApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = serializer.save(
            job=link.job,
            link=link,
            source=Application.Source.APPLY,
            status=Application.Status.PENDING,
            screening_answers=answers,
            form_completed_at=now,
        )
        return success(
            {"message": "Application submitted successfully.", "id": application.id},
            status.HTTP_201_CREATED,
        )


# ── HR: Links (job links / screening links with custom questions) ────────────

class JobLinkListCreateView(APIView):
    """
    GET  /api/v1/jobs/links/[?job=<id>]  — all links
    POST /api/v1/jobs/links/             — create; creating a Job link for a
                                            draft job also opens the job ("Post job")
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        links = JobLink.objects.select_related("job")
        job_id = request.query_params.get("job")
        if job_id:
            links = links.filter(job_id=job_id)
        return success(JobLinkSerializer(links, many=True).data)

    def post(self, request):
        serializer = JobLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = serializer.save(created_by=request.user)
        if link.kind == JobLink.Kind.APPLY and link.job.status == JobPost.Status.DRAFT:
            link.job.status = JobPost.Status.OPEN
            link.job.save(update_fields=["status"])
        return success(JobLinkSerializer(link).data, status.HTTP_201_CREATED)


class JobLinkDetailView(APIView):
    """GET / PATCH / DELETE /api/v1/jobs/links/<id>/ — PATCH also saves preferred answers."""
    permission_classes = [IsAuthenticated]

    def _get(self, pk):
        return JobLink.objects.select_related("job").filter(pk=pk).first()

    def get(self, request, pk):
        link = self._get(pk)
        if not link:
            return Response({"success": False, "error": "Link not found."}, status=404)
        return success(JobLinkSerializer(link).data)

    def patch(self, request, pk):
        link = self._get(pk)
        if not link:
            return Response({"success": False, "error": "Link not found."}, status=404)
        serializer = JobLinkSerializer(link, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success(serializer.data)

    def delete(self, request, pk):
        link = self._get(pk)
        if not link:
            return Response({"success": False, "error": "Link not found."}, status=404)
        if link.applications.exists():
            link.is_active = False
            link.save(update_fields=["is_active"])
            return success("This link already has responses, so it was switched off instead of deleted.")
        link.delete()
        return success("Link deleted.")


class StandardQuestionsView(APIView):
    """GET /api/v1/jobs/links/standard-questions/ — starter questions for the builder."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return success(STANDARD_QUESTIONS)


# ── HR: Import CVs ───────────────────────────────────────────────────────────

class ImportCvsView(APIView):
    """
    POST /api/v1/jobs/<id>/import-cvs/  (multipart: files=<many>, link=<screening link id>)
    Reads each CV, creates an "awaiting form" application and emails the
    candidate their personal screening link — in the background.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    MAX_FILES = 300
    MAX_BYTES = 10 * 1024 * 1024

    def post(self, request, pk):
        job = JobPost.objects.filter(pk=pk).first()
        if not job:
            return Response({"success": False, "error": "Job not found."}, status=404)
        if job.status == JobPost.Status.CLOSED:
            return Response({"success": False, "error": "This job is closed. Reopen it first."}, status=400)

        link = JobLink.objects.filter(
            pk=request.data.get("link"), job=job, kind=JobLink.Kind.SCREENING
        ).first()
        if not link:
            return Response({"success": False, "error": "Choose a screening link for this job."}, status=400)
        if not link.is_active:
            return Response({"success": False, "error": "That screening link is switched off."}, status=400)

        files = request.FILES.getlist("files")
        if not files:
            return Response({"success": False, "error": "No files uploaded."}, status=400)
        if len(files) > self.MAX_FILES:
            return Response({"success": False, "error": f"Upload at most {self.MAX_FILES} CVs at a time."}, status=400)

        batch = CvImport.objects.create(job=job, link=link, created_by=request.user)
        stored, skipped = [], []
        for f in files:
            if not f.name.lower().endswith((".pdf", ".docx")):
                skipped.append({"file": f.name, "reason": "Not a PDF or Word (.docx) file"})
            elif f.size > self.MAX_BYTES:
                skipped.append({"file": f.name, "reason": "Larger than 10 MB"})
            else:
                path = default_storage.save(f"imports/{batch.id}/{f.name}", f)
                stored.append((f.name, path))

        batch.total = len(files)
        batch.processed = len(skipped)
        batch.problems = skipped
        if not stored:
            batch.status = CvImport.Status.DONE
        batch.save()

        if stored:
            run_in_background(process_cv_import, batch.id, stored)
        return success(CvImportSerializer(batch).data, status.HTTP_202_ACCEPTED)


class CvImportStatusView(APIView):
    """GET /api/v1/jobs/imports/<uuid>/ — poll an import's progress."""
    permission_classes = [IsAuthenticated]

    def get(self, request, import_id):
        batch = CvImport.objects.filter(pk=import_id).first()
        if not batch:
            return Response({"success": False, "error": "Import not found."}, status=404)
        return success(CvImportSerializer(batch).data)


class ResendFormView(APIView):
    """POST /api/v1/jobs/<id>/applications/<app_id>/resend-form/ — email the screening link again."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, app_id):
        app = Application.objects.select_related("job", "link").filter(
            pk=app_id, job_id=pk, status=Application.Status.AWAITING_FORM
        ).first()
        if not app or not app.link or not app.form_token:
            return Response({"success": False, "error": "This candidate isn't waiting for a screening link."}, status=404)
        if not send_form_email(app):
            return Response({"success": False, "error": "The email failed to send. Check the email settings and try again."}, status=502)
        return success({"form_sent_at": app.form_sent_at})


class RemindAwaitingView(APIView):
    """POST /api/v1/jobs/<id>/remind-awaiting/ — re-email everyone who hasn't filled their link."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        ids = list(
            Application.objects.filter(
                job_id=pk, status=Application.Status.AWAITING_FORM,
                link__isnull=False, form_token__isnull=False,
            ).values_list("id", flat=True)
        )
        if not ids:
            return Response({"success": False, "error": "Nobody is waiting on a screening link."}, status=400)
        run_in_background(_remind, ids)
        return success({"queued": len(ids)}, status.HTTP_202_ACCEPTED)


def _remind(ids):
    for app in Application.objects.select_related("job", "link").filter(pk__in=ids):
        send_form_email(app)


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


class BlacklistImportView(APIView):
    """POST /api/v1/jobs/blacklist/import/ (multipart: file=<.xlsx or .csv>)"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        f = request.FILES.get("file")
        if not f:
            return Response({"success": False, "error": "No file uploaded."}, status=400)
        if f.size > 5 * 1024 * 1024:
            return Response({"success": False, "error": "File is too large (max 5 MB)."}, status=400)
        try:
            result = import_blacklist(f.read(), f.name, added_by=request.user)
        except BlacklistFileError as exc:
            return Response({"success": False, "error": str(exc)}, status=400)
        return success(result, status.HTTP_201_CREATED)
    

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
            job = JobPost.objects.get(pk=pk)
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
            job = JobPost.objects.get(pk=pk)
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
                pk=test_id, status=Test.Status.PUBLISHED
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

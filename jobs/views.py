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
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from config.background import run_in_background

from .blacklist_import import BlacklistFileError, import_blacklist
from .imports import process_cv_import, send_form_email
from .models import JobPost, JobLink, Application, Blacklist, CvImport
from .questions import (
    STANDARD_QUESTIONS, QuestionError, clean_answers, public_questions,
    strip_preferences, validate_preferences,
)
from .serializers import (
    JobPostSerializer,
    ApplicationSerializer,
    PublicApplicationSerializer,
    BlacklistSerializer,
    CvImportSerializer,
    JobLinkSerializer,
    clean_job_slug,
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


# ── HR: Post a job, preferences, departments ─────────────────────────────────

class PostJobView(APIView):
    """
    POST /api/v1/jobs/<id>/post/  {"link": <link id>, "slug": "gis-analyst-2026"}
    Choose the questions (a Link) and the public address, and open the job.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        job = JobPost.objects.filter(pk=pk).first()
        if not job:
            return Response({"success": False, "error": "Job not found."}, status=404)
        link = JobLink.objects.filter(pk=request.data.get("link"), is_active=True).first()
        if not link:
            return Response({"success": False, "error": "Choose a link (set of questions)."}, status=400)
        try:
            slug = clean_job_slug(request.data.get("slug") or job.title, job)
        except serializers.ValidationError as exc:
            return Response({"success": False, "error": exc.detail[0]}, status=400)
        job.apply_link = link
        job.slug = slug
        job.status = JobPost.Status.OPEN
        job.save(update_fields=["apply_link", "slug", "status"])
        return success(JobPostSerializer(job).data)


class JobPreferencesView(APIView):
    """PUT /api/v1/jobs/<id>/preferences/  {"preferences": {...}} — HR's preferred answers."""
    permission_classes = [IsAuthenticated]

    def put(self, request, pk):
        job = JobPost.objects.filter(pk=pk).first()
        if not job:
            return Response({"success": False, "error": "Job not found."}, status=404)
        try:
            job.preferences = validate_preferences(request.data.get("preferences") or {})
        except QuestionError as exc:
            return Response({"success": False, "error": str(exc)}, status=400)
        job.save(update_fields=["preferences"])
        return success(JobPostSerializer(job).data)


class DepartmentListView(APIView):
    """GET /api/v1/jobs/departments/ — departments already used, for suggestions."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        names = (
            JobPost.objects.exclude(department="")
            .values_list("department", flat=True).distinct().order_by("department")
        )
        return success(sorted(set(names), key=str.lower))


# ── Public: Application Form ──────────────────────────────────────────────────

class PublicApplicationView(APIView):
    """
    The public page for a job — no login.
      GET  /api/v1/jobs/apply/<job slug>/[?t=<token>]
      POST /api/v1/jobs/apply/<job slug>/

    Without a token: apply to the job (details + CV + cover letter + the
    questions of the job's link). Needs the job to be open.
    With a token (imported candidates, from their email): only answer the
    questions of the link they were sent. Works unless the job is closed.
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    NOT_AVAILABLE = "This form is not available."
    PERSONAL = "This link is personal. Please use the link from your email."

    def _resolve(self, slug, token):
        """Return (job, link, invitee) or None."""
        job = JobPost.objects.select_related("apply_link").filter(slug=slug).first()
        if not job or job.status == JobPost.Status.CLOSED:
            return None
        if token:
            try:
                token = uuid.UUID(str(token))
            except (TypeError, ValueError):
                return None
            invitee = Application.objects.select_related("link").filter(form_token=token, job=job).first()
            if not invitee or not invitee.link:
                return None
            return job, invitee.link, invitee
        if job.status != JobPost.Status.OPEN or not job.apply_link or not job.apply_link.is_active:
            return None
        return job, job.apply_link, None

    def get(self, request, slug):
        token = request.query_params.get("t")
        found = self._resolve(slug, token)
        if not found:
            return Response({"success": False, "error": self.PERSONAL if token else self.NOT_AVAILABLE}, status=404)
        job, link, invitee = found
        return success({
            "title": job.title,
            "department": job.department,
            "description": job.description,
            "requirements": job.requirements,
            "company": settings.COMPANY_NAME,
            "kind": "screening" if invitee else "apply",
            "intro": link.intro,
            "questions": public_questions(link.questions or []),
            "candidate": {
                "first_name": invitee.first_name,
                "completed": invitee.status != Application.Status.AWAITING_FORM,
            } if invitee else None,
        })

    def post(self, request, slug):
        token = request.data.get("token")
        found = self._resolve(slug, token)
        if not found:
            return Response({"success": False, "error": self.PERSONAL if token else self.NOT_AVAILABLE}, status=404)
        job, link, invitee = found

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

        if invitee:
            if invitee.status != Application.Status.AWAITING_FORM:
                return Response({"success": False, "error": "You have already submitted your answers. Thank you!"}, status=400)
            invitee.screening_answers = answers
            invitee.status = Application.Status.PENDING
            invitee.form_completed_at = now
            invitee.save(update_fields=["screening_answers", "status", "form_completed_at"])
            return success({"message": "Answers submitted.", "id": invitee.id}, status.HTTP_201_CREATED)

        email = (request.data.get("email") or "").strip()
        if Application.objects.filter(job=job, email__iexact=email).exists():
            return Response(
                {"success": False, "error": "You have already applied for this position."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = PublicApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = serializer.save(
            job=job,
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


# ── HR: Links (reusable question sets) ───────────────────────────────────────

class JobLinkListCreateView(APIView):
    """
    GET  /api/v1/jobs/links/            — all links
    GET  /api/v1/jobs/links/?job=<id>   — links a job uses (its posted link + links sent to its imports)
    POST /api/v1/jobs/links/            — create {name, intro, questions}
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        links = JobLink.objects.all()
        job_id = request.query_params.get("job")
        if job_id:
            job = JobPost.objects.filter(pk=job_id).first()
            ids = set(Application.objects.filter(job_id=job_id, link__isnull=False).values_list("link_id", flat=True))
            if job and job.apply_link_id:
                ids.add(job.apply_link_id)
            links = links.filter(pk__in=ids)
        return success(JobLinkSerializer(links, many=True).data)

    def post(self, request):
        serializer = JobLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = serializer.save(created_by=request.user)
        return success(JobLinkSerializer(link).data, status.HTTP_201_CREATED)


class JobLinkDetailView(APIView):
    """GET / PATCH / DELETE /api/v1/jobs/links/<id>/"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        link = JobLink.objects.filter(pk=pk).first()
        if not link:
            return Response({"success": False, "error": "Link not found."}, status=404)
        return success(JobLinkSerializer(link).data)

    def patch(self, request, pk):
        link = JobLink.objects.filter(pk=pk).first()
        if not link:
            return Response({"success": False, "error": "Link not found."}, status=404)
        serializer = JobLinkSerializer(link, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success(serializer.data)

    def delete(self, request, pk):
        link = JobLink.objects.filter(pk=pk).first()
        if not link:
            return Response({"success": False, "error": "Link not found."}, status=404)
        if link.applications.exists() or JobPost.objects.filter(apply_link=link).exists():
            link.is_active = False
            link.save(update_fields=["is_active"])
            return success("This link is in use, so it was switched off instead of deleted.")
        link.delete()
        return success("Link deleted.")


class StandardQuestionsView(APIView):
    """GET /api/v1/jobs/links/standard-questions/ — starter questions for the builder."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return success(strip_preferences(STANDARD_QUESTIONS))


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

        link = JobLink.objects.filter(pk=request.data.get("link")).first()
        if not link:
            return Response({"success": False, "error": "Choose which link (questions) to send them."}, status=400)
        if not link.is_active:
            return Response({"success": False, "error": "That link is switched off."}, status=400)

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

"""
assessments/views.py
─────────────────────
All test builder endpoints. HR JWT required for all.

Tests:
  GET    /api/v1/assessments/                    — list all tests
  POST   /api/v1/assessments/                    — create a draft test
  GET    /api/v1/assessments/<id>/               — retrieve test + questions
  PATCH  /api/v1/assessments/<id>/               — update (draft only)
  DELETE /api/v1/assessments/<id>/               — delete (draft only)
  POST   /api/v1/assessments/<id>/publish/       — publish a draft test

Questions:
  POST   /api/v1/assessments/<id>/questions/           — add a question
  PATCH  /api/v1/assessments/<id>/questions/<q_id>/    — edit a question
  DELETE /api/v1/assessments/<id>/questions/<q_id>/    — remove a question

Question import (PDF / Word, read by Claude in the background):
  POST   /api/v1/assessments/parse-pdf/              — upload, returns import_id
  GET    /api/v1/assessments/parse-pdf/<import_id>/  — poll status + questions
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser

from config import ai
from config.background import run_in_background

from .models import Test, Question, QuestionImport
from .serializers import TestSerializer, TestListSerializer, QuestionSerializer
from .tasks import process_question_import


def success(data, code=status.HTTP_200_OK):
    return Response({"success": True, "data": data}, status=code)


class TestListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tests = Test.objects.all()
        return success(TestListSerializer(tests, many=True).data)

    def post(self, request):
        serializer = TestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        test = serializer.save(created_by=request.user, status=Test.Status.DRAFT)
        return success(TestSerializer(test).data, status.HTTP_201_CREATED)


class TestDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_test(self, pk, user):
        try:
            return Test.objects.get(pk=pk)
        except Test.DoesNotExist:
            return None

    def get(self, request, pk):
        test = self._get_test(pk, request.user)
        if not test:
            return Response(
                {"success": False, "error": "Test not found."}, status=404
            )
        return success(TestSerializer(test).data)

    def patch(self, request, pk):
        test = self._get_test(pk, request.user)
        if not test:
            return Response(
                {"success": False, "error": "Test not found."}, status=404
            )
        data = request.data
        if test.status != Test.Status.DRAFT:
            # Published tests: only the name / description can change, not the questions or timing
            data = {k: v for k, v in request.data.items() if k in ("title", "description", "job_role")}
            if not data:
                return Response(
                    {"success": False, "error": "A published test can only be renamed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        serializer = TestSerializer(test, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success(serializer.data)

    def delete(self, request, pk):
        test = self._get_test(pk, request.user)
        if not test:
            return Response(
                {"success": False, "error": "Test not found."}, status=404
            )
        # Deletes its questions, invites, candidates' sessions and answers too.
        from exam_sessions.models import Session
        from exam_sessions.storage import delete_session_files
        for session_id in Session.objects.filter(invite__test=test).values_list("id", flat=True):
            delete_session_files(session_id)
        test.delete()
        return success("Test deleted.")


class PublishTestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        """Publish a draft. Once published it cannot be edited — only archived."""
        try:
            test = Test.objects.get(pk=pk)
        except Test.DoesNotExist:
            return Response(
                {"success": False, "error": "Test not found."}, status=404
            )
        if test.status != Test.Status.DRAFT:
            return Response(
                {"success": False, "error": "Only draft tests can be published."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not test.questions.exists():
            return Response(
                {"success": False, "error": "Add at least one question before publishing."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        test.status = Test.Status.PUBLISHED
        test.save(update_fields=["status"])
        return success(TestSerializer(test).data)


class QuestionListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_draft_test(self, pk, user):
        try:
            return Test.objects.get(pk=pk, status=Test.Status.DRAFT)
        except Test.DoesNotExist:
            return None

    def post(self, request, pk):
        test = self._get_draft_test(pk, request.user)
        if not test:
            return Response(
                {"success": False, "error": "Draft test not found."}, status=404
            )
        # Auto-assign order index after the last existing question
        next_order = test.questions.count()
        serializer = QuestionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question = serializer.save(test=test, order_index=next_order)
        return success(QuestionSerializer(question).data, status.HTTP_201_CREATED)


class QuestionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_question(self, test_pk, q_pk, user):
        try:
            return Question.objects.get(
                pk=q_pk,
                test__pk=test_pk,
                test__status=Test.Status.DRAFT,
            )
        except Question.DoesNotExist:
            return None

    def patch(self, request, pk, q_pk):
        question = self._get_question(pk, q_pk, request.user)
        if not question:
            return Response(
                {"success": False, "error": "Question not found."}, status=404
            )
        serializer = QuestionSerializer(question, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success(serializer.data)

    def delete(self, request, pk, q_pk):
        question = self._get_question(pk, q_pk, request.user)
        if not question:
            return Response(
                {"success": False, "error": "Question not found."}, status=404
            )
        test = question.test
        question.delete()

        # Close the gap so order_index stays 0, 1, 2, ...
        for index, q in enumerate(test.questions.order_by("order_index", "id")):
            if q.order_index != index:
                q.order_index = index
                q.save(update_fields=["order_index"])

        return success("Question deleted.")


class QuestionImageView(APIView):
    """
    POST   /api/v1/assessments/<id>/questions/<q_id>/image/  (multipart: image=<png/jpg/webp/gif>)
    DELETE /api/v1/assessments/<id>/questions/<q_id>/image/
    A diagram / picture shown to candidates under the question (draft tests only).
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    ALLOWED = (".png", ".jpg", ".jpeg", ".webp", ".gif")
    MAX_BYTES = 5 * 1024 * 1024

    def _question(self, pk, q_pk):
        return Question.objects.filter(pk=q_pk, test__pk=pk, test__status=Test.Status.DRAFT).first()

    def post(self, request, pk, q_pk):
        question = self._question(pk, q_pk)
        if not question:
            return Response({"success": False, "error": "Question not found (only draft tests can be changed)."}, status=404)
        image = request.FILES.get("image")
        if not image or not image.name.lower().endswith(self.ALLOWED):
            return Response({"success": False, "error": "Upload a PNG, JPG, WEBP or GIF image."}, status=400)
        if image.size > self.MAX_BYTES:
            return Response({"success": False, "error": "Image is too large (max 5 MB)."}, status=400)
        try:
            from PIL import Image
            Image.open(image).verify()  # rejects files that aren't really images
            image.seek(0)
        except Exception:
            return Response({"success": False, "error": "That file isn't a valid image."}, status=400)
        if question.image:
            question.image.delete(save=False)
        question.image = image
        question.save(update_fields=["image"])
        return success(QuestionSerializer(question).data)

    def delete(self, request, pk, q_pk):
        question = self._question(pk, q_pk)
        if not question:
            return Response({"success": False, "error": "Question not found."}, status=404)
        if question.image:
            question.image.delete(save=False)
            question.save(update_fields=["image"])
        return success(QuestionSerializer(question).data)


class ParseDocumentView(APIView):
    """
    POST /api/v1/assessments/parse-pdf/
    Upload a test paper (PDF or Word .docx). Claude extracts the questions in
    the background; poll ParseDocumentStatusView with the returned import_id.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    MAX_BYTES = 20 * 1024 * 1024

    def post(self, request):
        file = request.FILES.get("file")
        if not file:
            return Response(
                {"success": False, "error": "No file uploaded."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not ai.is_supported(file.name):
            return Response(
                {"success": False, "error": "Upload a PDF or Word (.docx) file."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if file.size > self.MAX_BYTES:
            return Response(
                {"success": False, "error": "File is too large (max 20 MB)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not ai.is_configured():
            return Response(
                {"success": False, "error": "Question import isn't set up yet: ANTHROPIC_API_KEY is missing on the server."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        job = QuestionImport.objects.create(created_by=request.user, filename=file.name)
        run_in_background(process_question_import, job.id, file.read(), file.name)

        return success(
            {"import_id": str(job.id), "status": job.status},
            status.HTTP_202_ACCEPTED,
        )


class ParseDocumentStatusView(APIView):
    """GET /api/v1/assessments/parse-pdf/<import_id>/ — poll an upload's progress."""
    permission_classes = [IsAuthenticated]

    def get(self, request, import_id):
        try:
            job = QuestionImport.objects.get(pk=import_id, created_by=request.user)
        except QuestionImport.DoesNotExist:
            return Response({"success": False, "error": "Import not found."}, status=404)

        return success({
            "import_id": str(job.id),
            "status": job.status,
            "questions": job.questions,
            "error": job.error,
        })

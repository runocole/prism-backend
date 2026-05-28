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
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser

from .models import Test, Question
from .serializers import TestSerializer, TestListSerializer, QuestionSerializer


def success(data, code=status.HTTP_200_OK):
    return Response({"success": True, "data": data}, status=code)


class TestListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tests = Test.objects.filter(created_by=request.user)
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
            return Test.objects.get(pk=pk, created_by=user)
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
        if test.status != Test.Status.DRAFT:
            return Response(
                {"success": False, "error": "Only draft tests can be edited."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = TestSerializer(test, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success(serializer.data)

    def delete(self, request, pk):
        test = self._get_test(pk, request.user)
        if not test:
            return Response(
                {"success": False, "error": "Test not found."}, status=404
            )
        if test.status != Test.Status.DRAFT:
            return Response(
                {"success": False, "error": "Only draft tests can be deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        test.delete()
        return success("Test deleted.")


class PublishTestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        """Publish a draft. Once published it cannot be edited — only archived."""
        try:
            test = Test.objects.get(pk=pk, created_by=request.user)
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
            return Test.objects.get(pk=pk, created_by=user, status=Test.Status.DRAFT)
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
                test__created_by=user,
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
        question

class ParsePdfView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        file = request.FILES.get("file")
        if not file:
            return Response(
                {"success": False, "error": "No file uploaded."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            import pypdf
            reader = pypdf.PdfReader(file)
            text = "\n".join(
                page.extract_text() or "" for page in reader.pages
            )
        except Exception:
            return Response(
                {"success": False, "error": "Could not read PDF."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        questions = self._parse_questions(text)

        if not questions:
            return Response(
                {"success": False, "error": "No questions found in PDF."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"success": True, "data": questions})

    def _parse_questions(self, text: str) -> list:
        import re
        questions = []
        blocks = re.split(r'\n(?=Q?\d+[\.\)])', text.strip())

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            lines = [l.strip() for l in block.split('\n') if l.strip()]
            if not lines:
                continue

            prompt = re.sub(r'^Q?\d+[\.\)]\s*', '', lines[0])
            options = []
            correct_index = 0

            for line in lines[1:]:
                opt_match = re.match(r'^([A-D])[\.\)]\s*(.+)', line)
                if opt_match:
                    options.append(opt_match.group(2))

                answer_match = re.match(r'^[Aa]nswer\s*[:=]\s*([A-D])', line)
                if answer_match:
                    letter = answer_match.group(1).upper()
                    correct_index = ord(letter) - ord('A')

            if options:
                questions.append({
                    "type": "mcq",
                    "prompt": prompt,
                    "options": options,
                    "correct_index": correct_index,
                    "points": 5,
                })
            else:
                questions.append({
                    "type": "short_answer",
                    "prompt": prompt,
                    "points": 5,
                })

        return questions
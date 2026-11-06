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
        import urllib.request
        import json
        import re

        prompt = (
            "You are a test paper parser. Extract all exam questions from the text below.\n\n"
            "Rules:\n"
            "- SKIP all header text, company name, address, instructions, and name/date/phone input fields\n"
            "- For each numbered question, determine if it is mcq (has lettered options a/b/c/d) or short_answer\n"
            "- For MCQ: extract the full question as prompt, all options (without the A/B/C prefix), correct_index (0-based, default 0 if not stated)\n"
            "- For short_answer: extract the full question text as prompt\n"
            "- Split multi-part questions like 5a) 5b) into separate questions, each with its full text\n"
            "- Only add [See diagram] to the prompt if the question explicitly says to refer to a diagram, figure or example table shown above\n"
            "- Always set points to 5\n"
            "- Return ONLY a valid JSON array, no markdown fences, no explanation\n\n"
            "Format:\n"
            "[\n"
            "  {\"type\": \"mcq\", \"prompt\": \"...\", \"options\": [\"...\"], \"correct_index\": 0, \"points\": 5},\n"
            "  {\"type\": \"short_answer\", \"prompt\": \"...\", \"points\": 5}\n"
            "]\n\n"
            "Text:\n" + text
        )

        payload = json.dumps({
            "model": "llama-3.1-8b-instant",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()

        from django.conf import settings
        api_key = getattr(settings, "GROK_API_KEY", "")

        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            import subprocess
            curl_result = subprocess.run([
                "curl", "-s", "-X", "POST",
                "https://api.groq.com/openai/v1/chat/completions",
                "-H", "Content-Type: application/json",
                "-H", f"Authorization: Bearer {api_key}",
                "-d", payload.decode()
            ], capture_output=True, text=True, timeout=60)
            result = json.loads(curl_result.stdout)
            raw = result["choices"][0]["message"]["content"].strip()
            raw = re.sub(r"^```json|^```|```$", "", raw, flags=re.MULTILINE).strip()
            parsed = json.loads(raw)
            # Normalize question_type -> type
            for q in parsed:
                if "question_type" in q and "type" not in q:
                    q["type"] = q.pop("question_type")
                if "correct_index" not in q:
                    q["correct_index"] = 0
            return parsed
        except Exception as e:
            return []


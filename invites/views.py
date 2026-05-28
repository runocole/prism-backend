"""
invites/views.py
─────────────────
HR endpoints (JWT required):
  GET  /api/v1/invites/                  — list all invites
  POST /api/v1/invites/                  — create a single invite
  POST /api/v1/invites/batch/            — create batch invites for one test
  GET  /api/v1/invites/<id>/             — retrieve a single invite
  POST /api/v1/invites/<id>/revoke/      — revoke a pending invite

Candidate endpoint (public — token is the credential):
  GET  /api/v1/invites/validate/<token>/ — validate token + return test info

Note: Email sending via Celery is disabled for local development.
Invite links can be copied manually from the Invites page.
"""

from datetime import timedelta
from django.utils import timezone
from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from assessments.models import Test
from .models import Invite
from .serializers import (
    InviteSerializer,
    SingleInviteCreateSerializer,
    BatchInviteCreateSerializer,
)


def success(data, code=status.HTTP_200_OK):
    return Response({"success": True, "data": data}, status=code)


def default_expiry():
    hours = getattr(settings, "INVITE_EXPIRY_HOURS", 72)
    return timezone.now() + timedelta(hours=hours)


class InviteListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """List invites. Optional ?test_id= query param to filter by test."""
        invites = Invite.objects.filter(
            created_by=request.user
        ).select_related("test")

        test_id = request.query_params.get("test_id")
        if test_id:
            invites = invites.filter(test_id=test_id)

        return success(
            InviteSerializer(invites, many=True, context={"request": request}).data
        )

    def post(self, request):
        """Create a single invite."""
        serializer = SingleInviteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            test = Test.objects.get(
                pk=d["test_id"],
                created_by=request.user,
                status=Test.Status.PUBLISHED,
            )
        except Test.DoesNotExist:
            return Response(
                {"success": False, "error": "Published test not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Expire any existing pending invite for this candidate + test
        Invite.objects.filter(
            test=test,
            candidate_email=d["candidate_email"],
            status=Invite.Status.PENDING,
        ).update(status=Invite.Status.EXPIRED)

        invite = Invite.objects.create(
            test=test,
            candidate_name=d["candidate_name"],
            candidate_email=d["candidate_email"],
            expires_at=d.get("expires_at") or default_expiry(),
            created_by=request.user,
        )

        # TODO: send_invite_email.delay(invite.id) — enable when Redis is running

        return success(
            InviteSerializer(invite, context={"request": request}).data,
            status.HTTP_201_CREATED,
        )


class BatchInviteCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Create invites for multiple candidates in one request."""
        serializer = BatchInviteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            test = Test.objects.get(
                pk=d["test_id"],
                created_by=request.user,
                status=Test.Status.PUBLISHED,
            )
        except Test.DoesNotExist:
            return Response(
                {"success": False, "error": "Published test not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        expires_at = d.get("expires_at")
        created_invites = []

        for candidate in d["candidates"]:
            Invite.objects.filter(
                test=test,
                candidate_email=candidate["email"],
                status=Invite.Status.PENDING,
            ).update(status=Invite.Status.EXPIRED)

            create_kwargs = {}
            if expires_at:
                create_kwargs["expires_at"] = expires_at

            invite = Invite.objects.create(
                test=test,
                candidate_name=candidate["name"],
                candidate_email=candidate["email"],
                created_by=request.user,
                **create_kwargs,
            )
            created_invites.append(invite)
            # TODO: send_invite_email.delay(invite.id) — enable when Redis is running

        return success(
            {
                "created": len(created_invites),
                "invites": InviteSerializer(
                    created_invites, many=True, context={"request": request}
                ).data,
            },
            status.HTTP_201_CREATED,
        )


class InviteDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            invite = Invite.objects.get(pk=pk, created_by=request.user)
        except Invite.DoesNotExist:
            return Response(
                {"success": False, "error": "Invite not found."}, status=404
            )
        return success(
            InviteSerializer(invite, context={"request": request}).data
        )


class RevokeInviteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        """Revoke a pending invite so the link immediately stops working."""
        try:
            invite = Invite.objects.get(pk=pk, created_by=request.user)
        except Invite.DoesNotExist:
            return Response(
                {"success": False, "error": "Invite not found."}, status=404
            )

        if invite.status != Invite.Status.PENDING:
            return Response(
                {"success": False, "error": "Only pending invites can be revoked."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        invite.status = Invite.Status.EXPIRED
        invite.save(update_fields=["status"])
        return success("Invite revoked.")


class ValidateInviteView(APIView):
    """
    Public endpoint — no JWT needed.
    Candidate uses this to verify their token and get test info
    before the instructions page loads.
    """
    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            invite = Invite.objects.select_related("test").get(token=token)
        except Invite.DoesNotExist:
            return Response(
                {"success": False, "error": "Invalid invite link."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if invite.status not in (Invite.Status.PENDING, Invite.Status.ACTIVE):
            return Response(
                {"success": False, "error": "This invite has expired or already been used."},
                status=status.HTTP_410_GONE,
            )

        if timezone.now() > invite.expires_at:
            return Response(
                {"success": False, "error": "This invite link has expired."},
                status=status.HTTP_410_GONE,
            )

        return success({
            "candidate_name": invite.candidate_name,
            "candidate_email": invite.candidate_email,
            "test": {
                "title": invite.test.title,
                "description": invite.test.description,
                "duration_mins": invite.test.duration_mins,
                "question_count": invite.test.questions.count(),
            },
            "expires_at": invite.expires_at,
        })
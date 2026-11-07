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
from .emails import send_invite_emails
from config.background import run_in_background
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


def get_or_refresh_invite(test, name, email, expires_at, user, job=None, opens_at=None):
    """
    Sending a test again to the same person re-sends their EXISTING link
    (with the new expiry) instead of cancelling it, so every email they
    received keeps working. A new invite is only made if they have none
    that's still usable.
    """
    existing = (
        Invite.objects.filter(
            test=test, candidate_email__iexact=email,
            status__in=[Invite.Status.PENDING, Invite.Status.ACTIVE],
        )
        .order_by("-created_at")
        .first()
    )
    if existing:
        existing.candidate_name = name
        existing.expires_at = max(existing.expires_at, expires_at)
        if job is not None:
            existing.job = job
        if opens_at is not None or existing.status == Invite.Status.PENDING:
            existing.opens_at = opens_at
        existing.save(update_fields=["candidate_name", "expires_at", "job", "opens_at"])
        return existing
    return Invite.objects.create(
        test=test, candidate_name=name, candidate_email=email,
        expires_at=expires_at, created_by=user, job=job, opens_at=opens_at,
    )


def _job_from(d):
    from jobs.models import JobPost
    return JobPost.objects.filter(pk=d.get("job_id")).first() if d.get("job_id") else None


class InviteListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """List invites. Optional ?test_id= query param to filter by test."""
        invites = Invite.objects.select_related("test")

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
            )
        except Test.DoesNotExist:
            return Response(
                {"success": False, "error": "Test not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        invite = get_or_refresh_invite(
            test, d["candidate_name"], d["candidate_email"],
            d.get("expires_at") or default_expiry(), request.user, job=_job_from(d),
            opens_at=d.get("opens_at"),
        )

        run_in_background(send_invite_emails, [invite.id])

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
            )
        except Test.DoesNotExist:
            return Response(
                {"success": False, "error": "Test not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        expires_at = d.get("expires_at")
        created_invites = []

        job = _job_from(d)
        for candidate in d["candidates"]:
            invite = get_or_refresh_invite(
                test, candidate["name"], candidate["email"],
                expires_at or default_expiry(), request.user, job=job,
                opens_at=d.get("opens_at"),
            )
            created_invites.append(invite)

        run_in_background(send_invite_emails, [i.id for i in created_invites])

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
            invite = Invite.objects.get(pk=pk)
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
            invite = Invite.objects.get(pk=pk)
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
        def gone(code, message, http=status.HTTP_410_GONE):
            # "code" lets the page show the right message instead of a bare 404
            return Response({"success": False, "code": code, "error": message}, status=http)

        try:
            invite = Invite.objects.select_related("test").get(token=token)
        except Invite.DoesNotExist:
            return gone("invalid", "This test link isn't valid. Please check you copied the whole link.",
                        status.HTTP_404_NOT_FOUND)

        if invite.status == Invite.Status.SUBMITTED:
            return gone("submitted", "You have already completed this assessment. Thank you!")

        if invite.status == Invite.Status.EXPIRED:
            newer = Invite.objects.filter(
                test=invite.test, candidate_email__iexact=invite.candidate_email,
                created_at__gt=invite.created_at,
            ).exists()
            if newer:
                return gone("replaced", "This link was replaced by a newer one. Please use the most recent email we sent you.")
            return gone("expired", "This test link has expired. Please contact the recruitment team.")

        if timezone.now() > invite.expires_at:
            return gone("expired", "This test link has expired. Please contact the recruitment team.")

        if invite.opens_at and timezone.now() < invite.opens_at:
            from .emails import when
            return Response({
                "success": False, "code": "not_open", "opens_at": invite.opens_at,
                "error": f"Your assessment opens on {when(invite.opens_at)}. Please come back then.",
            }, status=status.HTTP_403_FORBIDDEN)

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
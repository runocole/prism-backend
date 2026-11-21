"""
exam_sessions/authentication.py
─────────────────────────────────
Custom DRF authentication for candidates.

Candidates don't have accounts. Their credential is the invite token
sent in the X-Invite-Token header (or 'token' in the request body).

After authentication:
  request.user → the Invite object
  request.auth → the Invite object

Views that use this class should set permission_classes = [AllowAny]
since there is no User object — just check request.auth is not None.
"""

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.utils import timezone
from invites.models import Invite


class InviteTokenAuthentication(BaseAuthentication):

    def authenticate(self, request):
        # Accept token from header (preferred) or request body
        token = (
            request.headers.get("X-Invite-Token")
            or request.data.get("token")
            or request.query_params.get("token")
        )

        if not token:
            return None  # No token — let other authenticators try

        try:
            invite = Invite.objects.select_related("test").get(token=token)
        except (Invite.DoesNotExist, ValueError):
            raise AuthenticationFailed("Invalid invite token.")

        # Someone already in the waiting room or the test keeps going after the
        # link's expiry time: expiry only stops people who haven't started.
        # (Revoking only works on unused invites, so an EXPIRED invite with a
        # live session was expired by time, never by HR.)
        from .models import Session
        inside = Session.objects.filter(
            invite=invite, status__in=[Session.Status.WAITING, Session.Status.IN_PROGRESS],
        ).exists()
        if inside:
            if invite.status == Invite.Status.EXPIRED:
                invite.status = Invite.Status.ACTIVE
                invite.save(update_fields=["status"])
            return (invite, invite)

        # The final answer save may land just after the test closed: allow only the
        # draft endpoint, for 2 minutes, before anyone has reviewed it.
        if request.path.rstrip("/").endswith("/draft"):
            from datetime import timedelta
            just_ended = Session.objects.filter(
                invite=invite, status__in=[Session.Status.SUBMITTED, Session.Status.TIMED_OUT],
                submitted_at__gte=timezone.now() - timedelta(seconds=120), decision="",
            ).exists()
            if just_ended:
                return (invite, invite)

        # Only pending or active invites can be used
        if invite.status not in (Invite.Status.PENDING, Invite.Status.ACTIVE):
            raise AuthenticationFailed(
                "This invite has already been submitted or has expired."
            )

        # Check expiry and update status if needed
        if timezone.now() > invite.expires_at:
            invite.status = Invite.Status.EXPIRED
            invite.save(update_fields=["status"])
            raise AuthenticationFailed("This invite link has expired.")

        # Return (user, auth) — invite acts as both
        return (invite, invite)
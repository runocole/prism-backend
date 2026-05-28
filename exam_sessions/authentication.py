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
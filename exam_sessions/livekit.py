"""
exam_sessions/livekit.py
─────────────────────────
Access tokens for live video on the LiveKit server (shared with Workforce).

Each exam session gets its own private room, "pas-session-<id>":
  - the candidate publishes camera + microphone and hears HR
  - HR watches, can listen, and can publish their microphone to talk

A LiveKit token is a short-lived JWT signed with the API secret (HS256), so
PyJWT (already installed for logins) is enough — no extra package.
"""

import time

import jwt
from django.conf import settings


def is_configured() -> bool:
    return bool(settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET)


def room_name(session_id: int) -> str:
    return f"pas-session-{session_id}"


def make_token(identity: str, name: str, room: str, ttl_seconds: int = 4 * 3600) -> str:
    now = int(time.time())
    claims = {
        "iss": settings.LIVEKIT_API_KEY,
        "sub": identity,
        "name": name,
        "nbf": now - 10,
        "exp": now + ttl_seconds,
        "video": {
            "room": room,
            "roomJoin": True,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    return jwt.encode(claims, settings.LIVEKIT_API_SECRET, algorithm="HS256")

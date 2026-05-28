"""
config/asgi.py
───────────────
ASGI entry point — kept for future WebSocket support (e.g. live proctoring alerts).
Not used in v1.
"""

import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
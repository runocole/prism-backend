"""
config/background.py
─────────────────────
Run slow work (AI screening, sending emails, reading documents) in a
background thread so the HTTP request returns immediately instead of
hitting the Nginx/Gunicorn timeout (the old 504 errors).

No Redis/Celery needed. Trade-off: if the Gunicorn worker restarts while a
job is running, that job stops. Every caller is written so a stopped job
can simply be started again (e.g. "Screen applicants" picks up stale ones).
"""

import logging
import threading

from django.db import connection

logger = logging.getLogger(__name__)


def run_in_background(fn, *args, **kwargs):
    """Start fn(*args, **kwargs) in a daemon thread and return immediately."""

    def runner():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("Background job %s failed", getattr(fn, "__name__", fn))
        finally:
            # Each thread gets its own DB connection — close it when done.
            connection.close()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread

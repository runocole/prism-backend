"""
config/celery.py
─────────────────
Celery application setup.
Tasks are auto-discovered from each app's tasks.py.

To run locally:
  celery -A config worker --loglevel=info
"""

import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("proctorcore")

# Pull all celery config from Django settings (keys prefixed with CELERY_)
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in every installed app
app.autodiscover_tasks()
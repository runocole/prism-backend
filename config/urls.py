"""
config/urls.py
──────────────
All API routes live under /api/v1/.
Versioning in the URL makes future breaking changes easy to manage.
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),

    # Auth — login, token refresh, logout
    path("api/v1/auth/", include("users.urls")),

    # Test builder — HR creates/manages assessments and questions
    path("api/v1/assessments/", include("assessments.urls")),

    # Invites — HR sends; candidates consume via token
    path("api/v1/invites/", include("invites.urls")),

    # Sessions — candidate takes test, violations logged, recordings uploaded
    path("api/v1/sessions/", include("exam_sessions.urls")),
    
    #Jobs - Application management
    path("api/v1/jobs/", include("jobs.urls")),
]

# Serve media files locally during development only
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
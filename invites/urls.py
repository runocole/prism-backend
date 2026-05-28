"""
invites/urls.py
"""

from django.urls import path
from .views import (
    InviteListCreateView,
    BatchInviteCreateView,
    InviteDetailView,
    RevokeInviteView,
    ValidateInviteView,
)

urlpatterns = [
    path("", InviteListCreateView.as_view(), name="invite-list-create"),
    path("batch/", BatchInviteCreateView.as_view(), name="invite-batch"),
    path("<int:pk>/", InviteDetailView.as_view(), name="invite-detail"),
    path("<int:pk>/revoke/", RevokeInviteView.as_view(), name="invite-revoke"),
    # Public — candidate validates their token before starting
    path("validate/<uuid:token>/", ValidateInviteView.as_view(), name="invite-validate"),
]
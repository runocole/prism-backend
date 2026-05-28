from django.urls import path
from .views import (
    JobPostListCreateView,
    JobPostDetailView,
    OpenJobView,
    CloseJobView,
    JobApplicationsView,
    PublicApplicationView,
    BlacklistListCreateView,
    BlacklistDetailView,
    ScreenJobView,
    ManualScreenInView,
    BatchEmailView,
    UpdatePreferredAnswersView,
)

urlpatterns = [
    # HR — job management
    path("", JobPostListCreateView.as_view(), name="job-list-create"),
    path("<int:pk>/", JobPostDetailView.as_view(), name="job-detail"),
    path("<int:pk>/open/", OpenJobView.as_view(), name="job-open"),
    path("<int:pk>/close/", CloseJobView.as_view(), name="job-close"),
    path("<int:pk>/applications/", JobApplicationsView.as_view(), name="job-applications"),
    path("<int:pk>/screen/", ScreenJobView.as_view(), name="job-screen"),
    path("<int:pk>/applications/<int:app_id>/screen-in/", ManualScreenInView.as_view(), name="manual-screen-in"),
    path("<int:pk>/batch-email/", BatchEmailView.as_view(), name="batch-email"),
    path("<int:pk>/preferred-answers/", UpdatePreferredAnswersView.as_view(), name="preferred-answers"),

    # Public — application form
    path("apply/<slug:slug>/", PublicApplicationView.as_view(), name="job-apply"),

    # HR — blacklist
    path("blacklist/", BlacklistListCreateView.as_view(), name="blacklist-list-create"),
    path("blacklist/<int:pk>/", BlacklistDetailView.as_view(), name="blacklist-detail"),
]
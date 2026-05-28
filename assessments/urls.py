from django.urls import path
from .views import (
    TestListCreateView,
    TestDetailView,
    PublishTestView,
    QuestionListCreateView,
    QuestionDetailView,
    ParsePdfView,
)

urlpatterns = [
    path("", TestListCreateView.as_view(), name="test-list-create"),
    path("<int:pk>/", TestDetailView.as_view(), name="test-detail"),
    path("<int:pk>/publish/", PublishTestView.as_view(), name="test-publish"),
    path("<int:pk>/questions/", QuestionListCreateView.as_view(), name="question-list-create"),
    path("<int:pk>/questions/<int:q_pk>/", QuestionDetailView.as_view(), name="question-detail"),
    path("parse-pdf/", ParsePdfView.as_view(), name="parse-pdf"),
]
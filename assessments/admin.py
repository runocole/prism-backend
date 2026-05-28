from django.contrib import admin
from .models import Test, Question


class QuestionInline(admin.TabularInline):
    """Show questions directly inside the Test admin page."""
    model = Question
    extra = 0
    fields = ("type", "body", "points", "order_index")


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "duration_mins", "pass_mark_pct", "created_by", "created_at")
    list_filter = ("status",)
    search_fields = ("title",)
    inlines = [QuestionInline]


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("test", "type", "order_index", "points")
    list_filter = ("type",)
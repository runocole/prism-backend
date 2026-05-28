from django.contrib import admin
from .models import Session, Answer, Violation


class AnswerInline(admin.TabularInline):
    model = Answer
    extra = 0
    readonly_fields = ("question", "response", "is_correct", "auto_score")


class ViolationInline(admin.TabularInline):
    model = Violation
    extra = 0
    readonly_fields = ("type", "occurred_at", "snapshot_path")


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("id", "invite", "status", "started_at", "submitted_at")
    list_filter = ("status",)
    inlines = [AnswerInline, ViolationInline]


@admin.register(Violation)
class ViolationAdmin(admin.ModelAdmin):
    list_display = ("session", "type", "occurred_at")
    list_filter = ("type",)
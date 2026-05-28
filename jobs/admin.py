from django.contrib import admin
from .models import JobPost, Application, Blacklist


@admin.register(JobPost)
class JobPostAdmin(admin.ModelAdmin):
    list_display = ("title", "department", "status", "slug", "created_at")
    list_filter = ("status", "department")
    search_fields = ("title", "department")
    readonly_fields = ("slug",)


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("first_name", "last_name", "email", "job", "status", "ai_score", "submitted_at")
    list_filter = ("status",)
    search_fields = ("first_name", "last_name", "email")
    readonly_fields = ("ai_score", "ai_summary", "ai_screened_at", "submitted_at")

@admin.register(Blacklist)
class BlacklistAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "reason", "created_at")
    search_fields = ("name", "email")
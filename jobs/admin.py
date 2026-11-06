from django.contrib import admin
from .models import JobPost, JobLink, CvImport, Application, Blacklist


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


@admin.register(JobLink)
class JobLinkAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "slug", "job", "is_active", "created_at")
    list_filter = ("kind", "is_active")
    search_fields = ("name", "slug", "job__title")


@admin.register(CvImport)
class CvImportAdmin(admin.ModelAdmin):
    list_display = ("job", "status", "total", "created_count", "duplicate_count", "emailed_count", "created_at")
    readonly_fields = [f.name for f in CvImport._meta.fields]

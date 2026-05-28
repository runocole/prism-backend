from django.contrib import admin
from .models import Invite


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = (
        "candidate_email", "candidate_name", "test", "status", "expires_at", "created_at"
    )
    list_filter = ("status",)
    search_fields = ("candidate_email", "candidate_name")
    readonly_fields = ("token",)
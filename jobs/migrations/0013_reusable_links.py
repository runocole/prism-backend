"""
Links become reusable question sets, not tied to a job.

1. Add JobPost.apply_link and JobPost.preferences.
2. For each existing link: if it was a job's "Job link", make it that job's
   apply_link (and copy the link's address onto the job's URL slug); copy the
   preferred answers stored on the link's questions onto the job.
3. Remove JobLink.job / kind / slug.
"""

import django.db.models.deletion
from django.db import migrations, models


def forwards(apps, schema_editor):
    JobLink = apps.get_model("jobs", "JobLink")
    JobPost = apps.get_model("jobs", "JobPost")

    for link in JobLink.objects.select_related("job").order_by("created_at"):
        job = link.job
        prefs = dict(job.preferences or {})
        for q in link.questions or []:
            entry = {
                "preferred": list(q.get("preferred") or []),
                "min": q.get("min"),
                "max": q.get("max"),
            }
            if entry["preferred"] or entry["min"] is not None or entry["max"] is not None:
                prefs.setdefault(q["id"], entry)
            q.pop("preferred", None)
            q.pop("min", None)
            q.pop("max", None)
        link.save(update_fields=["questions"])

        updates = {"preferences": prefs}
        if link.kind == "apply" and job.apply_link_id is None:
            updates["apply_link"] = link
            if not JobPost.objects.filter(slug=link.slug).exclude(pk=job.pk).exists():
                updates["slug"] = link.slug
        JobPost.objects.filter(pk=job.pk).update(**updates)
        # give each link a recognisable name now that it's not grouped under a job
        if link.name in ("Application form", "Job link", "Screening link"):
            JobLink.objects.filter(pk=link.pk).update(name=f"{job.title} · {link.name}")


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0012_move_questions_to_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobpost",
            name="apply_link",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="jobs.joblink"),
        ),
        migrations.AddField(
            model_name="jobpost",
            name="preferences",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.RemoveField(model_name="joblink", name="job"),
        migrations.RemoveField(model_name="joblink", name="kind"),
        migrations.RemoveField(model_name="joblink", name="slug"),
        migrations.AlterField(
            model_name="joblink",
            name="name",
            field=models.CharField(help_text="e.g. 'Entry level' or 'Experienced'", max_length=255),
        ),
    ]

"""
Give every existing job a "Job link" that carries its old fixed screening
questions (as custom questions) and preferred answers. The link uses the
job's existing slug, so URLs already shared (/apply/<slug>) keep working.
Existing applications are attached to that link.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    from jobs.questions import from_legacy

    JobPost = apps.get_model("jobs", "JobPost")
    JobLink = apps.get_model("jobs", "JobLink")
    Application = apps.get_model("jobs", "Application")

    for job in JobPost.objects.all():
        if JobLink.objects.filter(slug=job.slug).exists():
            continue
        link = JobLink.objects.create(
            job=job,
            kind="apply",
            name="Application form",
            slug=job.slug,
            questions=from_legacy(job.screening_questions, job.preferred_answers),
            is_active=True,
            created_by_id=job.created_by_id,
        )
        Application.objects.filter(job=job, link__isnull=True).update(link=link)


def backwards(apps, schema_editor):
    JobLink = apps.get_model("jobs", "JobLink")
    JobLink.objects.filter(name="Application form").delete()


class Migration(migrations.Migration):
    dependencies = [("jobs", "0011_links_imports")]
    operations = [migrations.RunPython(forwards, backwards)]

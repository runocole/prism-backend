from django.test import TestCase

# Create your tests here.


class ScreenOutTests(TestCase):
    def test_screen_out_and_back_in(self):
        from rest_framework.test import APIClient
        from users.models import User
        from .models import Application, JobPost
        client = APIClient()
        client.force_authenticate(User.objects.create_user("hr@example.com", "pw-12345678"))
        job = JobPost.objects.create(title="BDE")
        app = Application.objects.create(job=job, first_name="Ada", last_name="Obi", email="ada@example.com",
                                         status=Application.Status.SCREENED_IN)
        r = client.post(f"/api/v1/jobs/{job.id}/applications/{app.id}/screen-out/")
        self.assertEqual(r.status_code, 200, r.content)
        app.refresh_from_db()
        self.assertEqual(app.status, "screened_out")
        # reversible with Screen in
        client.post(f"/api/v1/jobs/{job.id}/applications/{app.id}/screen-in/")
        app.refresh_from_db()
        self.assertEqual(app.status, "screened_in")
        # can't screen out someone already sent a test
        Application.objects.filter(pk=app.pk).update(status="invited")
        self.assertEqual(client.post(f"/api/v1/jobs/{job.id}/applications/{app.id}/screen-out/").status_code, 400)


class EmailScreenedOutTests(TestCase):
    def test_not_shortlisted_email_sent_once(self):
        from unittest import mock
        from django.core import mail
        from django.test import override_settings
        from rest_framework.test import APIClient
        from users.models import User
        from .emails import send_not_shortlisted_emails
        from .models import Application, JobPost

        client = APIClient()
        client.force_authenticate(User.objects.create_user("hr@example.com", "pw-12345678"))
        job = JobPost.objects.create(title="BDE")
        out = Application.objects.create(job=job, first_name="Ada", last_name="Obi", email="ada@example.com",
                                         status=Application.Status.SCREENED_OUT)
        Application.objects.create(job=job, first_name="In", last_name="Person", email="in@example.com",
                                   status=Application.Status.SCREENED_IN)

        with mock.patch("jobs.views.run_in_background") as bg, \
                override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            r = client.post(f"/api/v1/jobs/{job.id}/email-screened-out/", {}, format="json")
            self.assertEqual(r.status_code, 200, r.content)
            self.assertEqual(r.json()["data"]["application_ids"], [out.id])  # screened-in person not included
            send_not_shortlisted_emails(*bg.call_args.args[1:])
            self.assertEqual(len(mail.outbox), 1)
            self.assertIn("did not meet the", mail.outbox[0].alternatives[0][0])
            out.refresh_from_db()
            self.assertIsNotNone(out.rejection_emailed_at)
            # second click: nobody left to email
            self.assertEqual(client.post(f"/api/v1/jobs/{job.id}/email-screened-out/", {}, format="json").status_code, 400)

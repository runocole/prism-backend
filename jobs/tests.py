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

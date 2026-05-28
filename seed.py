import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from users.models import User

EMAIL = os.environ.get("SEED_EMAIL", "oticgs@gmail.com")
PASSWORD = os.environ.get("SEED_PASSWORD", "Dearfriend_08.")
FIRST_NAME = os.environ.get("SEED_FIRST_NAME", "OTIC")
LAST_NAME = os.environ.get("SEED_LAST_NAME", "Admin")

if User.objects.filter(email=EMAIL).exists():
    print(f"[skip] User '{EMAIL}' already exists.")
else:
    User.objects.create_user(
        email=EMAIL,
        password=PASSWORD,
        first_name=FIRST_NAME,
        last_name=LAST_NAME,
        role="hr",
        is_staff=True,
        is_superuser=True,
    )
    print(f"[ok] Created HR user: {EMAIL}")
    print(f"     Password: {PASSWORD}")
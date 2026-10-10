import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models import Staff


class Command(BaseCommand):
    help = (
        "Create the admin account from DJANGO_SUPERUSER_USERNAME and DJANGO_SUPERUSER_PASSWORD if it doesn't exist. "
        "For hosts with no shell, such as Render's free tier. Never changes an existing account."
    )

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
        if not (username and password):
            self.stdout.write("DJANGO_SUPERUSER_USERNAME or DJANGO_SUPERUSER_PASSWORD not set: no admin account created.")
            return
        if Staff.objects.filter(username=username).exists():
            self.stdout.write(f"Admin account '{username}' already exists: left unchanged.")
            return
        user = Staff(username=username, email=os.environ.get("DJANGO_SUPERUSER_EMAIL", ""))
        try:
            validate_password(password, user)  # the site is public, so no weak admin passwords
        except ValidationError as exc:
            raise CommandError(f"DJANGO_SUPERUSER_PASSWORD rejected: {' '.join(exc.messages)}")
        Staff.objects.create_superuser(username=username, email=user.email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Created admin account '{username}'."))

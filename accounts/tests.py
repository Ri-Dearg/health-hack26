import os
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase

from accounts.models import Staff


class EnsureSuperuserTests(TestCase):
    """Render's free tier has no shell, so build.sh creates the admin account from environment variables."""

    def run_command(self, **env):
        with mock.patch.dict(os.environ, env, clear=False):
            call_command("ensure_superuser", stdout=open(os.devnull, "w"))

    def test_creates_admin_once_and_never_changes_it(self):
        self.run_command(DJANGO_SUPERUSER_USERNAME="rory", DJANGO_SUPERUSER_PASSWORD="a-long-unusual-phrase-41")
        admin = Staff.objects.get(username="rory")
        self.assertTrue(admin.is_superuser and admin.check_password("a-long-unusual-phrase-41"))
        self.run_command(DJANGO_SUPERUSER_USERNAME="rory", DJANGO_SUPERUSER_PASSWORD="another-long-phrase-42")
        admin.refresh_from_db()
        self.assertTrue(admin.check_password("a-long-unusual-phrase-41"))

    def test_skips_without_variables(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            call_command("ensure_superuser", stdout=open(os.devnull, "w"))
        self.assertFalse(Staff.objects.exists())

    def test_rejects_weak_password(self):
        with self.assertRaises(CommandError):
            self.run_command(DJANGO_SUPERUSER_USERNAME="rory", DJANGO_SUPERUSER_PASSWORD="password")
        self.assertFalse(Staff.objects.exists())

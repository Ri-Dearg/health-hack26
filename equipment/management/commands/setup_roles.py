from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

# OT staff can do day-to-day work but not delete records. Admins are superusers.
OT_STAFF_PERMS = {
    "equipment": ["view", "add", "change"],
    "tag": ["view", "add", "change"],
    "loan": ["view", "add", "change"],
    "patient": ["view", "add"],
    "waitlistentry": ["view", "add", "change"],
    "locationlog": ["view", "add"],
    "category": ["view"],
}


class Command(BaseCommand):
    help = "Create or refresh the 'OT staff' group and its permissions. Safe to run repeatedly."

    def handle(self, *args, **options):
        group, _ = Group.objects.get_or_create(name="OT staff")
        codenames = [f"{action}_{model}" for model, actions in OT_STAFF_PERMS.items() for action in actions]
        group.permissions.set(Permission.objects.filter(content_type__app_label="equipment", codename__in=codenames))
        self.stdout.write(self.style.SUCCESS(f"'OT staff' group has {group.permissions.count()} permissions."))

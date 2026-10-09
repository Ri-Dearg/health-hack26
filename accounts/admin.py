from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm

from .models import Staff


class StaffChangeForm(UserChangeForm):
    card_id = forms.CharField(
        label="Keycard ID", required=False,
        help_text="Enter or tap a card to enrol it. Only a hash is stored; leave blank to keep the current card.",
    )

    class Meta(UserChangeForm.Meta):
        model = Staff

    def save(self, commit=True):
        staff = super().save(commit=False)
        if self.cleaned_data.get("card_id"):
            staff.set_card_id(self.cleaned_data["card_id"])
        if commit:
            staff.save()
            self.save_m2m()
        return staff


@admin.register(Staff)
class StaffAdmin(UserAdmin):
    form = StaffChangeForm
    list_display = ("username", "first_name", "last_name", "staff_number", "has_card", "is_superuser", "is_active")
    list_filter = ("is_superuser", "is_active", "groups")
    search_fields = ("username", "first_name", "last_name", "staff_number", "email")
    fieldsets = UserAdmin.fieldsets + (
        ("Hospital ID", {"fields": ("staff_number", "card_id")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Hospital ID", {"fields": ("first_name", "last_name", "email", "staff_number")}),
    )

    @admin.display(boolean=True, description="Card enrolled")
    def has_card(self, obj):
        return bool(obj.card_id_hash)

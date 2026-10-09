import base64
import io
import json

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html

from . import services, spreadsheet, synergy
from .models import Category, Equipment, Loan, LocationLog, Patient, Pool, Tag, WaitlistEntry

admin.site.site_header = "TUH OT equipment"
admin.site.site_title = "OT equipment"
admin.site.index_title = "Equipment, loans and waitlist"

# Status in words and an icon, never colour alone.
STATUS_ICONS = {
    Equipment.Status.AVAILABLE: "✅",
    Equipment.Status.ON_LOAN: "🔄",
    Equipment.Status.AWAITING_REPAIR: "🔧",
    Equipment.Status.MISSING: "❓",
    Equipment.Status.ALLOCATED: "📌",
}


def status_label(equipment):
    return format_html("{} {}", STATUS_ICONS.get(equipment.status, ""), equipment.get_status_display())


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "default_loan_days", "item_count")
    search_fields = ("name",)

    @admin.display(description="Items")
    def item_count(self, obj):
        return obj.items.count()


class TagInline(admin.TabularInline):
    model = Tag
    extra = 0


class LoanInline(admin.TabularInline):
    model = Loan
    extra = 0
    fields = ("patient", "issued_by", "ward", "issued_at", "due_at", "returned_at")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class LocationLogInline(admin.TabularInline):
    model = LocationLog
    extra = 0
    fields = ("recorded_at", "event", "location", "source", "rssi", "recorded_by")
    readonly_fields = fields
    can_delete = False
    max_num = 0
    ordering = ("-recorded_at",)


class TrackerImportForm(forms.Form):
    file = forms.FileField(label="Excel file (.xlsx)")
    default_pool = forms.ModelChoiceField(
        queryset=Pool.objects.all(), to_field_name="name", required=False,
        help_text="Pool for new items when the sheet doesn't say (use Winter Initiative for that spreadsheet). "
                  "Blank means OT Department.",
    )


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("asset_number", "category", "description", "status_display", "location", "location_updated_at",
                    "pool", "flag")
    list_filter = ("status", "flagged", "category", "pool", "on_assettrak")
    search_fields = ("asset_number", "barcode", "serial_number", "hc21_number", "dml_number", "description", "tags__uid")
    readonly_fields = ("location_updated_at",)
    inlines = [TagInline, LoanInline, LocationLogInline]
    actions = ["return_to_store", "mark_awaiting_repair", "mark_missing"]
    fieldsets = (
        (None, {"fields": ("asset_number", "category", "description", "pool", "status", "flagged")}),
        ("Fit", {"fields": ("size", "max_weight", "replacement_cover")}),
        ("Where", {"fields": ("location", "location_updated_at")}),
        ("Identifiers", {"fields": ("barcode", "serial_number", "hc21_number", "dml_number", "on_assettrak")}),
        ("Service", {"fields": ("purchase_date", "last_service_date", "notes")}),
    )

    @admin.display(description="Status", ordering="status")
    def status_display(self, obj):
        return status_label(obj)

    @admin.display(description="Flag", ordering="flagged")
    def flag(self, obj):
        return "🚩 Check" if obj.flagged else ""

    def get_urls(self):
        return [
            path("export/", self.admin_site.admin_view(self.export_view), name="equipment_export"),
            path("import/", self.admin_site.admin_view(self.import_view), name="equipment_import"),
        ] + super().get_urls()

    def export_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        response = HttpResponse(
            spreadsheet.export_bytes(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        stamp = timezone.localtime().strftime("%Y-%m-%d_%H%M")
        response["Content-Disposition"] = f'attachment; filename="ot-equipment-tracker_{stamp}.xlsx"'
        return response

    def import_view(self, request):
        """Upload, preview (dry run), then apply. The file waits in the session between the two steps."""
        if not (self.has_add_permission(request) and self.has_change_permission(request)):
            raise PermissionDenied
        context = {**self.admin_site.each_context(request), "title": "Import from Excel", "opts": self.model._meta}
        form = TrackerImportForm()
        if request.method == "POST" and "preview" in request.POST:
            form = TrackerImportForm(request.POST, request.FILES)
            if form.is_valid():
                data = form.cleaned_data["file"].read()
                pool = form.cleaned_data["default_pool"].name if form.cleaned_data["default_pool"] else Pool.DEFAULT_NAME
                try:
                    report = spreadsheet.import_workbook(io.BytesIO(data), dry_run=True, default_pool=pool)
                except Exception as exc:  # not an xlsx, corrupt file, etc.
                    self.message_user(request, f"Couldn't read that file: {exc}", messages.ERROR)
                else:
                    request.session["tracker_import"] = {"data": base64.b64encode(data).decode(), "pool": pool}
                    return TemplateResponse(request, "admin/equipment/import.html", context | {"report": report})
        elif request.method == "POST" and "apply" in request.POST:
            pending = request.session.pop("tracker_import", None)
            if not pending:
                self.message_user(request, "That preview has expired. Upload the file again.", messages.WARNING)
                return redirect("admin:equipment_import")
            report = spreadsheet.import_workbook(
                io.BytesIO(base64.b64decode(pending["data"])), dry_run=False, default_pool=pending["pool"],
            )
            return TemplateResponse(request, "admin/equipment/import.html", context | {"report": report})
        return TemplateResponse(request, "admin/equipment/import.html", context | {"form": form})

    @admin.action(description="Return to store (closes the loan)")
    def return_to_store(self, request, queryset):
        for item in queryset:
            entry = services.return_item(item, staff=request.user)
            if entry:
                self.message_user(request, f"{item.asset_number} is back. Next on the waitlist: {entry}.", messages.WARNING)
        self.message_user(request, f"{queryset.count()} item(s) returned.")

    @admin.action(description="Mark awaiting repair")
    def mark_awaiting_repair(self, request, queryset):
        queryset.update(status=Equipment.Status.AWAITING_REPAIR)

    @admin.action(description="Mark missing")
    def mark_missing(self, request, queryset):
        queryset.update(status=Equipment.Status.MISSING)


@admin.register(Pool)
class PoolAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "item_count")
    search_fields = ("name",)

    @admin.display(description="Items")
    def item_count(self, obj):
        return obj.equipment.count()


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("uid", "tag_type", "equipment", "is_active")
    list_filter = ("tag_type", "is_active")
    search_fields = ("uid", "equipment__asset_number")
    autocomplete_fields = ("equipment",)


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("mrn", "open_loans", "created_at")
    search_fields = ("mrn",)
    inlines = [LoanInline]

    @admin.display(description="Open loans")
    def open_loans(self, obj):
        return obj.loans.open().count()


class OverdueFilter(admin.SimpleListFilter):
    title = "loan state"
    parameter_name = "state"

    def lookups(self, request, model_admin):
        return [("open", "Open"), ("overdue", "Overdue"), ("needs_owner", "Needs an owner"), ("returned", "Returned")]

    def queryset(self, request, queryset):
        match self.value():
            case "open":
                return queryset.open()
            case "overdue":
                return queryset.overdue()
            case "needs_owner":
                return queryset.open().filter(issued_by__isnull=True)
            case "returned":
                return queryset.filter(returned_at__isnull=False)
        return queryset


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ("equipment", "patient", "issued_by_display", "ward", "issued_at", "due_at", "state")
    list_filter = (OverdueFilter, "short_term_loan", "equipment__category")
    search_fields = ("equipment__asset_number", "patient__mrn", "ward")
    autocomplete_fields = ("equipment", "patient", "issued_by")
    readonly_fields = ("overdue_notice_sent_at", "fhir_preview")
    date_hierarchy = "issued_at"
    actions = ["mark_returned", "claim"]

    @admin.display(description="What Synergy would receive (FHIR, not sent)")
    def fhir_preview(self, obj):
        if not obj.pk:
            return "Shown after saving."
        return format_html("<pre style='font-size:12px'>{}</pre>", json.dumps(synergy.loan_to_fhir(obj), indent=2))

    @admin.display(description="Issued by")
    def issued_by_display(self, obj):
        return obj.issued_by or "⚠️ Needs an owner"

    @admin.display(description="State")
    def state(self, obj):
        if not obj.is_open:
            return "Returned"
        return "⏰ Overdue" if obj.is_overdue else "Open"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change and obj.is_open:
            item = obj.equipment
            item.status = Equipment.Status.ON_LOAN
            item.location = obj.ward or item.location
            item.location_updated_at = timezone.now()
            item.save()
            LocationLog.objects.create(equipment=item, event=LocationLog.Event.OUT, location=item.location, recorded_by=request.user)

    @admin.action(description="Mark returned (item back to store)")
    def mark_returned(self, request, queryset):
        for loan in queryset.open():
            services.return_item(loan.equipment, staff=request.user)

    @admin.action(description="Claim: I issued these")
    def claim(self, request, queryset):
        n = queryset.filter(issued_by__isnull=True).update(issued_by=request.user)
        self.message_user(request, f"You now own {n} loan(s).")


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(admin.ModelAdmin):
    list_display = ("patient", "category", "priority", "ward", "requested_at", "status", "allocated_equipment")
    list_filter = ("status", "priority", "category")
    search_fields = ("patient__mrn", "ward")
    autocomplete_fields = ("patient", "allocated_equipment", "requested_by")


@admin.register(LocationLog)
class LocationLogAdmin(admin.ModelAdmin):
    list_display = ("recorded_at", "equipment", "event", "location", "source", "rssi", "recorded_by")
    list_filter = ("event", "source")
    search_fields = ("equipment__asset_number", "location")
    date_hierarchy = "recorded_at"
    autocomplete_fields = ("equipment", "tag", "recorded_by")

    # Append-only: it's the governance record, so nobody edits or deletes history.
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

import django.db.models.deletion
from django.db import migrations, models

import equipment.models

BASE_POOLS = {
    "ot": ("OT Department", "General OT Department loan pool"),
    "winter": ("Winter Initiative", "Funded by the Winter Initiative; managed by the Discharge Planning OT service"),
}


def pools_forward(apps, schema_editor):
    Pool = apps.get_model("equipment", "Pool")
    Equipment = apps.get_model("equipment", "Equipment")
    pools = {code: Pool.objects.get_or_create(name=name, defaults={"description": desc})[0]
             for code, (name, desc) in BASE_POOLS.items()}
    for item in Equipment.objects.all():
        item.pool_ref = pools.get(item.pool, pools["ot"])
        item.save(update_fields=["pool_ref"])


def pools_backward(apps, schema_editor):
    Equipment = apps.get_model("equipment", "Equipment")
    for item in Equipment.objects.select_related("pool_ref"):
        item.pool = "winter" if item.pool_ref and item.pool_ref.name == "Winter Initiative" else "ot"
        item.save(update_fields=["pool"])


class Migration(migrations.Migration):
    dependencies = [("equipment", "0002_log_sources")]

    operations = [
        migrations.CreateModel(
            name="Pool",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("description", models.CharField(blank=True, max_length=200)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.AddField(
            model_name="equipment",
            name="pool_ref",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to="equipment.pool"),
        ),
        migrations.RunPython(pools_forward, pools_backward),
        migrations.RemoveField(model_name="equipment", name="pool"),
        migrations.RenameField(model_name="equipment", old_name="pool_ref", new_name="pool"),
        migrations.AlterField(
            model_name="equipment",
            name="pool",
            field=models.ForeignKey(
                default=equipment.models.default_pool, on_delete=django.db.models.deletion.PROTECT,
                related_name="equipment", to="equipment.pool",
            ),
        ),
        migrations.AlterField(
            model_name="equipment", name="description",
            field=models.CharField(blank=True, max_length=200, verbose_name="make and model"),
        ),
        migrations.AddField(
            model_name="equipment", name="size",
            field=models.CharField(blank=True, help_text='As written on the register, e.g. 18" x 17".', max_length=100),
        ),
        migrations.AddField(
            model_name="equipment", name="max_weight",
            field=models.CharField(blank=True, help_text="As written, e.g. 135kg or 160kg / 25st.", max_length=50,
                                   verbose_name="max user weight"),
        ),
        migrations.AddField(
            model_name="equipment", name="hc21_number",
            field=models.CharField(blank=True, max_length=50, verbose_name="HC21 No."),
        ),
        migrations.AddField(
            model_name="equipment", name="dml_number",
            field=models.CharField(blank=True, max_length=50, verbose_name="DML No."),
        ),
        migrations.AddField(
            model_name="equipment", name="on_assettrak",
            field=models.BooleanField(blank=True, help_text="AT+ / AT- on the register. Unknown if blank.", null=True,
                                      verbose_name="on AssetTrak"),
        ),
        migrations.AddField(
            model_name="equipment", name="replacement_cover",
            field=models.BooleanField(blank=True, help_text="Cushions: has a spare cover.", null=True),
        ),
        migrations.AddField(
            model_name="equipment", name="flagged",
            field=models.BooleanField(default=False, help_text="Needs someone to look at it (yellow on the register)."),
        ),
    ]

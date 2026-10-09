from django.db import migrations


class Migration(migrations.Migration):
    """The register calls this column "Comments"."""

    dependencies = [("equipment", "0004_tag_events")]

    operations = [migrations.RenameField(model_name="equipment", old_name="notes", new_name="comments")]

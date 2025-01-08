from django.db import migrations
from ebustoolbox.default_scenario import set_default_scenario

class Migration(migrations.Migration):
    dependencies = [
        ("ebustoolbox", "0043_alter_batterytype_chemistry_and_more"),
    ]

    operations = [
        migrations.RunPython(set_default_scenario, lambda app, schema_editor: ()),
    ]

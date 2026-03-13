# Generated manually — adds metadata JSONField to Connection

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("network_graph", "0008_backfill_node_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="connection",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

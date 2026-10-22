# PatentRef 5.11: the terms version and the time of its acceptance on each key record. Two nullable columns and no default,
# so SQLite adds them with ALTER TABLE ADD COLUMN (no table copy) and the keys minted before keep working unchanged.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lapse_accounts", "0002_windowcount"),
    ]

    operations = [
        migrations.AddField(
            model_name="accountkey",
            name="terms_version",
            field=models.CharField(blank=True, max_length=40, null=True),
        ),
        migrations.AddField(
            model_name="accountkey",
            name="terms_accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

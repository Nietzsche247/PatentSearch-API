# PatentRef wave 4 Q1: the weekly sign-up source counter (week, code, count). A new table and nothing else: no existing
# row or column changes, so it needs no typed confirmation and the keys and accounts are untouched.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lapse_accounts", "0003_accountkey_terms"),
    ]

    operations = [
        migrations.CreateModel(
            name="SignupSource",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("week", models.CharField(help_text="ISO week, YYYY-Www, UTC", max_length=8)),
                ("code", models.CharField(max_length=32)),
                ("count", models.PositiveIntegerField(default=0)),
            ],
            options={
                "unique_together": {("week", "code")},
            },
        ),
    ]

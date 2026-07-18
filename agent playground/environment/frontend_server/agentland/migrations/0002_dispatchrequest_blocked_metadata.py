from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("agentland", "0001_initial")]

    operations = [
        migrations.AddField(model_name="dispatchrequest", name="error_category", field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name="dispatchrequest", name="exit_code", field=models.IntegerField(blank=True, null=True)),
        migrations.AddField(model_name="dispatchrequest", name="platform_error", field=models.TextField(blank=True)),
        migrations.AddField(model_name="dispatchrequest", name="codex_started", field=models.BooleanField(blank=True, null=True)),
    ]

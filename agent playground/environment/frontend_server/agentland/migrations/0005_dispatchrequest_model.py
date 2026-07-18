from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("agentland", "0004_verificationrequest")]

    operations = [
        migrations.AddField(
            model_name="dispatchrequest",
            name="model",
            field=models.CharField(blank=True, max_length=128),
        ),
    ]

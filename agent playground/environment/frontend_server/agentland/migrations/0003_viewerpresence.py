from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [("agentland", "0002_dispatchrequest_blocked_metadata")]
    operations = [
        migrations.CreateModel(name="ViewerPresence", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("viewer_id", models.CharField(max_length=64)), ("label", models.CharField(default="Observer", max_length=64)),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="viewer_presences", to="agentland.projectsession")),
        ]),
        migrations.AddConstraint(model_name="viewerpresence", constraint=models.UniqueConstraint(fields=("session", "viewer_id"), name="agentland_session_viewer_unique")),
    ]

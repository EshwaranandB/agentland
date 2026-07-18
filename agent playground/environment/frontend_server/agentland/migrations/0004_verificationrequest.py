from django.db import migrations, models
import django.db.models.deletion
import agentland.models
import uuid


class Migration(migrations.Migration):
    dependencies = [("agentland", "0003_viewerpresence")]
    operations = [
        migrations.CreateModel(name="VerificationRequest", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("idempotency_key", models.CharField(max_length=255)), ("status", models.CharField(default="running", max_length=32)),
            ("response_payload", agentland.models.JSONTextField(default=dict)), ("created_at", models.DateTimeField(auto_now_add=True)), ("completed_at", models.DateTimeField(blank=True, null=True)),
            ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="verification_requests", to="agentland.projectsession")),
            ("task", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="agentland.task")),
        ]),
        migrations.AddConstraint(model_name="verificationrequest", constraint=models.UniqueConstraint(fields=("session", "idempotency_key"), name="agentland_session_verification_idempotency_unique")),
    ]

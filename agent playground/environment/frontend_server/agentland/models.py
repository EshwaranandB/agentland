import uuid
import json

from django.db import models


class JSONTextField(models.TextField):
    """Django 2.2-compatible JSON storage for SQLite-backed P0 evidence."""
    def from_db_value(self, value, expression, connection):
        return self.to_python(value)

    def to_python(self, value):
        if value is None or isinstance(value, (dict, list)):
            return value if value is not None else {}
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}

    def get_prep_value(self, value):
        return json.dumps(value if value is not None else {})


class ProjectSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mission = models.TextField()
    status = models.CharField(max_length=32, default="created")
    current_sequence = models.PositiveIntegerField(default=0)
    time_limit_seconds = models.PositiveIntegerField(default=0)
    token_limit = models.PositiveIntegerField(default=0)
    cost_limit = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Task(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ProjectSession, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=255)
    description = models.TextField()
    status = models.CharField(max_length=32, default="created")
    assigned_worker = models.ForeignKey("Worker", null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_tasks")
    acceptance_criteria = models.TextField()
    artifact_reference = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Worker(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ProjectSession, on_delete=models.CASCADE, related_name="workers")
    role = models.CharField(max_length=32)
    status = models.CharField(max_length=32, default="idle")
    current_task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name="active_workers")
    public_summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class WorkEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ProjectSession, on_delete=models.CASCADE, related_name="events")
    sequence = models.PositiveIntegerField()
    event_type = models.CharField(max_length=64)
    actor_id = models.CharField(max_length=128, blank=True)
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    public_summary = models.TextField()
    structured_payload = JSONTextField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session", "sequence"], name="agentland_session_sequence_unique")]
        ordering = ["sequence"]


class DispatchRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ProjectSession, on_delete=models.CASCADE, related_name="dispatches")
    idempotency_key = models.CharField(max_length=255)
    status = models.CharField(max_length=32, default="running")
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL)
    first_event_sequence = models.PositiveIntegerField(null=True, blank=True)
    last_event_sequence = models.PositiveIntegerField(null=True, blank=True)
    response_payload = JSONTextField(default=dict)
    error_summary = models.TextField(blank=True)
    error_category = models.CharField(max_length=64, blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    platform_error = models.TextField(blank=True)
    codex_started = models.BooleanField(null=True, blank=True)
    runner_identity = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session", "idempotency_key"], name="agentland_session_idempotency_unique")]


class UsageEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ProjectSession, on_delete=models.CASCADE, related_name="usage_entries")
    worker = models.ForeignKey(Worker, on_delete=models.CASCADE, related_name="usage_entries")
    event = models.ForeignKey(WorkEvent, on_delete=models.CASCADE, related_name="usage_entries")
    provider = models.CharField(max_length=64)
    model = models.CharField(max_length=128, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    is_estimated = models.BooleanField(default=True)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class Artifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ProjectSession, on_delete=models.CASCADE, related_name="artifacts")
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="artifacts")
    artifact_type = models.CharField(max_length=64)
    relative_path = models.CharField(max_length=512)
    sha256 = models.CharField(max_length=64)
    metadata = JSONTextField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class ViewerPresence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ProjectSession, on_delete=models.CASCADE, related_name="viewer_presences")
    viewer_id = models.CharField(max_length=64)
    label = models.CharField(max_length=64, default="Observer")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session", "viewer_id"], name="agentland_session_viewer_unique")]


class VerificationRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ProjectSession, on_delete=models.CASCADE, related_name="verification_requests")
    idempotency_key = models.CharField(max_length=255)
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=32, default="running")
    response_payload = JSONTextField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session", "idempotency_key"], name="agentland_session_verification_idempotency_unique")]

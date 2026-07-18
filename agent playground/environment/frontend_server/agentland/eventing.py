from django.db import IntegrityError, transaction

from .models import WorkEvent

EVENT_TYPES = {
    "session.created", "session.started", "worker.created", "task.created",
    "task.assigned", "task.started", "tool.started", "tool.completed",
    "tool.failed", "file.created", "file.modified", "file.deleted",
    "test.started", "test.passed", "test.failed", "usage.updated",
    "artifact.created", "task.completed", "task.failed", "session.paused",
    "session.completed",
}


def append_event(session, event_type, public_summary, *, actor_id="", task=None, payload=None, retries=3):
    if event_type not in EVENT_TYPES:
        raise ValueError("Unsupported AgentLand event type")
    for attempt in range(retries):
        try:
            with transaction.atomic():
                session.refresh_from_db()
                sequence = session.current_sequence + 1
                event = WorkEvent.objects.create(
                    session=session, sequence=sequence, event_type=event_type,
                    actor_id=actor_id, task=task, public_summary=public_summary,
                    structured_payload=payload or {},
                )
                session.current_sequence = sequence
                session.save(update_fields=["current_sequence", "updated_at"])
                return event
        except IntegrityError:
            if attempt == retries - 1:
                raise
    raise RuntimeError("Unable to append AgentLand event")

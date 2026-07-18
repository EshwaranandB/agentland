import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .eventing import append_event
from .models import Artifact, DispatchRequest, ProjectSession, Task
from .runners import CodexWorkerRunner, DeterministicWorkerRunner, run_fixed_test


def workspace_root():
    return Path(settings.AGENTLAND_WORKSPACE_ROOT).resolve()


def restore_codex_baseline():
    """Restore the deliberate defect before each real-Codex validation."""
    root = workspace_root()
    git = shutil.which("git")
    if git and (root / ".git").exists():
        safe_env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC") if key in os.environ}
        reset = subprocess.run([git, "reset", "--hard", "HEAD"], cwd=str(root), shell=False, env=safe_env, text=True, capture_output=True, timeout=20, check=False)
        clean = subprocess.run([git, "clean", "-fd"], cwd=str(root), shell=False, env=safe_env, text=True, capture_output=True, timeout=20, check=False)
        if reset.returncode == 0 and clean.returncode == 0:
            return
    source = root / ".agentland-baseline" / "rooms.js"
    target = root / "src" / "rooms.js"
    if not source.is_file() or source.is_symlink():
        raise ValueError("AgentLand Codex baseline is unavailable")
    if target.is_symlink():
        raise ValueError("AgentLand implementation target is a symlink")
    target.write_bytes(source.read_bytes())


def scan_workspace():
    root = workspace_root()
    if not root.is_dir():
        raise ValueError("AgentLand workspace is unavailable")
    found = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        found[str(relative).replace("\\", "/")] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return found


def workspace_changes(before, after):
    records = []
    for path in sorted(after.keys() - before.keys()):
        records.append(("file.created", path, None, after[path]))
    for path in sorted(before.keys() & after.keys()):
        if before[path] != after[path]:
            records.append(("file.modified", path, before[path], after[path]))
    for path in sorted(before.keys() - after.keys()):
        records.append(("file.deleted", path, before[path], None))
    return records


def serialize_session(session):
    return {
        "id": str(session.id), "mission": session.mission, "status": session.status,
        "current_sequence": session.current_sequence,
        "workers": [{"id": str(w.id), "role": w.role, "status": w.status, "public_summary": w.public_summary} for w in session.workers.all()],
        "tasks": [{"id": str(t.id), "title": t.title, "status": t.status, "artifact_reference": t.artifact_reference} for t in session.tasks.all()],
        "artifacts": [{"id": str(a.id), "type": a.artifact_type, "path": a.relative_path, "sha256": a.sha256} for a in session.artifacts.all()],
        "events": [{"sequence": e.sequence, "type": e.event_type, "actor_id": e.actor_id, "task_id": str(e.task_id) if e.task_id else None, "summary": e.public_summary, "payload": e.structured_payload} for e in session.events.all()],
        "dispatches": [{"id": str(d.id), "status": d.status, "runner_identity": d.runner_identity, "model": d.model, "first_event_sequence": d.first_event_sequence, "last_event_sequence": d.last_event_sequence, "error_category": d.error_category, "exit_code": d.exit_code, "platform_error": d.platform_error, "codex_started": d.codex_started} for d in session.dispatches.all()],
    }


def _claim_dispatch(session, key, runner_name):
    """Claim before creating work, so a retry cannot start a second worker."""
    try:
        with transaction.atomic():
            dispatch = DispatchRequest.objects.create(session=session, idempotency_key=key, runner_identity=runner_name)
            return dispatch, True
    except IntegrityError:
        return DispatchRequest.objects.get(session=session, idempotency_key=key), False


def _finish(dispatch, status, terminal, error="", result=None):
    result = result or {}
    dispatch.status = status
    dispatch.error_summary = error[:500]
    dispatch.error_category = result.get("error_category", "")[:64]
    dispatch.exit_code = result.get("exit_code")
    dispatch.platform_error = result.get("platform_error", "")[:500]
    dispatch.codex_started = result.get("codex_started") if dispatch.runner_identity == "codex" else None
    dispatch.last_event_sequence = terminal.sequence
    dispatch.completed_at = timezone.now()
    dispatch.save(update_fields=["status", "error_summary", "error_category", "exit_code", "platform_error", "codex_started", "last_event_sequence", "completed_at"])
    dispatch.response_payload = serialize_session(dispatch.session)
    dispatch.save(update_fields=["response_payload"])


def dispatch_builder(session, idempotency_key, runner_name="deterministic"):
    runners = {"deterministic": DeterministicWorkerRunner, "codex": CodexWorkerRunner}
    if runner_name not in runners:
        raise ValueError("Unsupported runner identity")
    dispatch, claimed = _claim_dispatch(session, idempotency_key, runner_name)
    if not claimed:
        return dispatch, False
    builder = session.workers.get(role="builder")
    with transaction.atomic():
        task = Task.objects.create(session=session, title="Repair whiteboard room isolation", description="Ensure strokes remain in their room.", status="assigned", assigned_worker=builder, acceptance_criteria="Room isolation test passes.")
        dispatch.task = task
        created = append_event(session, "task.created", "Created bounded room-isolation task.", actor_id="orchestrator", task=task)
        append_event(session, "task.assigned", "Assigned task to Builder.", actor_id="orchestrator", task=task)
        task.status, builder.status, builder.current_task = "active", "working", task
        task.save(update_fields=["status", "updated_at"])
        builder.save(update_fields=["status", "current_task", "updated_at"])
        append_event(session, "task.started", "Builder started room-isolation repair.", actor_id="builder", task=task)
        dispatch.first_event_sequence = created.sequence
        dispatch.save(update_fields=["task", "first_event_sequence"])
    baseline = None
    if runner_name == "codex":
        restore_codex_baseline()
        baseline = run_fixed_test(workspace_root())
        if baseline["exit_code"] == 0:
            raise RuntimeError("Codex validation refused: baseline isolation test unexpectedly passed")
    append_event(session, "tool.started", f"Started {runner_name} worker.", actor_id="builder", task=task, payload={"runner_identity": runner_name, "baseline_test": baseline})
    before = scan_workspace()
    try:
        result = runners[runner_name]().run(workspace_root())
    except Exception as error:
        append_event(session, "tool.failed", "Worker runner failed safely.", actor_id="builder", task=task, payload={"runner_identity": runner_name, "error": str(error)[:500]})
        task.status, builder.status = "failed", "idle"
        task.save(update_fields=["status", "updated_at"])
        builder.save(update_fields=["status", "updated_at"])
        terminal = append_event(session, "task.failed", "Builder task failed before test execution.", actor_id="builder", task=task)
        _finish(dispatch, "failed", terminal, str(error))
        return dispatch, True
    if runner_name == "codex" and not result.get("codex_started", True):
        append_event(session, "tool.failed", "Codex process could not be launched.", actor_id="builder", task=task, payload={"runner_identity": runner_name, **result})
        task.status, builder.status, builder.current_task = "failed", "idle", None
        task.save(update_fields=["status", "updated_at"])
        builder.save(update_fields=["status", "current_task", "updated_at"])
        terminal = append_event(session, "task.failed", "Builder task blocked before Codex execution.", actor_id="builder", task=task)
        _finish(dispatch, "blocked", terminal, result.get("stderr", "Codex process launch denied"), result)
        return dispatch, True
    after = scan_workspace()
    for kind, path, before_hash, after_hash in workspace_changes(before, after):
        append_event(session, kind, f"Independently detected {kind.split('.')[1]} file: {path}", actor_id="builder", task=task, payload={"path": path, "before_sha256": before_hash, "after_sha256": after_hash})
    tool_event = "tool.completed" if result["exit_code"] == 0 else "tool.failed"
    tool_summary = "Worker runner completed." if result["exit_code"] == 0 else "Worker runner exited unsuccessfully."
    append_event(session, tool_event, tool_summary, actor_id="builder", task=task, payload={"runner_identity": runner_name, **result})
    append_event(session, "test.started", "Started fixed room-isolation test.", actor_id="tester", task=task)
    test = run_fixed_test(workspace_root())
    if test["exit_code"] == 0:
        append_event(session, "test.passed", "Room-isolation test passed.", actor_id="tester", task=task, payload=test)
        artifact = Artifact.objects.create(session=session, task=task, artifact_type="web-entry", relative_path="whiteboard/index.html", sha256=after.get("index.html", ""), metadata={"runner_identity": runner_name})
        append_event(session, "artifact.created", "Created whiteboard artifact reference.", actor_id="builder", task=task, payload={"path": artifact.relative_path, "sha256": artifact.sha256})
        task.status, task.artifact_reference, builder.status, builder.current_task = "completed", artifact.relative_path, "idle", None
        task.save(update_fields=["status", "artifact_reference", "updated_at"])
        builder.save(update_fields=["status", "current_task", "updated_at"])
        append_event(session, "task.completed", "Builder task completed after verification.", actor_id="builder", task=task)
        session.status = "completed"
        session.save(update_fields=["status", "updated_at"])
        terminal = append_event(session, "session.completed", "AgentLand P0 session completed.", actor_id="orchestrator")
        _finish(dispatch, "completed", terminal)
    else:
        append_event(session, "test.failed", "Room-isolation test failed.", actor_id="tester", task=task, payload=test)
        task.status, builder.status = "failed", "idle"
        task.save(update_fields=["status", "updated_at"])
        builder.save(update_fields=["status", "updated_at"])
        terminal = append_event(session, "task.failed", "Builder task failed verification.", actor_id="tester", task=task)
        _finish(dispatch, "failed", terminal)
    return dispatch, True

import json
import uuid
from datetime import timedelta

from django.http import HttpResponse, JsonResponse
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .eventing import append_event
from .models import Artifact, ProjectSession, Task, VerificationRequest, ViewerPresence, Worker
from .city import city_page
from .shell import demo_shell
from .projection import project_city_frames
from .services import dispatch_builder, restore_codex_baseline, run_fixed_test, serialize_session, workspace_root


def request_body(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        raise ValueError("Request body must be JSON")


@require_GET
def shell(request):
    return demo_shell()


@require_http_methods(["GET", "POST"])
def create_session(request):
    if request.method == "GET":
        return JsonResponse({"sessions": [serialize_session(session) for session in ProjectSession.objects.order_by("-created_at")[:20]]})
    try:
        mission = str(request_body(request).get("mission", "Repair whiteboard room isolation")).strip()
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    if not mission:
        return JsonResponse({"error": "mission is required"}, status=400)
    with transaction.atomic():
        session = ProjectSession.objects.create(mission=mission, status="started")
        append_event(session, "session.created", "Created AgentLand session.", actor_id="orchestrator")
        for role in ("orchestrator", "builder", "tester"):
            Worker.objects.create(session=session, role=role)
            append_event(session, "worker.created", f"Created {role.title()} worker.", actor_id=role, payload={"role": role})
        append_event(session, "session.started", "Started AgentLand session.", actor_id="orchestrator")
    return JsonResponse(serialize_session(session), status=201)


@require_POST
def dispatch_builder_view(request, session_id):
    key = request.headers.get("Idempotency-Key")
    if not key:
        return JsonResponse({"error": "Idempotency-Key header is required"}, status=400)
    session = ProjectSession.objects.filter(id=session_id).first()
    if not session:
        return JsonResponse({"error": "session not found"}, status=404)
    try:
        runner = request_body(request).get("runner_identity", "deterministic")
        dispatch, claimed = dispatch_builder(session, key, runner)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    if not claimed and dispatch.status == "running":
        return JsonResponse({"error": "dispatch already in progress"}, status=409)
    return JsonResponse(dispatch.response_payload, status=200)


@require_GET
def snapshot(request, session_id):
    session = ProjectSession.objects.filter(id=session_id).first()
    if not session:
        return JsonResponse({"error": "session not found"}, status=404)
    return JsonResponse(serialize_session(session))


@require_GET
def events(request, session_id):
    session = ProjectSession.objects.filter(id=session_id).first()
    if not session:
        return JsonResponse({"error": "session not found"}, status=404)
    rows = session.events.all()
    after = request.GET.get("after_sequence")
    if after:
        rows = rows.filter(sequence__gt=int(after))
    return JsonResponse({"events": [{"sequence": e.sequence, "type": e.event_type, "summary": e.public_summary, "payload": e.structured_payload} for e in rows]})


@require_GET
def city(request, session_id):
    session = ProjectSession.objects.filter(id=session_id).first()
    if not session:
        return JsonResponse({"error": "session not found"}, status=404)
    return city_page(session)


@require_GET
def artifact_preview(request, session_id, artifact_id):
    artifact = Artifact.objects.filter(id=artifact_id, session_id=session_id, artifact_type="web-entry").first()
    if not artifact or not artifact.relative_path.startswith("whiteboard/"):
        return JsonResponse({"error": "artifact not found"}, status=404)
    root = workspace_root()
    target = (root / artifact.relative_path.split("/", 1)[1]).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return JsonResponse({"error": "artifact not found"}, status=404)
    if not target.is_file() or target.is_symlink():
        return JsonResponse({"error": "artifact not found"}, status=404)
    return HttpResponse(target.read_bytes(), content_type="text/html; charset=utf-8")


@require_GET
def city_state(request, session_id):
    session = ProjectSession.objects.filter(id=session_id).first()
    if not session:
        return JsonResponse({"error": "session not found"}, status=404)
    viewers = session.viewer_presences.filter(updated_at__gte=timezone.now() - timedelta(seconds=30))
    return JsonResponse({"frames": project_city_frames(session), "viewer_count": viewers.count(), "latest_sequence": session.current_sequence})


@require_POST
def heartbeat_presence(request, session_id):
    session = ProjectSession.objects.filter(id=session_id).first()
    if not session:
        return JsonResponse({"error": "session not found"}, status=404)
    body = request_body(request)
    viewer_id = str(body.get("viewer_id") or request.headers.get("X-AgentLand-Viewer") or uuid.uuid4())[:64]
    label = str(body.get("label") or "Observer")[:64]
    presence, _ = ViewerPresence.objects.get_or_create(session=session, viewer_id=viewer_id, defaults={"label": label})
    if presence.label != label:
        presence.label = label
        presence.save(update_fields=["label", "updated_at"])
    return JsonResponse({"viewer_id": viewer_id, "viewer_count": session.viewer_presences.filter(updated_at__gte=timezone.now() - timedelta(seconds=30)).count(), "latest_sequence": session.current_sequence})


@require_POST
def verification_request(request, session_id):
    session = ProjectSession.objects.filter(id=session_id).first()
    if not session:
        return JsonResponse({"error": "session not found"}, status=404)
    key = request.headers.get("Idempotency-Key")
    if not key:
        return JsonResponse({"error": "Idempotency-Key header is required"}, status=400)
    body = request_body(request)
    request_text = str(body.get("request", "")).strip()
    if not request_text or len(request_text) > 240:
        return JsonResponse({"error": "a bounded verification request is required"}, status=400)
    try:
        with transaction.atomic():
            verification, claimed = VerificationRequest.objects.get_or_create(session=session, idempotency_key=key)
    except IntegrityError:
        verification, claimed = VerificationRequest.objects.get(session=session, idempotency_key=key), False
    if not claimed:
        if verification.status == "running":
            return JsonResponse({"error": "verification already in progress"}, status=409)
        return JsonResponse(verification.response_payload)
    tester = session.workers.get(role="tester")
    with transaction.atomic():
        task = Task.objects.create(session=session, title="Verification request", description=request_text, status="assigned", assigned_worker=tester, acceptance_criteria="Review the requested verification.")
        verification.task = task
        verification.save(update_fields=["task"])
        append_event(session, "task.created", "Contributor created a verification request.", actor_id="contributor", task=task, payload={"source": "contributor"})
        append_event(session, "task.assigned", "Assigned verification request to Tester.", actor_id="contributor", task=task, payload={"source": "contributor", "assignee": "tester"})
    tester.status, tester.current_task = "working", task
    tester.save(update_fields=["status", "current_task", "updated_at"])
    append_event(session, "test.started", "Tester started fixed room-isolation verification.", actor_id="tester", task=task, payload={"source": "contributor"})
    result = run_fixed_test(workspace_root())
    if result["exit_code"] == 0:
        append_event(session, "test.passed", "Contributor verification passed.", actor_id="tester", task=task, payload=result)
        task.status, tester.status, tester.current_task = "completed", "idle", None
        task.save(update_fields=["status", "updated_at"])
        tester.save(update_fields=["status", "current_task", "updated_at"])
        terminal = append_event(session, "task.completed", "Tester completed contributor verification.", actor_id="tester", task=task)
        status = "completed"
    else:
        append_event(session, "test.failed", "Contributor verification failed.", actor_id="tester", task=task, payload=result)
        task.status, tester.status, tester.current_task = "failed", "failed", None
        task.save(update_fields=["status", "updated_at"])
        tester.save(update_fields=["status", "current_task", "updated_at"])
        terminal = append_event(session, "task.failed", "Tester failed contributor verification.", actor_id="tester", task=task)
        status = "failed"
    verification.status, verification.completed_at = status, timezone.now()
    verification.response_payload = {"task_id": str(task.id), "status": status, "latest_sequence": terminal.sequence, "test": result}
    verification.save(update_fields=["status", "completed_at", "response_payload"])
    return JsonResponse(verification.response_payload, status=201)


@require_POST
def reset_baseline(request):
    try:
        restore_codex_baseline()
    except (OSError, ValueError) as error:
        return JsonResponse({"error": str(error)}, status=400)
    return JsonResponse({"status": "restored"})

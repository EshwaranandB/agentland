import json
import uuid
from datetime import timedelta

from django.http import HttpResponse, JsonResponse
from django.db import IntegrityError, transaction
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .eventing import append_event
from .models import Artifact, ProjectSession, Task, VerificationRequest, ViewerPresence, Worker
from .city import city_page
from .shell import demo_shell
from .projection import project_city_frames
from .services import dispatch_builder, restore_codex_baseline, run_fixed_test, serialize_session, workspace_root
from .agent_runtime.credentials import credential_store, local_bootstrap_key
from .agent_runtime.service import DEFAULT_MODEL, MODEL_ALLOWLIST, run_mission, valid_model


def request_body(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        raise ValueError("Request body must be JSON")


def _browser_session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


@require_http_methods(["GET", "POST", "DELETE"])
def openai_settings(request):
    key = _browser_session_key(request)
    if request.method == "DELETE":
        credential_store.clear_openai_key(key)
        request.session.pop("agentland_openai_model", None)
        return JsonResponse({"configured": False, "model": DEFAULT_MODEL})
    if request.method == "POST":
        try:
            body = request_body(request)
        except ValueError as error:
            return JsonResponse({"error": str(error)}, status=400)
        model, api_key = str(body.get("model", DEFAULT_MODEL)), str(body.get("api_key", "")).strip()
        if not valid_model(model):
            return JsonResponse({"error": "Unsupported model"}, status=400)
        if not api_key or len(api_key) < 20:
            return JsonResponse({"error": "A valid API key is required"}, status=400)
        credential_store.set_openai_key(key, api_key)
        request.session["agentland_openai_model"] = model
    if not credential_store.has_openai_key(key):
        bootstrap = local_bootstrap_key()
        if bootstrap:
            credential_store.set_openai_key(key, bootstrap)
    return JsonResponse({"configured": credential_store.has_openai_key(key), "model": request.session.get("agentland_openai_model", DEFAULT_MODEL), "models": list(MODEL_ALLOWLIST)})


@require_GET
def shell(request):
    return render(request, "agentland/shell.html")


@require_GET
def demo(request):
    """Play the deterministic demo directly on the existing Phaser Smallville map."""
    def frame(sequence, builder_location, tester_location, factory, testing, summary):
        workers = [
            {"id": "demo-orchestrator", "role": "orchestrator", "status": "working", "location": "town_hall", "summary": "Coordinating the visible demo.", "task": "demo"},
            {"id": "demo-builder", "role": "builder", "status": "working", "location": builder_location, "summary": summary, "task": "demo"},
            {"id": "demo-tester", "role": "tester", "status": "working" if tester_location == "testing_facility" else "idle", "location": tester_location, "summary": "Waiting for verification." if tester_location == "town_hall" else "Running fixed verification.", "task": "demo"},
        ]
        return {"session": {"id": "demo", "mission": "Build a collaborative whiteboard with isolated rooms and deploy it.", "status": "started"}, "hud": {"execution_mode": "DEMO MODE · SIMULATION", "runner_identity": "demo", "last_sequence": sequence}, "buildings": {"town_hall": "active", "code_factory": factory, "testing_facility": testing, "deployment_tower": "active" if sequence > 5 else "idle"}, "workers": workers, "tasks": [], "evidence": {"files": [], "tool": None, "test": None, "artifact": None, "blocker": None, "usage": []}, "activity": [{"sequence": sequence, "type": "demo.stage", "summary": summary, "actor": "demo", "task_id": "demo", "payload": {}}]}
    frames = [
        frame(1, "town_hall", "town_hall", "idle", "idle", "Planner received the mission."),
        frame(2, "code_factory", "town_hall", "active", "idle", "Builder is walking to Code Factory."),
        frame(3, "code_factory", "town_hall", "active", "idle", "Builder is repairing room isolation."),
        frame(4, "code_factory", "testing_facility", "progress", "active", "Tester is walking to Testing Facility."),
        frame(5, "code_factory", "testing_facility", "complete", "active", "Fixed verification is running."),
        frame(6, "deployment_tower", "testing_facility", "complete", "passed", "Verification passed; artifact is deploying."),
        frame(7, "deployment_tower", "testing_facility", "complete", "passed", "Demo workflow completed."),
    ]
    return render(request, "agentland/city.html", {"frames_json": json.dumps(frames).replace("</", "<\\/")})


@require_GET
def workspace(request, session_id):
    session = ProjectSession.objects.filter(id=session_id).first()
    if not session:
        return JsonResponse({"error": "session not found"}, status=404)
    return render(request, "agentland/workspace.html", {"session": serialize_session(session)})


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
        for role in ("orchestrator", "researcher", "builder", "tester"):
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
        if runner == "openai_api":
            api_key = credential_store.get_openai_key(_browser_session_key(request))
            model = request.session.get("agentland_openai_model", DEFAULT_MODEL)
            if not api_key:
                return JsonResponse({"error": "Configure an OpenAI API key in Settings first."}, status=400)
            api_result = run_mission(session_id=str(session.id), api_key=api_key, model=model, mission=session.mission)
            if not api_result.get("ok"):
                append_event(session, "tool.failed", "OpenAI mission planner could not start.", actor_id="orchestrator", payload={"runner_identity": "openai_api", "provider": "openai", "model": model, "error_category": api_result.get("error_category")})
                return JsonResponse({"error": api_result.get("error_category", "api_unavailable"), "fallback": "deterministic"}, status=502)
            orchestrator = session.workers.get(role="orchestrator")
            orchestrator.public_summary = api_result["public_summary"]
            orchestrator.save(update_fields=["public_summary", "updated_at"])
            append_event(session, "tool.completed", "OpenAI Orchestrator returned a bounded public plan.", actor_id="orchestrator", payload={"runner_identity": "openai_api", "provider": "openai", "model": model, "public_summary": api_result["public_summary"], "usage": api_result.get("usage", {})})
        dispatch, claimed = dispatch_builder(session, key, "deterministic" if runner == "openai_api" else runner)
        if runner == "openai_api":
            dispatch.runner_identity, dispatch.model = "openai_api", request.session.get("agentland_openai_model", DEFAULT_MODEL)
            dispatch.save(update_fields=["runner_identity", "model"])
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
@xframe_options_sameorigin
def city(request, session_id):
    session = ProjectSession.objects.filter(id=session_id).first()
    if not session:
        return JsonResponse({"error": "session not found"}, status=404)
    return city_page(request, session)


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

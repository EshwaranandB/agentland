"""Pure, read-only translation from persisted AgentLand evidence to city state."""


BUILDINGS = ("town_hall", "code_factory", "testing_facility", "deployment_tower")


def _event_payload(event):
    return event.structured_payload if isinstance(event.structured_payload, dict) else {}


def _safe_event(event):
    payload = _event_payload(event)
    return {"sequence": event.sequence, "type": event.event_type, "summary": event.public_summary,
            "actor": event.actor_id, "task_id": str(event.task_id) if event.task_id else None,
            "payload": payload}


def project_city(session, upto_sequence=None):
    """Build city state only from events up to an optional persisted sequence."""
    all_events = list(session.events.all())
    events = [event for event in all_events if upto_sequence is None or event.sequence <= upto_sequence]
    buildings = {name: "idle" for name in BUILDINGS}
    workers = {}
    tasks = {}
    evidence = {"files": [], "tool": None, "test": None, "artifact": None, "blocker": None, "usage": []}
    runner_identity = None
    for event in events:
        payload = _event_payload(event)
        if event.event_type == "worker.created":
            role = payload.get("role") or event.actor_id
            worker = next((row for row in session.workers.all() if row.role == role), None)
            workers[role] = {"id": str(worker.id) if worker else role, "role": role, "status": "idle", "location": "town_hall", "summary": "", "task": None}
        if event.task_id and event.task_id not in tasks:
            task = event.task
            tasks[event.task_id] = {"id": str(event.task_id), "title": task.title if task else "Task", "status": "created"}
        if event.event_type == "session.created":
            buildings["town_hall"] = "active"
        elif event.event_type == "task.assigned":
            assignee = payload.get("assignee", "builder")
            if assignee in workers:
                workers[assignee].update(status="assigned", task=str(event.task_id) if event.task_id else None)
        elif event.event_type == "task.started":
            if "builder" in workers:
                workers["builder"].update(status="working", location="code_factory", task=str(event.task_id) if event.task_id else None)
            if event.task_id in tasks:
                tasks[event.task_id]["status"] = "active"
        elif event.event_type == "tool.started":
            buildings["code_factory"] = "active"
            runner_identity = payload.get("runner_identity") or runner_identity
            evidence["tool"] = {"status": "started", "runner_identity": runner_identity}
        elif event.event_type in ("file.created", "file.modified", "file.deleted"):
            buildings["code_factory"] = "progress"
            evidence["files"].append({"kind": event.event_type, "path": payload.get("path"), "before_sha256": payload.get("before_sha256"), "after_sha256": payload.get("after_sha256")})
        elif event.event_type == "tool.completed":
            runner_identity = payload.get("runner_identity") or runner_identity
            evidence["tool"] = {"status": "completed", "runner_identity": runner_identity, "exit_code": payload.get("exit_code"), "duration_ms": payload.get("duration_ms"), "stdout": payload.get("stdout"), "stderr": payload.get("stderr")}
        elif event.event_type == "tool.failed":
            buildings["code_factory"] = "blocked"
            runner_identity = payload.get("runner_identity") or runner_identity
            evidence["tool"] = {"status": "failed", "runner_identity": runner_identity, "exit_code": payload.get("exit_code"), "duration_ms": payload.get("duration_ms"), "stdout": payload.get("stdout"), "stderr": payload.get("stderr")}
            evidence["blocker"] = {"reason": payload.get("error_category") or event.public_summary, "platform_error": payload.get("platform_error"), "codex_started": payload.get("codex_started")}
            if "builder" in workers:
                workers["builder"].update(status="blocked", location="code_factory")
        elif event.event_type == "test.started":
            buildings["testing_facility"] = "active"
            if "tester" in workers:
                workers["tester"].update(status="working", location="testing_facility", task=str(event.task_id) if event.task_id else None)
        elif event.event_type in ("test.failed", "test.passed"):
            outcome = "passed" if event.event_type == "test.passed" else "failed"
            buildings["testing_facility"] = outcome
            evidence["test"] = {"status": outcome, "command_id": payload.get("command_id"), "exit_code": payload.get("exit_code"), "duration_ms": payload.get("duration_ms"), "stdout": payload.get("stdout"), "stderr": payload.get("stderr")}
            if "tester" in workers:
                workers["tester"].update(status="idle", location="testing_facility")
        elif event.event_type == "artifact.created":
            buildings["deployment_tower"] = "active"
            artifact = session.artifacts.filter(relative_path=payload.get("path", ""), task_id=event.task_id).first()
            evidence["artifact"] = {"id": str(artifact.id) if artifact else None, "path": payload.get("path"), "sha256": payload.get("sha256"), "task_id": str(event.task_id) if event.task_id else None, "worker": "builder"}
        elif event.event_type == "task.completed":
            buildings["code_factory"] = "complete"
            buildings["deployment_tower"] = "complete"
            if event.task_id in tasks:
                tasks[event.task_id]["status"] = "completed"
            if "builder" in workers:
                workers["builder"].update(status="idle", task=None)
        elif event.event_type == "task.failed":
            if event.task_id in tasks:
                tasks[event.task_id]["status"] = "failed"
            if "builder" in workers and workers["builder"]["status"] != "blocked":
                workers["builder"].update(status="failed")
        elif event.event_type == "session.completed":
            buildings["town_hall"] = "complete"
    active = sum(worker["status"] in ("working", "assigned") for worker in workers.values())
    blocked = sum(worker["status"] == "blocked" for worker in workers.values())
    mode = "CODEX BLOCKED" if evidence["blocker"] and runner_identity == "codex" else ("DETERMINISTIC DEMO" if runner_identity == "deterministic" else ("REAL CODEX" if runner_identity == "codex" and evidence["tool"] and evidence["tool"]["status"] == "completed" else "NOT DISPATCHED"))
    hud = {"session_status": "completed" if any(e.event_type == "session.completed" for e in events) else ("started" if events else "created"), "tasks_complete": sum(task["status"] == "completed" for task in tasks.values()), "active_workers": active, "blocked_workers": blocked, "event_count": len(events), "runner_identity": runner_identity or "not-dispatched", "execution_mode": mode, "estimated_cost": str(session.estimated_cost), "last_sequence": events[-1].sequence if events else 0}
    return {"session": {"id": str(session.id), "mission": session.mission, "status": hud["session_status"]}, "hud": hud, "buildings": buildings, "workers": list(workers.values()), "tasks": list(tasks.values()), "evidence": evidence, "activity": [_safe_event(event) for event in events[-12:]]}


def project_city_frames(session):
    """Build a replayable city frame for every confirmed event, without a replay store."""
    return [project_city(session, event.sequence) for event in session.events.all()]

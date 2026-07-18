import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.db import connections
from django.test import TestCase, override_settings

from .eventing import EVENT_TYPES
from .models import ProjectSession
from .projection import project_city, project_city_frames
from .services import restore_codex_baseline, run_fixed_test, scan_workspace, workspace_changes


class AgentLandP0Tests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        source = Path(__file__).resolve().parents[3] / "townos_demo_workspace" / "whiteboard"
        self.workspace = Path(self.tempdir.name) / "whiteboard"
        shutil.copytree(source, self.workspace)
        self.settings_override = override_settings(AGENTLAND_WORKSPACE_ROOT=str(self.workspace))
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        self.tempdir.cleanup()

    def create_session(self):
        response = self.client.post("/agentland/sessions/", data='{"mission":"Repair whiteboard room isolation"}', content_type="application/json")
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_event_allowlist_is_closed(self):
        self.assertEqual(len(EVENT_TYPES), 21)
        self.assertIn("file.modified", EVENT_TYPES)
        self.assertNotIn("agent.thought", EVENT_TYPES)

    def test_deterministic_dispatch_is_ordered_idempotent_and_restart_safe(self):
        session = self.create_session()
        session_id = session["id"]
        response = self.client.post(f"/agentland/sessions/{session_id}/dispatch-builder/", data='{"runner_identity":"deterministic"}', content_type="application/json", headers={"Idempotency-Key": "dispatch-1"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "completed")
        events = payload["events"]
        self.assertEqual([item["sequence"] for item in events], list(range(1, 17)))
        self.assertEqual(events[9]["type"], "file.modified")
        self.assertEqual(events[-1]["type"], "session.completed")
        self.assertEqual(payload["dispatches"][0]["runner_identity"], "deterministic")
        self.assertTrue(any(item["path"] == "whiteboard/index.html" for item in payload["artifacts"]))
        duplicate = self.client.post(f"/agentland/sessions/{session_id}/dispatch-builder/", data='{"runner_identity":"deterministic"}', content_type="application/json", headers={"Idempotency-Key": "dispatch-1"})
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(len(duplicate.json()["events"]), 16)
        before_restart = self.client.get(f"/agentland/sessions/{session_id}/snapshot/").json()
        connections.close_all()
        after_restart = self.client.get(f"/agentland/sessions/{session_id}/snapshot/").json()
        self.assertEqual(before_restart, after_restart)

    def test_workspace_evidence_is_relative_and_detects_real_change(self):
        before = scan_workspace()
        target = self.workspace / "src" / "rooms.js"
        target.write_text(target.read_text(encoding="utf-8").replace("return strokes;", "return [] ;"), encoding="utf-8")
        after = scan_workspace()
        records = workspace_changes(before, after)
        self.assertEqual(records, [("file.modified", "src/rooms.js", before["src/rooms.js"], after["src/rooms.js"])])
        self.assertFalse(any(str(self.workspace) in record[1] for record in records))

    def test_codex_baseline_restore_reintroduces_a_real_failure(self):
        target = self.workspace / "src" / "rooms.js"
        target.write_text(target.read_text(encoding="utf-8").replace("return strokes;", "return strokes.filter((stroke) => stroke.roomId === roomId);"), encoding="utf-8")
        self.assertEqual(run_fixed_test(self.workspace)["exit_code"], 0)
        restore_codex_baseline()
        self.assertNotEqual(run_fixed_test(self.workspace)["exit_code"], 0)

    def test_missing_idempotency_header_is_rejected(self):
        session = self.create_session()
        response = self.client.post(f"/agentland/sessions/{session['id']}/dispatch-builder/", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    @patch("agentland.services.CodexWorkerRunner")
    def test_denied_codex_launch_is_persisted_without_downstream_evidence(self, runner_class):
        runner_class.return_value.run.return_value = {
            "exit_code": -1, "duration_ms": 0, "stdout": "", "stderr": "[WinError 5] Access is denied",
            "cli_version": "unavailable", "codex_started": False,
            "error_category": "process_launch_denied", "platform_error": "WinError 5",
        }
        session = self.create_session()
        response = self.client.post(
            "/agentland/sessions/{0}/dispatch-builder/".format(session["id"]),
            data='{"runner_identity":"codex"}', content_type="application/json",
            headers={"Idempotency-Key": "codex-denied"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        dispatch = payload["dispatches"][0]
        self.assertEqual(dispatch["status"], "blocked")
        self.assertEqual(dispatch["runner_identity"], "codex")
        self.assertEqual(dispatch["error_category"], "process_launch_denied")
        self.assertEqual(dispatch["exit_code"], -1)
        self.assertEqual(dispatch["platform_error"], "WinError 5")
        self.assertFalse(dispatch["codex_started"])
        self.assertEqual([event["type"] for event in payload["events"]][-2:], ["tool.failed", "task.failed"])
        forbidden = {"file.created", "file.modified", "file.deleted", "test.started", "test.passed", "test.failed", "artifact.created"}
        self.assertFalse(forbidden.intersection(event["type"] for event in payload["events"]))
        city = project_city(ProjectSession.objects.get(id=session["id"]))
        self.assertEqual(city["buildings"]["code_factory"], "blocked")
        self.assertEqual(next(worker for worker in city["workers"] if worker["role"] == "builder")["status"], "blocked")

    def test_city_projection_is_derived_from_persisted_events(self):
        session = self.create_session()
        response = self.client.post(
            "/agentland/sessions/{0}/dispatch-builder/".format(session["id"]),
            data='{"runner_identity":"deterministic"}', content_type="application/json",
            headers={"Idempotency-Key": "city-source"},
        )
        self.assertEqual(response.status_code, 200)
        page = self.client.get("/agentland/sessions/{0}/city/".format(session["id"]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Code Factory")
        self.assertContains(page, "Event playback")
        persisted = ProjectSession.objects.get(id=session["id"])
        stored = project_city(persisted)
        self.assertEqual(stored["buildings"]["testing_facility"], "passed")
        artifact = persisted.artifacts.get()
        preview = self.client.get("/agentland/sessions/{0}/artifacts/{1}/preview/".format(session["id"], artifact.id))
        self.assertEqual(preview.status_code, 200)
        self.assertNotIn("townos_demo_workspace", preview.content.decode("utf-8"))
        frames = project_city_frames(persisted)
        self.assertEqual(len(frames), 16)
        self.assertEqual(frames[8]["buildings"]["code_factory"], "active")
        self.assertEqual(frames[8]["evidence"]["files"], [])
        self.assertEqual(frames[9]["evidence"]["files"][0]["path"], "src/rooms.js")
        self.assertEqual(frames[-1], stored)

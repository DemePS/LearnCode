"""Smoke-test the LearnCode backend in-process (Swagger/OpenAPI + every endpoint), with the
agent mocked so no live model call is made. Run from LearnCode/backend/ with:
    .venv/Scripts/python test_backend_swagger.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, ".")

# store.py writes a per-session folder (backend/data/workspaces/<id>); point it at the temp dir so
# the test never touches the project. Must be set BEFORE importing app.store.
os.environ.setdefault("LEARNCODE_DATA_DIR", tempfile.mkdtemp(prefix="learncode-test-"))

import _mock_agent_server  # noqa: F401  (monkeypatches seedagent.session before app import)
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

failures = []


def check(label, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{(' -- ' + extra) if extra else ''}")
    if not cond:
        failures.append(label)


print("=== 1. OpenAPI / Swagger ===")
r = client.get("/openapi.json")
check("/openapi.json returns 200", r.status_code == 200, str(r.status_code))
paths = r.json().get("paths", {})
for p, ops in sorted(paths.items()):
    print("   ", p, sorted(ops.keys()))

expected_paths = {
    "/api/learn/clarify",
    "/api/learn/start",
    "/api/learn/{session_id}",
    "/api/learn/{session_id}/action",
    "/api/learn/{session_id}/ask",
}
check("all 5 API paths documented", expected_paths <= set(paths),
      f"missing={expected_paths - set(paths)}")

r = client.get("/docs")
check("/docs (Swagger UI) returns 200", r.status_code == 200, str(r.status_code))
check("/docs serves swagger-ui", "swagger" in r.text.lower())

print()
print("=== 2. POST /api/learn/start ===")
r = client.post("/api/learn/start", json={"objective": "learn FastAPI", "history": []})
check("start returns 200", r.status_code == 200, str(r.status_code))
body = r.json()
session_id = body.get("session_id")
page = body.get("page") or {}
check("start returns a session_id", bool(session_id), str(session_id))
check("page has title + blocks", "title" in page and isinstance(page.get("blocks"), list))
print("    title:", page.get("title"))
print("    block types:", [b.get("type") for b in page.get("blocks", [])])

check("start with empty objective -> 400",
      client.post("/api/learn/start", json={"objective": "   ", "history": []}).status_code == 400)

print()
print("=== 3. GET /api/learn/{session_id} (refetch) ===")
r = client.get(f"/api/learn/{session_id}")
check("refetch returns 200", r.status_code == 200, str(r.status_code))
check("refetch returns the same title", (r.json().get("page") or {}).get("title") == page.get("title"))
check("unknown session -> 404", client.get("/api/learn/deadbeef").status_code == 404)

print()
print("=== 4. POST /api/learn/{session_id}/action (open exercise) ===")
r = client.post(f"/api/learn/{session_id}/action",
                json={"action": "open_ex1", "payload": {}})
check("action returns 200", r.status_code == 200, str(r.status_code))
page2 = r.json().get("page") or {}
types2 = [b.get("type") for b in page2.get("blocks", [])]
print("    title:", page2.get("title"), "| blocks:", types2)
check("opening an exercise yields a code_editor block", "code_editor" in types2)
ce = next((b for b in page2.get("blocks", []) if b.get("type") == "code_editor"), {})
check("code_editor carries starter_code", bool(ce.get("starter_code")))
check("code_editor carries submit_action", bool(ce.get("submit_action")))

print()
print("=== 5. POST /api/learn/{session_id}/action (submit code) ===")
r = client.post(f"/api/learn/{session_id}/action",
                json={"action": "submit_ex1",
                      "payload": {"exercise_id": "ex1", "file_name": "models.py",
                                  "code": "class Link: pass\n"}})
check("submit returns 200", r.status_code == 200, str(r.status_code))
types3 = [b.get("type") for b in (r.json().get("page") or {}).get("blocks", [])]
print("    blocks:", types3)
check("submission yields a feedback block", "feedback" in types3)
check("empty action -> 400",
      client.post(f"/api/learn/{session_id}/action", json={"action": "  "}).status_code == 400)
check("action on unknown session -> 404",
      client.post("/api/learn/deadbeef/action", json={"action": "x"}).status_code == 404)

print()
print("=== 6. POST /api/learn/{session_id}/ask (help widget) ===")
r = client.post(f"/api/learn/{session_id}/ask", json={"question": "why?", "context": {}})
check("ask returns 200", r.status_code == 200, str(r.status_code))
check("ask returns an answer key", "answer" in (r.json() if r.status_code == 200 else {}))
check("empty question -> 400",
      client.post(f"/api/learn/{session_id}/ask", json={"question": ""}).status_code == 400)

print()
print("=== 7. Validation / schema ===")
check("start without objective -> 422 (pydantic)",
      client.post("/api/learn/start", json={}).status_code == 422)

print()
print("=== 8. Ask does not overwrite the last rendered page ===")
before = (client.get(f"/api/learn/{session_id}").json().get("page") or {}).get("title")
after = (client.get(f"/api/learn/{session_id}").json().get("page") or {}).get("title")
check("/ask left last_page untouched", before == after, f"{before!r} -> {after!r}")

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED")

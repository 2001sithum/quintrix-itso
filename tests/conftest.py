"""Shared fixtures. Every test runs against a throwaway DB and archive so a
run can never touch the operator's real data.

The redirection happens at **module import time**, not in a fixture. pytest
imports every conftest before it imports any test module, and a test module
that does `from engine import jobs` at the top level pulls in
`engine.storage_manager` during collection — long before any fixture runs.
`storage_manager` reads `QUINTRIX_DB` and `QUINTRIX_ARCHIVE` at import and
creates its archive directories as a side effect, so setting them in a fixture
was too late: a collection-time import bound the *real* database and the suite
wrote fake projects into it.
"""
import os
import shutil
import subprocess
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Must precede any import of engine.storage_manager — see module docstring.
_SANDBOX = tempfile.mkdtemp(prefix="itso-tests-")
os.environ["QUINTRIX_DB"] = os.path.join(_SANDBOX, "test.db")
os.environ["QUINTRIX_ARCHIVE"] = os.path.join(_SANDBOX, "archive")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SANDBOX, ignore_errors=True)


@pytest.fixture(scope="session")
def sandbox():
    """The throwaway working directory bound at import time."""
    import pathlib
    return pathlib.Path(_SANDBOX)


def test_sandbox_is_actually_isolated():
    """Guard rail: if this ever fails, the suite is writing to the real DB."""
    from engine import storage_manager
    assert storage_manager.DB_PATH.startswith(_SANDBOX), (
        f"tests are bound to {storage_manager.DB_PATH!r}, not the sandbox — "
        "something imported engine.storage_manager before conftest ran")


@pytest.fixture(scope="session")
def sm(sandbox):
    from engine import storage_manager
    storage_manager.init_db()
    return storage_manager


@pytest.fixture(scope="session")
def has_ffmpeg():
    return shutil.which("ffmpeg") is not None


@pytest.fixture(scope="session")
def tiny_video(sandbox, has_ffmpeg):
    """A 6-second synthetic clip with real motion: a box moving across frame.

    Generated rather than committed so the suite has no binary fixtures and
    runs identically on any machine with ffmpeg.
    """
    if not has_ffmpeg:
        pytest.skip("ffmpeg not available")
    path = str(sandbox / "moving.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "color=c=#101820:s=320x240:d=6:r=15",
        "-f", "lavfi", "-i", "color=c=#e0e0e0:s=40x40:d=6:r=15",
        "-filter_complex", "[0][1]overlay=x='(t/6)*280':y=100",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-g", "15", path,
    ], capture_output=True, check=True)
    return path


@pytest.fixture(scope="session")
def still_video(sandbox, has_ffmpeg):
    """A 6-second clip with no motion whatsoever — the case that exposed the
    Invaligator warm-up defect."""
    if not has_ffmpeg:
        pytest.skip("ffmpeg not available")
    path = str(sandbox / "still.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=#203040:s=320x240:d=6:r=15",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-g", "15", path,
    ], capture_output=True, check=True)
    return path


@pytest.fixture(scope="session")
def client(sm):
    from fastapi.testclient import TestClient
    import server
    return TestClient(server.app)


@pytest.fixture(scope="session")
def auth(client):
    r = client.post("/api/login", data={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return {"x-session": r.json()["token"]}


@pytest.fixture(scope="session")
def operator(client, auth, sm):
    """A SecurityOperator and a plain User, for role-boundary checks."""
    made = {}
    for name, role in (("op_test", "SecurityOperator"), ("user_test", "User")):
        client.post("/api/register", data={"username": name, "full_name": name,
                                           "password": "Str0ng!Pass1"})
        uid = sm.q("SELECT id FROM users WHERE username=?", (name,), one=True)["id"]
        client.post(f"/api/admin/users/{uid}/approve", headers=auth,
                    data={"action": "approve", "role": role})
        tok = client.post("/api/login", data={"username": name,
                                              "password": "Str0ng!Pass1"}).json()["token"]
        made[role] = {"x-session": tok}
    return made



@pytest.fixture(scope="session")
def mk_project(sm):
    """Factory for container projects.

    A footage must belong to a project, so any test that inserts a footage row
    directly needs a parent. Session-scoped so module-scoped fixtures can use it.
    """
    import uuid as _u

    def _make(name="Test Project"):
        pid = _u.uuid4().hex
        sm.execute("INSERT INTO projects(id,name,description,owner_id,created_at) "
                   "VALUES(?,?,?,?,?)", (pid, f"{name} {pid[:6]}", "", 1, sm.now()))
        return pid
    return _make

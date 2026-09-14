"""Projects as containers, footages as recordings.

A project holds many footages; a footage holds many segments. Nothing derived
may outlive its parent, and an upload may never end up without one.
"""
import os
import sqlite3
import uuid

import pytest


# --------------------------------------------------------------------------
# Schema shape
# --------------------------------------------------------------------------
def test_child_tables_reference_a_footage(sm):
    """A segment belongs to a footage, not to a project — the container has no
    media of its own."""
    with sm.connect() as con:
        for table in ("segments", "events", "alerts", "stage_stats", "purge_queue"):
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            assert "footage_id" in cols, f"{table} should hang off a footage"
            assert "project_id" not in cols, f"{table} still references a project"


def test_footage_references_a_project(sm):
    with sm.connect() as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(footages)")]
        assert "project_id" in cols
        proj = [r[1] for r in con.execute("PRAGMA table_info(projects)")]
        # the container must NOT carry media columns
        assert "filename" not in proj and "original_path" not in proj


def test_deleting_a_project_cascades_to_footages_and_segments(sm, mk_project):
    pid = mk_project("Cascade")
    fid, sid = uuid.uuid4().hex, uuid.uuid4().hex
    sm.execute("INSERT INTO footages(id,project_id,name,filename,original_path,"
               "status,created_at) VALUES(?,?,?,?,?,?,?)",
               (fid, pid, "f", "f.mp4", "", "done", sm.now()))
    sm.execute("INSERT INTO segments(id,footage_id,idx,ts,motion,tier) "
               "VALUES(?,?,0,?,1,'LOW')", (sid, fid, sm.now()))
    sm.execute("DELETE FROM projects WHERE id=?", (pid,))
    assert sm.q("SELECT 1 FROM footages WHERE id=?", (fid,), one=True) is None
    assert sm.q("SELECT 1 FROM segments WHERE id=?", (sid,), one=True) is None


def test_a_footage_cannot_reference_a_missing_project(sm):
    with pytest.raises(sqlite3.IntegrityError):
        sm.execute("INSERT INTO footages(id,project_id,name,filename,original_path,"
                   "status,created_at) VALUES(?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex, "no-such-project", "f", "f.mp4", "",
                    "done", sm.now()))


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
def test_project_list_returns_containers_not_recordings(client, auth):
    """REGRESSION of intent: the list page must stay a list of projects. If
    this ever returns footage rows, the flat-wall problem is back."""
    d = client.get("/api/projects", headers=auth).json()
    assert "projects" in d and "totals" in d
    for p in d["projects"]:
        assert "footages" in p, "each row should report how many footages it holds"
        assert "filename" not in p, "a project is a container, not a recording"


def test_project_rollup_sums_its_footages(client, auth, sm, mk_project):
    pid = mk_project("Rollup")
    for i, (ob, sb) in enumerate([(1000, 250), (3000, 750)]):
        sm.execute("INSERT INTO footages(id,project_id,name,filename,original_path,"
                   "status,created_at,original_bytes,stored_bytes,duration) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex, pid, f"f{i}", "f.mp4", "", "done",
                    sm.now(), ob, sb, 10))
    d = client.get(f"/api/projects/{pid}", headers=auth).json()
    p = d["project"]
    assert p["footages"] == 2
    assert p["uploaded_bytes"] == 4000
    assert p["stored_bytes"] == 1000
    assert p["savings_percent"] == pytest.approx(75.0)
    assert p["duration"] == pytest.approx(20)
    assert len(d["footages"]) == 2


def test_creating_a_project(client, auth):
    name = "Site " + uuid.uuid4().hex[:6]
    r = client.post("/api/projects", headers=auth, data={"name": name})
    assert r.status_code == 200
    pid = r.json()["id"]
    assert client.get(f"/api/projects/{pid}", headers=auth).status_code == 200


def test_duplicate_project_names_are_rejected(client, auth):
    name = "Dup " + uuid.uuid4().hex[:6]
    assert client.post("/api/projects", headers=auth, data={"name": name}).status_code == 200
    assert client.post("/api/projects", headers=auth, data={"name": name}).status_code == 400


def test_renaming_a_project(client, auth):
    pid = client.post("/api/projects", headers=auth,
                      data={"name": "Before " + uuid.uuid4().hex[:6]}).json()["id"]
    new = "After " + uuid.uuid4().hex[:6]
    assert client.patch(f"/api/projects/{pid}", headers=auth,
                        data={"name": new}).status_code == 200
    assert client.get(f"/api/projects/{pid}", headers=auth).json()["project"]["name"] == new


def test_upload_without_a_project_lands_in_unsorted(client, auth, sandbox, has_ffmpeg):
    """A footage must always have a parent — an orphan would be unreachable in
    a project-first UI."""
    if not has_ffmpeg:
        pytest.skip("ffmpeg needed")
    import subprocess
    clip = str(sandbox / "unsorted.mp4")
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                    "-i", "color=c=black:s=160x120:d=1:r=10", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", clip],
                   capture_output=True, check=True)
    with open(clip, "rb") as f:
        r = client.post("/api/footages", headers=auth,
                        files={"file": ("unsorted.mp4", f, "video/mp4")})
    assert r.status_code == 200
    body = r.json()
    assert body["project_id"], "upload must be assigned to a project"
    name = client.get(f"/api/projects/{body['project_id']}",
                      headers=auth).json()["project"]["name"]
    assert name == "Unsorted"


def test_upload_into_a_named_project_creates_it_once(client, auth, sandbox, has_ffmpeg):
    if not has_ffmpeg:
        pytest.skip("ffmpeg needed")
    import subprocess
    clip = str(sandbox / "named.mp4")
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                    "-i", "color=c=black:s=160x120:d=1:r=10", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", clip],
                   capture_output=True, check=True)
    name = "Named " + uuid.uuid4().hex[:6]
    ids = []
    for _ in range(2):
        with open(clip, "rb") as f:
            ids.append(client.post("/api/footages", headers=auth,
                                   files={"file": ("named.mp4", f, "video/mp4")},
                                   data={"project_name": name}).json()["project_id"])
    assert ids[0] == ids[1], "find-or-create should not make a second project"


def test_upload_into_a_missing_project_404s(client, auth, sandbox, has_ffmpeg):
    if not has_ffmpeg:
        pytest.skip("ffmpeg needed")
    clip = str(sandbox / "named.mp4")
    if not os.path.exists(clip):
        pytest.skip("fixture clip not built")
    with open(clip, "rb") as f:
        r = client.post("/api/footages", headers=auth,
                        files={"file": ("x.mp4", f, "video/mp4")},
                        data={"project_id": "nope"})
    assert r.status_code == 404


def test_a_footage_can_move_between_projects(client, auth, sm, mk_project):
    a, b = mk_project("From"), mk_project("To")
    fid = uuid.uuid4().hex
    sm.execute("INSERT INTO footages(id,project_id,name,filename,original_path,"
               "status,created_at) VALUES(?,?,?,?,?,?,?)",
               (fid, a, "movable", "m.mp4", "", "done", sm.now()))
    assert client.patch(f"/api/footages/{fid}", headers=auth,
                        data={"project_id": b}).status_code == 200
    assert sm.q("SELECT project_id FROM footages WHERE id=?",
                (fid,), one=True)["project_id"] == b


def test_moving_to_a_missing_project_404s(client, auth, sm, mk_project):
    fid = uuid.uuid4().hex
    sm.execute("INSERT INTO footages(id,project_id,name,filename,original_path,"
               "status,created_at) VALUES(?,?,?,?,?,?,?)",
               (fid, mk_project("Stay"), "f", "f.mp4", "", "done", sm.now()))
    assert client.patch(f"/api/footages/{fid}", headers=auth,
                        data={"project_id": "nope"}).status_code == 404


def test_footage_list_can_be_scoped_to_a_project(client, auth, sm, mk_project):
    pid = mk_project("Scoped")
    for i in range(3):
        sm.execute("INSERT INTO footages(id,project_id,name,filename,original_path,"
                   "status,created_at) VALUES(?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex, pid, f"s{i}", "s.mp4", "", "done", sm.now()))
    rows = client.get(f"/api/footages?project={pid}", headers=auth).json()
    assert len(rows) == 3
    assert all(r["project_id"] == pid for r in rows)


def test_deleting_a_project_via_api_reports_footage_count(client, auth, sm, mk_project):
    pid = mk_project("Doomed")
    for i in range(2):
        sm.execute("INSERT INTO footages(id,project_id,name,filename,original_path,"
                   "status,created_at) VALUES(?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex, pid, f"d{i}", "d.mp4", "", "done", sm.now()))
    r = client.delete(f"/api/projects/{pid}", headers=auth)
    assert r.status_code == 200
    assert r.json()["footages_deleted"] == 2
    assert client.get(f"/api/projects/{pid}", headers=auth).status_code == 404


def test_overview_is_footage_level(client, auth):
    d = client.get("/api/overview/footages", headers=auth).json()
    assert "footages" in d and "totals" in d
    for f in d["footages"]:
        assert "project_id" in f and "stages" in f

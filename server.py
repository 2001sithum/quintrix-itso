"""
server.py — REST API + static UI host. Implements endpoints for all 60 FRs.
Run: uvicorn server:app --reload
"""
import os, uuid, json, hashlib, secrets, threading, datetime as dt
from typing import Optional

# macOS python.org builds ship without a default CA bundle, which makes
# urllib/torch.hub model downloads fail with CERTIFICATE_VERIFY_FAILED.
# Point OpenSSL at certifi's bundle so first-run model downloads succeed.
os.environ.setdefault("SSL_CERT_FILE", __import__("certifi").where())

import cv2
from fastapi import (FastAPI, UploadFile, File, Form, HTTPException, Request,
                     Depends, Query)
from fastapi.responses import (FileResponse, StreamingResponse, JSONResponse,
                               HTMLResponse)
from fastapi.staticfiles import StaticFiles

from engine import storage_manager as sm
from engine import itso_engine
from engine import jobs
from engine import modes as itso_modes
from engine import fusion
# aliased: the /api/analytics route handler is also named `analytics`,
# and a module-level def shadows a module-level import.
from engine import analytics as itso_analytics
from models import sentiment_model

sm.init_db()
DEVICE = itso_engine.select_device()

# Tier-3 safeguard: sweep expired holds out of the review queue in the
# background. Started once, here, so the deletion deadline is enforced by the
# running service rather than by whoever remembers to call an endpoint.
itso_engine.start_purge_worker()
jobs.limit_torch_threads()
# The worker pool lives in this process, so anything in flight when it last
# stopped would otherwise sit in `queued`/`processing` forever.
jobs.recover_orphans()

# seed default admin
def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

if not sm.q("SELECT 1 FROM users WHERE username='admin'", one=True):
    sm.execute("INSERT INTO users(username,full_name,password_hash,role,status,created_at)"
               " VALUES(?,?,?,?,?,?)",
               ("admin", "System Administrator", _hash("admin123"),
                "Administrator", "active", sm.now()))
    sm.log("system", "Default admin account created")

app = FastAPI(title="Quintrix ITSO Engine")

BASE = os.path.dirname(__file__)


# ---------------------------------------------------------------------------
# API request logging middleware (FR54)
# ---------------------------------------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/api"):
        sm.log("api", f"{request.method} {request.url.path}",
               context={"status": resp.status_code})
    return resp


# ---------------------------------------------------------------------------
# Auth helpers (FR04-07, 24)
# ---------------------------------------------------------------------------
def current_user(request: Request):
    # EventSource can't send custom headers, so the SSE log stream is
    # reached with ?session=TOKEN instead — accepted only for that reason.
    token = (request.headers.get("x-session") or request.cookies.get("session")
             or request.query_params.get("session"))
    if not token:
        return None
    row = sm.q("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
               "WHERE s.token=?", (token,), one=True)
    return row


def require(request: Request, roles=None):
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Authentication required")
    if roles and u["role"] not in roles:
        raise HTTPException(403, "Access denied for role " + u["role"])   # FR07
    return u


# ---------------------------------------------------------------------------
# FR01 Registration + FR02 password strength
# ---------------------------------------------------------------------------
def password_strength(pw: str):
    import re
    score = 0
    checks = {"length": len(pw) >= 8, "upper": bool(re.search(r"[A-Z]", pw)),
              "lower": bool(re.search(r"[a-z]", pw)), "digit": bool(re.search(r"\d", pw)),
              "special": bool(re.search(r"[^A-Za-z0-9]", pw))}
    score = sum(checks.values())
    level = "strong" if score >= 5 else "medium" if score >= 3 else "weak"
    color = {"weak": "red", "medium": "yellow", "strong": "green"}[level]
    return {"level": level, "color": color, "score": score, "checks": checks}


@app.post("/api/password-strength")            # FR02 (real-time)
async def api_pw_strength(password: str = Form(...)):
    return password_strength(password)


@app.post("/api/register")                     # FR01 + FR03
async def register(username: str = Form(...), full_name: str = Form(""),
                   password: str = Form(...)):
    if sm.q("SELECT 1 FROM users WHERE username=?", (username,), one=True):
        raise HTTPException(400, "Username already exists")
    st = password_strength(password)
    if st["level"] == "weak":
        raise HTTPException(400, "Password too weak")
    sm.execute("INSERT INTO users(username,full_name,password_hash,role,status,created_at)"
               " VALUES(?,?,?,?,?,?)",
               (username, full_name, _hash(password), "User", "pending", sm.now()))
    sm.log("system", f"Registration request: {username} (awaiting admin approval)")
    return {"status": "pending", "message": "Account created, awaiting admin approval"}


@app.post("/api/login")                        # FR04/05/24
async def login(username: str = Form(...), password: str = Form(...)):
    u = sm.q("SELECT * FROM users WHERE username=?", (username,), one=True)
    if not u or u["password_hash"] != _hash(password):
        sm.log("system", f"Failed login: {username}", severity="warning")
        raise HTTPException(401, "Invalid credentials")
    if u["status"] != "active":
        raise HTTPException(403, f"Account {u['status']}")
    token = secrets.token_hex(24)
    sm.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)",
               (token, u["id"], sm.now()))
    sm.log("system", f"Login: {username}", user=username)
    return {"token": token, "role": u["role"], "username": u["username"],
            "full_name": u["full_name"]}


@app.post("/api/logout")                       # FR06
async def logout(request: Request):
    token = request.headers.get("x-session")
    if token:
        sm.execute("DELETE FROM sessions WHERE token=?", (token,))
    return {"status": "logged_out"}


@app.get("/api/me")
async def me(request: Request):
    u = require(request)
    return {"username": u["username"], "role": u["role"], "full_name": u["full_name"]}


# ---------------------------------------------------------------------------
# Admin: user approval + management (FR03, 56)
# ---------------------------------------------------------------------------
@app.get("/api/admin/users")
async def list_users(request: Request):
    require(request, ["Administrator"])
    return sm.q("SELECT id,username,full_name,role,status,created_at FROM users ORDER BY id")


@app.post("/api/admin/users/{uid}/approve")
async def approve_user(uid: int, request: Request, action: str = Form(...),
                       role: str = Form("User")):
    require(request, ["Administrator"])
    status = "active" if action == "approve" else "rejected"
    sm.execute("UPDATE users SET status=?, role=? WHERE id=?", (status, role, uid))
    sm.log("audit", f"User {uid} {status}", context={"role": role})
    return {"status": status}


ROLES = ("Administrator", "SecurityOperator", "User")


def _active_admin_count(exclude_uid=None):
    row = sm.q("SELECT COUNT(*) n FROM users WHERE role='Administrator' AND status='active'"
               + (" AND id!=?" if exclude_uid else ""),
               (exclude_uid,) if exclude_uid else ())
    return row[0]["n"]


@app.patch("/api/admin/users/{uid}")            # FR56 admin user control
async def update_user(uid: int, request: Request, full_name: Optional[str] = Form(None),
                      role: Optional[str] = Form(None)):
    require(request, ["Administrator"])
    target = sm.q("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not target:
        raise HTTPException(404)
    if role is not None:
        if role not in ROLES:
            raise HTTPException(400, "Invalid role")
        if target["role"] == "Administrator" and role != "Administrator" \
                and _active_admin_count(exclude_uid=uid) == 0:
            raise HTTPException(400, "Cannot demote the last active Administrator")
    fields, args = [], []
    if full_name is not None:
        fields.append("full_name=?"); args.append(full_name)
    if role is not None:
        fields.append("role=?"); args.append(role)
    if fields:
        args.append(uid)
        sm.execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", tuple(args))
    sm.log("audit", f"User {uid} updated", context={"full_name": full_name, "role": role})
    return {"status": "updated"}


@app.delete("/api/admin/users/{uid}")           # FR56 admin user control
async def delete_user(uid: int, request: Request):
    u = require(request, ["Administrator"])
    target = sm.q("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not target:
        raise HTTPException(404)
    if target["id"] == u["id"]:
        raise HTTPException(400, "Cannot delete your own account")
    if target["role"] == "Administrator" and target["status"] == "active" \
            and _active_admin_count(exclude_uid=uid) == 0:
        raise HTTPException(400, "Cannot delete the last active Administrator")
    sm.execute("DELETE FROM users WHERE id=?", (uid,))     # cascades sessions
    sm.log("audit", f"User deleted: {target['username']}")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# FR08 upload + FR29 async processing
# ---------------------------------------------------------------------------
ALLOWED = {".mp4", ".avi", ".mov", ".mkv"}
MAX_BYTES = 500 * 1024 * 1024


DEFAULT_PROJECT_NAME = "Unsorted"


def _ensure_project(name, owner_id, description=""):
    """Find or create a project by name. Uploads always land in a project, so
    there is never an orphan footage; without a name they go to "Unsorted"."""
    name = (name or DEFAULT_PROJECT_NAME).strip() or DEFAULT_PROJECT_NAME
    row = sm.q("SELECT id FROM projects WHERE name=?", (name,), one=True)
    if row:
        return row["id"]
    pid = uuid.uuid4().hex
    sm.execute("INSERT INTO projects(id,name,description,owner_id,created_at) "
               "VALUES(?,?,?,?,?)", (pid, name, description, owner_id, sm.now()))
    return pid


def _project_rollup(pid):
    """Totals for one project, summed over its footages."""
    r = sm.q("SELECT COUNT(*) n, SUM(original_bytes) ob, SUM(stored_bytes) sb, "
             "SUM(duration) dur, SUM(processing_ms) ms "
             "FROM footages WHERE project_id=?", (pid,), one=True) or {}
    segs = sm.q("SELECT COUNT(*) n, SUM(motion) m FROM segments WHERE footage_id IN "
                "(SELECT id FROM footages WHERE project_id=?)", (pid,), one=True) or {}
    tiers = sm.q("SELECT tier, COUNT(*) n FROM segments WHERE tier!='' AND footage_id IN "
                 "(SELECT id FROM footages WHERE project_id=?) GROUP BY tier", (pid,))
    status = sm.q("SELECT status, COUNT(*) n FROM footages WHERE project_id=? "
                  "GROUP BY status", (pid,))
    held = sm.q("SELECT COUNT(*) n FROM purge_queue WHERE status='pending' AND footage_id IN "
                "(SELECT id FROM footages WHERE project_id=?)", (pid,), one=True) or {}
    ob, sb = r.get("ob") or 0, r.get("sb") or 0
    by_status = {x["status"]: x["n"] for x in status}
    return {
        "footages": r.get("n") or 0,
        "uploaded_bytes": ob, "stored_bytes": sb,
        "savings_percent": round(100 * (1 - sb / ob), 2) if ob else 0.0,
        "duration": round(r.get("dur") or 0, 1),
        "processing_ms": round(r.get("ms") or 0, 1),
        "segments": segs.get("n") or 0, "motion_segments": segs.get("m") or 0,
        "tiers": {t["tier"]: t["n"] for t in tiers},
        "status": by_status,
        "busy": by_status.get("processing", 0) + by_status.get("queued", 0),
        "held": held.get("n") or 0,
    }


# ---------------------------------------------------------------------------
# Projects — containers that hold footages
# ---------------------------------------------------------------------------
@app.get("/api/projects")
async def list_projects(request: Request):
    """Projects with their rolled-up totals. Individual footages are *not*
    included — a project can hold many recordings and the list view should stay
    a list of projects. Fetch `/api/projects/{pid}` to see inside one."""
    require(request)
    rows = sm.q("SELECT * FROM projects ORDER BY created_at DESC")
    out = []
    for p in rows:
        out.append({**p, **_project_rollup(p["id"])})
    tot_ob = sum(o["uploaded_bytes"] for o in out)
    tot_sb = sum(o["stored_bytes"] for o in out)
    return {
        "projects": out,
        "totals": {
            "projects": len(out),
            "footages": sum(o["footages"] for o in out),
            "segments": sum(o["segments"] for o in out),
            "uploaded_bytes": tot_ob, "stored_bytes": tot_sb,
            "savings_percent": round(100 * (1 - tot_sb / tot_ob), 2) if tot_ob else 0.0,
            "busy": sum(o["busy"] for o in out),
            "held": sum(o["held"] for o in out),
        },
    }


@app.post("/api/projects")
async def create_project(request: Request, name: str = Form(...),
                         description: str = Form("")):
    u = require(request, ["Administrator", "SecurityOperator"])
    name = name.strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    if sm.q("SELECT 1 FROM projects WHERE name=?", (name,), one=True):
        raise HTTPException(400, "A project with that name already exists")
    pid = uuid.uuid4().hex
    sm.execute("INSERT INTO projects(id,name,description,owner_id,created_at) "
               "VALUES(?,?,?,?,?)", (pid, name, description.strip(), u["id"], sm.now()))
    sm.log("audit", f"Project created: {name}", user=u["username"])
    return {"id": pid, "name": name}


@app.get("/api/projects/{pid}")
async def project_detail(pid: str, request: Request):
    """A project and the footages inside it."""
    require(request)
    p = sm.q("SELECT * FROM projects WHERE id=?", (pid,), one=True)
    if not p:
        raise HTTPException(404)
    foots = sm.q("SELECT * FROM footages WHERE project_id=? ORDER BY created_at DESC", (pid,))
    for f in foots:
        seg = sm.q("SELECT COUNT(*) n, SUM(motion) m FROM segments WHERE footage_id=?",
                   (f["id"],), one=True) or {}
        tiers = sm.q("SELECT tier, COUNT(*) n FROM segments WHERE footage_id=? "
                     "AND tier!='' GROUP BY tier", (f["id"],))
        ob, sb = f.get("original_bytes") or 0, f.get("stored_bytes") or 0
        f["segments"] = seg.get("n") or 0
        f["motion_segments"] = seg.get("m") or 0
        # `profile` mirrors the overview endpoint's field name so the same
        # footage card renders identically wherever it is used.
        f["profile"] = f.get("pipeline_profile") or "legacy"
        f["tiers"] = {t["tier"]: t["n"] for t in tiers}
        f["savings_percent"] = round(100 * (1 - sb / ob), 2) if ob else 0.0
        f["has_telemetry"] = bool(sm.q("SELECT 1 FROM stage_stats WHERE footage_id=? LIMIT 1",
                                       (f["id"],), one=True))
        f["reprocessable"] = bool(f["original_path"] and os.path.exists(f["original_path"]))
        f["stages"] = sm.stage_report(f["id"])
    return {"project": {**p, **_project_rollup(pid)}, "footages": foots}


@app.patch("/api/projects/{pid}")
async def rename_project(pid: str, request: Request, name: Optional[str] = Form(None),
                         description: Optional[str] = Form(None)):
    u = require(request, ["Administrator", "SecurityOperator"])
    if not sm.q("SELECT 1 FROM projects WHERE id=?", (pid,), one=True):
        raise HTTPException(404)
    fields, args = [], []
    if name is not None:
        name = name.strip()
        if not name:
            raise HTTPException(400, "Name cannot be empty")
        clash = sm.q("SELECT id FROM projects WHERE name=? AND id!=?", (name, pid), one=True)
        if clash:
            raise HTTPException(400, "A project with that name already exists")
        fields.append("name=?"); args.append(name)
    if description is not None:
        fields.append("description=?"); args.append(description.strip())
    if fields:
        args.append(pid)
        sm.execute(f"UPDATE projects SET {','.join(fields)} WHERE id=?", tuple(args))
    sm.log("audit", f"Project updated: {pid}", user=u["username"],
           context={"name": name})
    return {"status": "updated", "name": name}


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str, request: Request):
    """Delete a project and every footage inside it, with their media."""
    u = require(request, ["Administrator", "SecurityOperator"])
    if not sm.q("SELECT 1 FROM projects WHERE id=?", (pid,), one=True):
        raise HTTPException(404)
    foots = sm.q("SELECT id FROM footages WHERE project_id=?", (pid,))
    for f in foots:
        _delete_footage_media(f["id"])
    sm.execute("DELETE FROM projects WHERE id=?", (pid,))   # cascades footages → segments
    sm.log("audit", f"Project deleted with {len(foots)} footage(s): {pid}",
           user=u["username"])
    return {"status": "deleted", "footages_deleted": len(foots)}


# ---------------------------------------------------------------------------
# Footages — one uploaded recording each (FR08 upload + FR29 async processing)
# ---------------------------------------------------------------------------
def _delete_footage_media(fid):
    """Remove every file a footage owns: tier artefacts, thumbnails, held
    purge files and the uploaded source."""
    for row in sm.q("SELECT stored_path, thumb FROM segments WHERE footage_id=?", (fid,)):
        for path in (row["stored_path"], row["thumb"]):
            if path and os.path.exists(path):
                try: os.remove(path)
                except OSError: pass
    for row in sm.q("SELECT path FROM purge_queue WHERE footage_id=?", (fid,)):
        if row["path"] and os.path.exists(row["path"]):
            try: os.remove(row["path"])
            except OSError: pass
    src = sm.q("SELECT original_path FROM footages WHERE id=?", (fid,), one=True)
    if src and src["original_path"] and os.path.exists(src["original_path"]):
        try: os.remove(src["original_path"])
        except OSError: pass


@app.post("/api/footages")
async def upload(request: Request, file: UploadFile = File(...),
                 project_id: Optional[str] = Form(None),
                 project_name: Optional[str] = Form(None)):
    """Upload a recording into a project.

    `project_id` targets an existing project; `project_name` finds-or-creates
    one by name; neither sends it to "Unsorted". A footage always belongs to a
    project, so the hierarchy can never have orphans.
    """
    u = require(request)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"Unsupported type {ext}")

    if project_id:
        if not sm.q("SELECT 1 FROM projects WHERE id=?", (project_id,), one=True):
            raise HTTPException(404, "No such project")
        pid = project_id
    else:
        pid = _ensure_project(project_name, u["id"])

    fid, jid = uuid.uuid4().hex, uuid.uuid4().hex
    dest = os.path.join(sm.ARCHIVE_DIR, "uploads", f"{fid}{ext}")
    size = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_BYTES:
                f.close(); os.remove(dest)
                raise HTTPException(400, "File exceeds 500MB")
            f.write(chunk)
    sm.execute("INSERT INTO footages(id,project_id,job_id,owner_id,name,filename,"
               "original_path,status,created_at,original_bytes) "
               "VALUES(?,?,?,?,?,?,?,?,?,?)",
               (fid, pid, jid, u["id"], os.path.splitext(file.filename)[0],
                file.filename, dest, "uploaded", sm.now(), size))
    sm.log("system", f"Upload: {file.filename}", user=u["username"],
           context={"footage": fid, "project": pid, "job": jid})
    position = jobs.submit(fid)
    return {"footage_id": fid, "project_id": pid, "job_id": jid,
            "status": "queued", "queue_position": position}


@app.get("/api/footages")
async def list_footages(request: Request, project: Optional[str] = None):
    require(request)
    where, args = [], []
    if project:
        where.append("f.project_id=?"); args.append(project)
    sql = ("SELECT f.*, p.name AS project_name, "
           "(SELECT COUNT(*) FROM segments s WHERE s.footage_id=f.id) segs "
           "FROM footages f LEFT JOIN projects p ON p.id=f.project_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sm.q(sql + " ORDER BY f.created_at DESC", tuple(args))


@app.get("/api/footages/{fid}")
async def footage_detail(fid: str, request: Request):
    require(request)
    f = sm.q("SELECT f.*, p.name AS project_name FROM footages f "
             "LEFT JOIN projects p ON p.id=f.project_id WHERE f.id=?", (fid,), one=True)
    if not f:
        raise HTTPException(404)
    segs = sm.q("SELECT * FROM segments WHERE footage_id=? ORDER BY idx", (fid,))
    for s in segs:
        s["objects"] = json.loads(s["objects"]); s["actions"] = json.loads(s["actions"])
        for k in ("fusion", "features"):
            try: s[k] = json.loads(s.get(k) or "{}")
            except Exception: s[k] = {}
    return {"footage": f, "segments": segs, "funnel": itso_analytics.footage_funnel(fid)}


@app.patch("/api/footages/{fid}")              # FR50 (rename)
async def rename_footage(fid: str, request: Request, name: Optional[str] = Form(None),
                         project_id: Optional[str] = Form(None)):
    """Rename a footage, or move it to a different project."""
    u = require(request, ["Administrator", "SecurityOperator"])
    if not sm.q("SELECT 1 FROM footages WHERE id=?", (fid,), one=True):
        raise HTTPException(404)
    fields, args = [], []
    if name is not None:
        name = name.strip()
        if not name:
            raise HTTPException(400, "Name cannot be empty")
        fields.append("name=?"); args.append(name)
    if project_id is not None:
        if not sm.q("SELECT 1 FROM projects WHERE id=?", (project_id,), one=True):
            raise HTTPException(404, "No such project")
        fields.append("project_id=?"); args.append(project_id)
    if fields:
        args.append(fid)
        sm.execute(f"UPDATE footages SET {','.join(fields)} WHERE id=?", tuple(args))
    sm.log("audit", f"Footage updated: {fid}", user=u["username"],
           context={"name": name, "project": project_id})
    return {"status": "updated", "name": name}


@app.delete("/api/footages/{fid}")             # FR27/50/51 deletion with cascade
async def delete_footage(fid: str, request: Request):
    u = require(request, ["Administrator", "SecurityOperator"])
    if not sm.q("SELECT 1 FROM footages WHERE id=?", (fid,), one=True):
        raise HTTPException(404)
    _delete_footage_media(fid)
    sm.execute("DELETE FROM footages WHERE id=?", (fid,))   # cascades segments/events
    sm.log("audit", f"Footage deleted: {fid}", user=u["username"])
    return {"status": "deleted"}


@app.delete("/api/segments/{sid}")             # FR51 cascade
async def delete_segment(sid: str, request: Request):
    require(request, ["Administrator", "SecurityOperator"])
    s = sm.q("SELECT stored_path,thumb FROM segments WHERE id=?", (sid,), one=True)
    if s:
        for p in (s["stored_path"], s["thumb"]):
            if p and os.path.exists(p):
                try: os.remove(p)
                except OSError: pass
    sm.execute("DELETE FROM segments WHERE id=?", (sid,))
    sm.log("audit", f"Segment deleted: {sid}")
    return {"status": "deleted"}


# FR58 manual tier override
@app.post("/api/segments/{sid}/tier")
async def manual_tier(sid: str, request: Request, tier: str = Form(...)):
    require(request, ["Administrator", "SecurityOperator"])
    if tier not in ("HIGH", "MEDIUM", "LOW"):
        raise HTTPException(400, "Invalid tier")
    sm.execute("UPDATE segments SET tier=?, tier_manual=1 WHERE id=?", (tier, sid))
    sm.log("audit", f"Manual tier {tier} applied to {sid}")
    return {"status": "updated", "tier": tier}


# ---------------------------------------------------------------------------
# Search (FR26, 34-37, 39)
# ---------------------------------------------------------------------------
@app.get("/api/search")
async def search(request: Request, start: Optional[str] = None, end: Optional[str] = None,
                 objects: Optional[str] = None, actions: Optional[str] = None,
                 tier: Optional[str] = None, min_ssig: float = 0.0,
                 max_ssig: float = 1.0, page: int = 1, page_size: int = 20):
    require(request)
    where, args = ["ssig>=? AND ssig<=?"], [min_ssig, max_ssig]
    if start: where.append("ts>=?"); args.append(start)
    if end: where.append("ts<=?"); args.append(end)
    if tier: where.append("tier=?"); args.append(tier)
    if objects: where.append("objects LIKE ?"); args.append(f"%{objects}%")
    if actions: where.append("actions LIKE ?"); args.append(f"%{actions}%")
    sql = "SELECT * FROM segments WHERE " + " AND ".join(where) + " ORDER BY ts DESC"
    rows = sm.q(sql, tuple(args))
    total = len(rows)
    rows = rows[(page - 1) * page_size: page * page_size]     # FR37 pagination
    for s in rows:
        s["objects"] = json.loads(s["objects"]); s["actions"] = json.loads(s["actions"])
    return {"total": total, "page": page, "page_size": page_size, "results": rows}


@app.get("/api/segments/{sid}")                # FR39 metadata view
async def segment_meta(sid: str, request: Request):
    require(request)
    s = sm.q("SELECT * FROM segments WHERE id=?", (sid,), one=True)
    if not s:
        raise HTTPException(404)
    s["objects"] = json.loads(s["objects"]); s["actions"] = json.loads(s["actions"])
    s["metadata"] = json.loads(s["metadata"])
    return s


# FR33 thumbnail, FR38 playback stream
@app.get("/api/thumb/{sid}")
async def thumb(sid: str):
    s = sm.q("SELECT thumb FROM segments WHERE id=?", (sid,), one=True)
    if not s or not s["thumb"] or not os.path.exists(s["thumb"]):
        raise HTTPException(404)
    return FileResponse(s["thumb"])


@app.get("/api/playback/{sid}")                # FR38
async def playback(sid: str):
    s = sm.q("SELECT stored_path FROM segments WHERE id=?", (sid,), one=True)
    if not s or not s["stored_path"] or not os.path.exists(s["stored_path"]):
        raise HTTPException(404, "No playable media (segment may be keyframe-only)")
    path = s["stored_path"]
    if path.endswith(".jpg"):
        return FileResponse(path)
    return FileResponse(path, media_type="video/mp4")


# ---------------------------------------------------------------------------
# Analytics dashboard (FR25, 31, 49, 52)
# ---------------------------------------------------------------------------
@app.get("/api/analytics")
async def analytics(request: Request):
    require(request)
    rep = sm.storage_report()
    proj = sm.q("SELECT status, COUNT(*) n FROM footages GROUP BY status")
    tiers = sm.q("SELECT tier, COUNT(*) n FROM segments WHERE tier!='' GROUP BY tier")
    threats = sm.q("SELECT threat_level, COUNT(*) n FROM segments "
                   "WHERE motion=1 GROUP BY threat_level")
    seg_total = sm.q("SELECT COUNT(*) n FROM segments", one=True)["n"]
    motion = sm.q("SELECT COUNT(*) n FROM segments WHERE motion=1", one=True)["n"]
    events = sm.q("SELECT COUNT(*) n FROM events", one=True)["n"]
    alerts = sm.q("SELECT COUNT(*) n FROM alerts WHERE status='new'", one=True)["n"]
    # Savings measured against the files operators actually uploaded, not
    # against the intermediate segment files the pipeline produced itself.
    tot = sm.q("SELECT SUM(original_bytes) ob, SUM(stored_bytes) sb, SUM(duration) d "
               "FROM footages WHERE status='done'", one=True) or {}
    uploaded, stored = tot.get("ob") or 0, tot.get("sb") or 0
    rep["uploaded_bytes"] = uploaded
    rep["true_savings_percent"] = round(100 * (1 - stored / uploaded), 2) if uploaded else 0.0
    reg = itso_analytics.model_registry()
    by_status = {r["status"]: r["n"] for r in proj}
    return {"storage": rep, "projects": by_status,
            "tiers": {r["tier"]: r["n"] for r in tiers},
            "threats": {r["threat_level"]: r["n"] for r in threats},
            "segments_total": seg_total, "segments_motion": motion,
            "events": events, "open_alerts": alerts, "device": DEVICE,
            "hours_analysed": round((tot.get("d") or 0) / 3600.0, 2),
            "queued_footages": by_status.get("queued", 0),
            "processing_footages": by_status.get("processing", 0),
            "queued_projects": by_status.get("queued", 0),
            "processing_projects": by_status.get("processing", 0),
            "job_queue": jobs.queue_depth(),
            "review_queue": sm.purge_summary(),
            "pipeline_profile": reg["active_profile"],
            "action_model": reg["deployed_action_model"],
            "sentiment_backend": sentiment_model.status()}


@app.get("/api/status")                        # FR49 (2s polling target)
async def proc_status(request: Request):
    require(request)
    active = sm.q("SELECT COUNT(*) n FROM footages WHERE status='processing'", one=True)["n"]
    queued = sm.q("SELECT COUNT(*) n FROM footages WHERE status='queued'", one=True)["n"]
    done = sm.q("SELECT COUNT(*) n FROM footages WHERE status='done'", one=True)["n"]
    segs = sm.q("SELECT COUNT(*) n FROM segments", one=True)["n"]
    return {"active_footages": active, "queued_footages": queued,
            "completed_footages": done, "segments_processed": segs,
            # kept under the old keys too so existing callers keep working
            "active_projects": active, "queued_projects": queued,
            "completed_projects": done,
            "queue": jobs.queue_depth(), "device": DEVICE, "ts": sm.now()}


@app.get("/api/storage")                       # FR31/52
async def storage(request: Request):
    require(request)
    return sm.storage_report()


# ---------------------------------------------------------------------------
# Events + Alerts (FR17, 41-43)
# ---------------------------------------------------------------------------
@app.get("/api/events")
async def events(request: Request):
    require(request)
    return sm.q("SELECT * FROM events ORDER BY ts DESC LIMIT 200")


@app.get("/api/alerts")                         # FR42
async def alerts(request: Request):
    require(request, ["Administrator", "SecurityOperator"])
    rows = sm.q("SELECT * FROM alerts ORDER BY ts DESC LIMIT 200")
    for a in rows:
        try: a["detail"] = json.loads(a["detail"])
        except Exception: pass
    return rows


@app.post("/api/alerts/{aid}/ack")              # FR43
async def ack_alert(aid: int, request: Request):
    u = require(request, ["Administrator", "SecurityOperator"])
    sm.execute("UPDATE alerts SET status='acknowledged',ack_by=?,ack_at=? WHERE id=?",
               (u["username"], sm.now(), aid))
    sm.log("audit", f"Alert {aid} acknowledged", user=u["username"])
    return {"status": "acknowledged"}


@app.delete("/api/alerts/{aid}")
async def delete_alert(aid: int, request: Request):
    u = require(request, ["Administrator"])
    if not sm.q("SELECT 1 FROM alerts WHERE id=?", (aid,), one=True):
        raise HTTPException(404)
    sm.execute("DELETE FROM alerts WHERE id=?", (aid,))
    sm.log("audit", f"Alert {aid} deleted", user=u["username"])
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Config (FR44-47)
# ---------------------------------------------------------------------------
@app.get("/api/config")
async def get_config(request: Request):
    require(request, ["Administrator"])
    return sm.get_config()


@app.post("/api/config")
async def set_config(request: Request):
    require(request, ["Administrator"])
    data = await request.json()
    # FR45 validation: threshold_low < threshold_high
    if "threshold_high" in data and "threshold_low" in data:
        if float(data["threshold_low"]) >= float(data["threshold_high"]):
            raise HTTPException(400, "threshold_low must be < threshold_high")
    for k, v in data.items():
        sm.set_config(k, v if isinstance(v, str) else json.dumps(v)
                      if isinstance(v, (dict, list)) else v)
    sm.log("audit", "Configuration updated", context={"keys": list(data.keys())})
    return {"status": "saved", "config": sm.get_config()}


# ---------------------------------------------------------------------------
# Logs (FR30, 48, 53, 54, 57) + recommendations (FR59)
# ---------------------------------------------------------------------------
@app.get("/api/logs")                           # FR48
async def logs(request: Request, type: Optional[str] = None,
               severity: Optional[str] = None, limit: int = 200):
    require(request, ["Administrator"])
    where, args = [], []
    if type: where.append("type=?"); args.append(type)
    if severity: where.append("severity=?"); args.append(severity)
    sql = "SELECT * FROM logs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"; args.append(limit)
    rows = sm.q(sql, tuple(args))
    for r in rows:
        try: r["context"] = json.loads(r["context"])
        except Exception: pass
    return rows


@app.get("/api/logs/stream")                    # FR30 live log stream (SSE)
async def log_stream(request: Request):
    require(request)   # any authenticated role — used by the live pipeline tracker too
    import asyncio
    async def gen():
        last = sm.q("SELECT MAX(id) m FROM logs", one=True)["m"] or 0
        for _ in range(600):   # ~10 min cap
            rows = sm.q("SELECT * FROM logs WHERE id>? ORDER BY id", (last,))
            for r in rows:
                last = r["id"]
                yield f"data: {json.dumps(r)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/recommendations")                # FR59
async def recommendations(request: Request):
    require(request)
    return sm.q("SELECT * FROM recommendations ORDER BY id DESC LIMIT 50")




# ---------------------------------------------------------------------------
# Model registry — which models are actually deployed (docs/MODELS.md)
# ---------------------------------------------------------------------------
@app.get("/api/models")
async def models(request: Request):
    """Live answer to "which action model is running?".

    The written report and its appendix disagreed on this. Prose can drift from
    code; this endpoint reads the running config and the real checkpoint paths,
    so the deployed answer is always self-reported.
    """
    require(request)
    reg = itso_analytics.model_registry()
    reg["feature_contract"] = {
        "names": fusion.FEATURE_NAMES,
        "n": fusion.N_FEATURES,
        "weights": {"action": fusion.W_ACTION, "objects": fusion.W_OBJECT,
                    "sentiment": fusion.W_SENTIMENT},
    }
    reg["device"] = DEVICE
    return reg


# ---------------------------------------------------------------------------
# Per-stage reduction funnel + workflow simulation
# ---------------------------------------------------------------------------
@app.get("/api/footages/{fid}/funnel")
async def footage_funnel_route(fid: str, request: Request):
    require(request)
    rep = itso_analytics.footage_funnel(pid)
    if not rep:
        raise HTTPException(404)
    return rep


@app.get("/api/footages/{fid}/simulate")
async def footage_simulate(fid: str, request: Request):
    """Everything the Workflow Simulator needs for one real footage: the
    measured stage funnel, every segment with its fusion trace, and the model
    registry. One request, because the page walks the stages in order and
    re-fetching per step would make the walkthrough stutter."""
    require(request)
    rep = itso_analytics.footage_funnel(fid)
    if not rep:
        raise HTTPException(404)
    segs = sm.q("SELECT * FROM segments WHERE footage_id=? ORDER BY idx", (fid,))
    for seg in segs:
        for k, empty in (("objects", "[]"), ("actions", "[]"), ("metadata", "{}"),
                         ("fusion", "{}"), ("features", "{}")):
            try:
                seg[k] = json.loads(seg.get(k) or empty)
            except Exception:
                seg[k] = [] if empty == "[]" else {}
    rep["segments"] = segs
    rep["models"] = itso_analytics.model_registry()
    rep["feature_names"] = fusion.FEATURE_NAMES
    return rep


@app.get("/api/segments/{sid}/explain")
async def segment_explain(sid: str, request: Request):
    """Why this segment got this score — the auditable counterpart to an
    irreversible tiering decision."""
    require(request)
    s = itso_analytics.segment_explain(sid)
    if not s:
        raise HTTPException(404)
    return s


@app.post("/api/simulate/score")
async def simulate_score(request: Request):
    """Score a hypothetical segment without any video.

    The Workflow Simulator's what-if panel posts a partial feature dict; this
    fills the rest with zeros, runs the real trained model and the real fusion
    arithmetic, and returns the full decision with its trace. Useful for
    answering "what would it take for this to be tiered HIGH?" without
    uploading footage that demonstrates it.
    """
    require(request)
    body = await request.json()
    feats = {n: float(body.get(n, 0.0) or 0.0) for n in fusion.FEATURE_NAMES}
    cfg = sm.get_config()
    try:
        ctx = json.loads(cfg.get("context_rules") or "{}")
    except Exception:
        ctx = {}
    score, backend = sentiment_model.predict(feats)
    rule = sentiment_model.rule_score(feats)

    # Rebuild the minimal model-output shapes fuse() expects from the features
    # themselves, so the what-if runs through the same code path as a real
    # segment rather than a parallel reimplementation.
    action = {"label": body.get("action_label", "synthetic"),
              "confidence": feats["action_top_conf"],
              "probs": body.get("probs") or {}, "top": []}
    track1 = ([{"label": body.get("t1_label", "Knife"),
                "confidence": feats["t1_weapon_conf"] or feats["t1_max_threat"],
                "frame_hits": int(round(feats["t1_persistence"] * 15)),
                "threat_weight": 1.0,
                "critical": True}] if feats["t1_max_threat"] > 0 else [])
    track2 = ([{"label": "person", "confidence": feats["ctx_person_conf"],
                "frame_hits": 15,
                "instances": int(round(feats["ctx_people"] * 8))}]
              if feats["ctx_people"] > 0 else [])

    res = fusion.fuse(action, track1, track2, score,
                      motion_ratio=feats["motion_ratio"],
                      is_night=bool(feats["is_night"]), frames_sampled=15,
                      ctx_rules=ctx,
                      th_high=float(cfg.get("threshold_high", 0.7)),
                      th_low=float(cfg.get("threshold_low", 0.4)))
    # fuse() rebuilds features from the synthetic detections, which cannot
    # round-trip every dimension; report the caller's vector as authoritative.
    res["features"] = feats
    res["sentiment_backend"] = backend
    res["rule_score"] = rule
    res["delta_vs_rule"] = round(score - rule, 4)
    return res


# ---------------------------------------------------------------------------
# Savings projection (FR52 extension)
# ---------------------------------------------------------------------------
@app.get("/api/savings/projection")
async def savings_projection(request: Request, months: int = 36,
                             growth: float = 6.0, cost_per_gb: float = 0.023,
                             retention_months: Optional[int] = None,
                             profile: Optional[str] = None):
    require(request)
    months = max(1, min(120, months))
    return itso_analytics.savings_projection(months, growth, cost_per_gb,
                                             retention_months, profile)


# ---------------------------------------------------------------------------
# Tier-3 deletion safeguard — review queue
# ---------------------------------------------------------------------------
@app.get("/api/review-queue")
async def review_queue(request: Request, status: str = "pending", limit: int = 200):
    """LOW-tier segments whose video is held pending irreversible deletion."""
    require(request, ["Administrator", "SecurityOperator"])
    rows = sm.q("SELECT q.*, s.idx, s.ssig AS seg_ssig, s.thumb, s.tier AS seg_tier, "
                "f.name AS footage_name, p.name AS project_name "
                "FROM purge_queue q "
                "LEFT JOIN segments s ON s.id = q.segment_id "
                "LEFT JOIN footages f ON f.id = q.footage_id "
                "LEFT JOIN projects p ON p.id = f.project_id "
                "WHERE q.status=? ORDER BY q.purge_after LIMIT ?", (status, limit))
    for r in rows:
        r["exists"] = bool(r["path"] and os.path.exists(r["path"]))
    return {"status": status, "items": rows, "summary": sm.purge_summary(),
            "grace_hours": sm.get_config().get("tier3_grace_hours", "24")}


@app.post("/api/review-queue/{qid}/restore")
async def restore_held(qid: int, request: Request, tier: str = Form("MEDIUM")):
    """Rescue a held segment: re-tier it and keep the footage.

    This is the reversal path the safeguard exists for — an operator who
    disagrees with a LOW score can promote the segment while the video still
    exists, instead of discovering the loss afterwards.
    """
    u = require(request, ["Administrator", "SecurityOperator"])
    if tier not in ("HIGH", "MEDIUM"):
        raise HTTPException(400, "Restore to HIGH or MEDIUM")
    row = sm.q("SELECT * FROM purge_queue WHERE id=?", (qid,), one=True)
    if not row:
        raise HTTPException(404)
    if row["status"] != "pending":
        raise HTTPException(400, f"Already {row['status']}")
    if not row["path"] or not os.path.exists(row["path"]):
        raise HTTPException(410, "Held file no longer on disk")

    from models import tier_segmenters
    tier_dir = os.path.join(sm.ARCHIVE_DIR, "tiers")
    stored_path, stored_bytes = tier_segmenters.execute_tier(
        tier, row["path"], tier_dir, row["segment_id"] + "_restored")
    sm.execute("UPDATE segments SET tier=?, tier_manual=1, stored_path=?, "
               "stored_bytes=?, purge_state='restored' WHERE id=?",
               (tier, stored_path, stored_bytes, row["segment_id"]))
    sm.execute("UPDATE purge_queue SET status='restored', reviewed_by=?, reviewed_at=? "
               "WHERE id=?", (u["username"], sm.now(), qid))
    try:
        os.remove(row["path"])
    except OSError:
        pass
    sm.log("audit", f"Held segment {row['segment_id']} restored to {tier}",
           user=u["username"], context={"queue_id": qid, "tier": tier})
    return {"status": "restored", "tier": tier, "stored_bytes": stored_bytes}


@app.post("/api/review-queue/{qid}/purge")
async def purge_held(qid: int, request: Request):
    """Confirm deletion now, before the grace period expires."""
    u = require(request, ["Administrator", "SecurityOperator"])
    row = sm.q("SELECT * FROM purge_queue WHERE id=?", (qid,), one=True)
    if not row:
        raise HTTPException(404)
    if row["status"] != "pending":
        raise HTTPException(400, f"Already {row['status']}")
    if row["path"] and os.path.exists(row["path"]):
        try:
            os.remove(row["path"])
        except OSError:
            pass
    sm.execute("UPDATE purge_queue SET status='purged', reviewed_by=?, reviewed_at=? "
               "WHERE id=?", (u["username"], sm.now(), qid))
    sm.execute("UPDATE segments SET purge_state='purged' WHERE id=?",
               (row["segment_id"],))
    sm.log("audit", f"Held segment {row['segment_id']} purged by operator",
           user=u["username"], context={"queue_id": qid})
    return {"status": "purged"}


@app.post("/api/review-queue/sweep")
async def sweep_queue(request: Request):
    """Run the expiry sweep immediately (it also runs on a background timer)."""
    u = require(request, ["Administrator"])
    n, freed = itso_engine.run_purge_sweep()
    sm.log("audit", f"Manual purge sweep by {u['username']}: {n} deleted",
           user=u["username"])
    return {"purged": n, "bytes_reclaimed": freed}


# ---------------------------------------------------------------------------
# Threshold calibration evidence
# ---------------------------------------------------------------------------
@app.get("/api/calibration")
async def calibration(request: Request):
    """Results of the tier-boundary generalisation sweep. Returns 404 with a
    hint rather than a fabricated result when the experiment has not been run."""
    require(request)
    path = os.path.join(BASE, "weights", "threshold_calibration.json")
    if not os.path.exists(path):
        raise HTTPException(404, "No calibration run yet — "
                                 "run: python -m training.calibrate_thresholds")
    with open(path) as f:
        data = json.load(f)
    # The full grid is large and the UI only plots summary points.
    data["grid"] = sorted(data["grid"], key=lambda r: r["mean_storage_index"])[:400]
    data["current_config"] = {
        "threshold_low": sm.get_config().get("threshold_low"),
        "threshold_high": sm.get_config().get("threshold_high"),
    }
    return data


@app.post("/api/calibration/apply")
async def apply_calibration(request: Request):
    """Adopt the recommended boundaries. Explicit, admin-only, and audited —
    the sweep never changes live thresholds on its own."""
    u = require(request, ["Administrator"])
    path = os.path.join(BASE, "weights", "threshold_calibration.json")
    if not os.path.exists(path):
        raise HTTPException(404, "No calibration run yet")
    with open(path) as f:
        rec = json.load(f)["recommended"]
    sm.set_config("threshold_low", rec["threshold_low"])
    sm.set_config("threshold_high", rec["threshold_high"])
    sm.log("audit", f"Calibrated thresholds applied: "
                    f"{rec['threshold_low']}/{rec['threshold_high']}",
           user=u["username"], context=rec)
    return {"status": "applied", "threshold_low": rec["threshold_low"],
            "threshold_high": rec["threshold_high"]}


# ---------------------------------------------------------------------------
# Validation evidence (deployment targets)
# ---------------------------------------------------------------------------
@app.get("/api/validation")
async def validation(request: Request):
    """Measured results of the availability / concurrency / cross-platform
    experiments, if `benchmarks/` has been run."""
    require(request)
    path = os.path.join(BASE, "benchmarks", "results.json")
    if not os.path.exists(path):
        raise HTTPException(404, "No validation run yet — "
                                 "run: python -m benchmarks.run_all")
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Deep analytics — detections, decision logic, alerts, timeline
# ---------------------------------------------------------------------------
@app.get("/api/analytics/detections")
async def analytics_detections(request: Request, project: Optional[str] = None,
                               profile: Optional[str] = None):
    """What the models found, split by track. `profile=fusion` scopes to the
    deployed pipeline; without it, legacy segments are mixed in and the action
    labels come from two different class vocabularies."""
    require(request)
    return itso_analytics.detection_analytics(project, profile)


@app.get("/api/analytics/decisions")
async def analytics_decisions(request: Request, project: Optional[str] = None,
                              profile: Optional[str] = None):
    """How model output became a tiering decision: modifier fire rates,
    per-channel contribution, Ssig distribution, boundary sensitivity."""
    require(request)
    return itso_analytics.decision_analytics(project, profile)


@app.get("/api/analytics/alerts")
async def analytics_alerts(request: Request, project: Optional[str] = None):
    require(request, ["Administrator", "SecurityOperator"])
    return itso_analytics.alert_analytics(project)


@app.get("/api/analytics/timeline")
async def analytics_timeline(request: Request, project: Optional[str] = None,
                             limit: int = 400, profile: Optional[str] = None):
    require(request)
    return itso_analytics.timeline_analytics(project, max(1, min(2000, limit)), profile)


@app.get("/api/segments/{sid}/overlay")
async def segment_overlay(sid: str, request: Request):
    """Per-frame detection boxes for one segment, for the playback overlay.

    Boxes are normalised to 0..1 of the frame, so the client can scale them to
    whatever size the video element happens to be rendered at. Returns an empty
    timeline (not a 404) for segments processed before overlay capture existed,
    so playback degrades to plain video rather than erroring.
    """
    require(request)
    row = sm.q("SELECT metadata, start_time, end_time, tier, ssig FROM segments "
               "WHERE id=?", (sid,), one=True)
    if not row:
        raise HTTPException(404)
    try:
        meta = json.loads(row.get("metadata") or "{}")
    except Exception:
        meta = {}
    overlay = meta.get("overlay") or []
    return {
        "segment_id": sid,
        "start_time": row.get("start_time"),
        "end_time": row.get("end_time"),
        "duration": (row.get("end_time") or 0) - (row.get("start_time") or 0),
        "tier": row.get("tier"), "ssig": row.get("ssig"),
        "frames": overlay,
        "available": bool(overlay),
        "note": None if overlay else
                "This segment was processed before per-frame overlay capture; "
                "reprocess the project to get detection boxes.",
    }


# ---------------------------------------------------------------------------
# Operating modes — five documented presets over the tuning knobs
# ---------------------------------------------------------------------------
@app.get("/api/modes")
async def list_modes(request: Request):
    """All five modes plus which one the live config matches (or `custom`),
    with the specific keys that differ from each."""
    require(request)
    return itso_modes.list_modes()


@app.get("/api/modes/{mode_id}/preview")
async def preview_mode(mode_id: str, request: Request):
    """What applying this mode would change — shown before confirming, because
    a mode rewrites tiering behaviour for every future upload."""
    require(request)
    try:
        return itso_modes.preview(mode_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/modes/{mode_id}/rationale")
async def mode_rationale(mode_id: str, request: Request):
    """Why this mode behaves as it does, replayed over the system's own scored
    segments rather than asserted."""
    require(request)
    try:
        return itso_modes.rationale(mode_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/modes/{mode_id}/apply")
async def apply_mode(mode_id: str, request: Request):
    u = require(request, ["Administrator"])
    try:
        return itso_modes.apply_mode(mode_id, u["username"])
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------------------------------
# Reprocessing — re-run existing footage through the current pipeline
# ---------------------------------------------------------------------------
@app.post("/api/footages/{fid}/reprocess")
async def reprocess_footage(fid: str, request: Request):
    """Re-run one footage. Derived artefacts are deleted; the upload is kept.

    Needed because the pipeline changed underneath the data: anything analysed
    before the lossless-segmentation and motion-filter fixes has inflated
    segment sizes, no stage telemetry, no fusion trace and no detection
    overlay.
    """
    u = require(request, ["Administrator", "SecurityOperator"])
    f = sm.q("SELECT * FROM footages WHERE id=?", (fid,), one=True)
    if not f:
        raise HTTPException(404)
    if f["status"] in ("processing", "queued"):
        raise HTTPException(400, "Already in the pipeline")
    if not f["original_path"] or not os.path.exists(f["original_path"]):
        raise HTTPException(410, "Source file is no longer on disk — cannot reprocess")

    cleaned = itso_engine.reset_footage(fid)
    sm.log("audit", f"Reprocess requested for {fid}", user=u["username"],
           context={"footage": fid, **cleaned})
    position = jobs.submit(fid)
    return {"status": "queued", "queue_position": position, "cleaned": cleaned}


@app.post("/api/footages/reprocess-all")
async def reprocess_all(request: Request, only_legacy: bool = False,
                        project: Optional[str] = None):
    """Queue every reprocessable footage, optionally scoped to one project."""
    u = require(request, ["Administrator"])
    where = "status NOT IN ('processing','queued')"
    args = []
    if only_legacy:
        where += " AND (pipeline_profile IS NULL OR pipeline_profile!='fusion')"
    if project:
        where += " AND project_id=?"
        args.append(project)
    rows = sm.q(f"SELECT id, name, original_path FROM footages WHERE {where}", tuple(args))

    queued, skipped = [], []
    for r in rows:
        if not r["original_path"] or not os.path.exists(r["original_path"]):
            skipped.append({"id": r["id"], "name": r["name"], "reason": "source missing"})
            continue
        itso_engine.reset_footage(r["id"])
        jobs.submit(r["id"])
        queued.append({"id": r["id"], "name": r["name"]})
    sm.log("audit", f"Bulk reprocess: {len(queued)} queued, {len(skipped)} skipped",
           user=u["username"])
    return {"queued": queued, "skipped": skipped, "workers": jobs.max_workers()}


@app.get("/api/overview/footages")
async def footages_overview(request: Request, project: Optional[str] = None):
    """One row per footage with everything a management view needs: status,
    tier mix, per-stage reduction summary and whether it can be reprocessed.

    Assembled server-side because the alternative is the UI firing a funnel
    request per footage, which is N+1 over the network for a list view.
    Used by the Workflow and Analytics selectors; the Projects page uses the
    project-level rollup instead.
    """
    require(request)
    where, args = [], []
    if project:
        where.append("project_id=?"); args.append(project)
    sql = "SELECT * FROM footages"
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = sm.q(sql + " ORDER BY created_at DESC", tuple(args))

    stage_rows = sm.q("SELECT footage_id, stage, name, unit, items_in, items_out, "
                      "bytes_in, bytes_out, frames_in, frames_out, duration_ms "
                      "FROM stage_stats")
    tier_rows = sm.q("SELECT footage_id, tier, COUNT(*) n, SUM(stored_bytes) sb "
                     "FROM segments WHERE tier!='' GROUP BY footage_id, tier")
    seg_rows = sm.q("SELECT footage_id, COUNT(*) n, SUM(motion) m FROM segments "
                    "GROUP BY footage_id")
    held_rows = sm.q("SELECT footage_id, COUNT(*) n FROM purge_queue "
                     "WHERE status='pending' GROUP BY footage_id")
    proj_names = {r["id"]: r["name"] for r in sm.q("SELECT id, name FROM projects")}

    by_stage, by_tier, by_seg, by_held = {}, {}, {}, {}
    for r in stage_rows:
        by_stage.setdefault(r["footage_id"], []).append(r)
    for r in tier_rows:
        by_tier.setdefault(r["footage_id"], {})[r["tier"]] = r["n"]
    for r in seg_rows:
        by_seg[r["footage_id"]] = {"total": r["n"], "motion": r["m"] or 0}
    for r in held_rows:
        by_held[r["footage_id"]] = r["n"]

    out = []
    for f in rows:
        uploaded = f.get("original_bytes") or 0
        stored = f.get("stored_bytes") or 0
        stages = sorted(by_stage.get(f["id"], []), key=lambda x: x["stage"])
        segs = by_seg.get(f["id"], {"total": 0, "motion": 0})
        out.append({
            "id": f["id"], "project_id": f.get("project_id"),
            "project_name": proj_names.get(f.get("project_id"), ""),
            "name": f["name"] or f["filename"], "filename": f["filename"],
            "status": f["status"], "created_at": f["created_at"],
            "duration": f.get("duration") or 0,
            "width": f.get("width"), "height": f.get("height"), "fps": f.get("fps"),
            "profile": f.get("pipeline_profile") or "legacy",
            "uploaded_bytes": uploaded,
            "segment_bytes": f.get("segment_bytes") or 0,
            "stored_bytes": stored,
            "savings_percent": round(100 * (1 - stored / uploaded), 2) if uploaded else 0.0,
            "processing_ms": f.get("processing_ms") or 0,
            "frames_total": f.get("frames_total") or 0,
            "frames_analyzed": f.get("frames_analyzed") or 0,
            "segments": segs["total"], "motion_segments": segs["motion"],
            "tiers": by_tier.get(f["id"], {}),
            "held": by_held.get(f["id"], 0),
            "has_telemetry": bool(stages),
            "stages": [{
                "stage": x["stage"], "name": x["name"], "unit": x["unit"],
                "in": x["bytes_in"] if x["unit"] == "bytes"
                      else x["frames_in"] if x["unit"] == "frames" else x["items_in"],
                "out": x["bytes_out"] if x["unit"] == "bytes"
                       else x["frames_out"] if x["unit"] == "frames" else x["items_out"],
                "duration_ms": x["duration_ms"],
            } for x in stages],
            "reprocessable": bool(f["original_path"] and os.path.exists(f["original_path"])),
        })

    totals = {
        "footages": len(out),
        "uploaded_bytes": sum(o["uploaded_bytes"] for o in out),
        "stored_bytes": sum(o["stored_bytes"] for o in out),
        "segments": sum(o["segments"] for o in out),
        "fusion": sum(1 for o in out if o["profile"] == "fusion"),
        "legacy": sum(1 for o in out if o["profile"] != "fusion"),
        "needs_reprocess": sum(1 for o in out
                               if o["profile"] != "fusion" or not o["has_telemetry"]),
    }
    totals["savings_percent"] = (
        round(100 * (1 - totals["stored_bytes"] / totals["uploaded_bytes"]), 2)
        if totals["uploaded_bytes"] else 0.0)
    return {"footages": out, "totals": totals}


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


@app.get("/api/health")
async def health():
    return {"ok": True, "device": DEVICE}

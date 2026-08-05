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

sm.init_db()
DEVICE = itso_engine.select_device()

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
    token = request.headers.get("x-session") or request.cookies.get("session")
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


@app.post("/api/projects")
async def upload(request: Request, file: UploadFile = File(...)):
    u = require(request)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"Unsupported type {ext}")
    pid, jid = uuid.uuid4().hex, uuid.uuid4().hex
    dest = os.path.join(sm.ARCHIVE_DIR, "uploads", f"{pid}{ext}")
    size = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_BYTES:
                f.close(); os.remove(dest)
                raise HTTPException(400, "File exceeds 500MB")
            f.write(chunk)
    default_name = os.path.splitext(file.filename)[0]
    sm.execute("INSERT INTO projects(id,job_id,owner_id,name,filename,original_path,status,"
               "created_at,original_bytes) VALUES(?,?,?,?,?,?,?,?,?)",
               (pid, jid, u["id"], default_name, file.filename, dest, "uploaded", sm.now(), size))
    sm.log("system", f"Upload: {file.filename}", user=u["username"],
           context={"project": pid, "job": jid})
    threading.Thread(target=itso_engine.process_project, args=(pid,), daemon=True).start()
    return {"project_id": pid, "job_id": jid, "status": "processing"}


@app.get("/api/projects")
async def list_projects(request: Request):
    require(request)
    rows = sm.q("SELECT p.*, (SELECT COUNT(*) FROM segments s WHERE s.project_id=p.id) segs "
                "FROM projects p ORDER BY created_at DESC")
    return rows


@app.get("/api/projects/{pid}")
async def project_detail(pid: str, request: Request):
    require(request)
    p = sm.q("SELECT * FROM projects WHERE id=?", (pid,), one=True)
    if not p:
        raise HTTPException(404)
    segs = sm.q("SELECT * FROM segments WHERE project_id=? ORDER BY idx", (pid,))
    for s in segs:
        s["objects"] = json.loads(s["objects"]); s["actions"] = json.loads(s["actions"])
    return {"project": p, "segments": segs}


@app.patch("/api/projects/{pid}")              # FR50 project management (rename)
async def rename_project(pid: str, request: Request, name: str = Form(...)):
    require(request, ["Administrator", "SecurityOperator"])
    name = name.strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    if not sm.q("SELECT 1 FROM projects WHERE id=?", (pid,), one=True):
        raise HTTPException(404)
    sm.execute("UPDATE projects SET name=? WHERE id=?", (name, pid))
    sm.log("audit", f"Project renamed: {pid}", context={"name": name})
    return {"status": "updated", "name": name}


# FR27/50/51 deletion with cascade
@app.delete("/api/projects/{pid}")
async def delete_project(pid: str, request: Request):
    require(request, ["Administrator", "SecurityOperator"])
    segs = sm.q("SELECT stored_path,thumb FROM segments WHERE project_id=?", (pid,))
    for s in segs:
        for p in (s["stored_path"], s["thumb"]):
            if p and os.path.exists(p):
                try: os.remove(p)
                except OSError: pass
    proj = sm.q("SELECT original_path FROM projects WHERE id=?", (pid,), one=True)
    if proj and proj["original_path"] and os.path.exists(proj["original_path"]):
        try: os.remove(proj["original_path"])
        except OSError: pass
    sm.execute("DELETE FROM projects WHERE id=?", (pid,))   # cascades segments/events
    sm.log("audit", f"Project deleted: {pid}")
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
    proj = sm.q("SELECT status, COUNT(*) n FROM projects GROUP BY status")
    tiers = sm.q("SELECT tier, COUNT(*) n FROM segments WHERE tier!='' GROUP BY tier")
    threats = sm.q("SELECT threat_level, COUNT(*) n FROM segments "
                   "WHERE motion=1 GROUP BY threat_level")
    seg_total = sm.q("SELECT COUNT(*) n FROM segments", one=True)["n"]
    motion = sm.q("SELECT COUNT(*) n FROM segments WHERE motion=1", one=True)["n"]
    events = sm.q("SELECT COUNT(*) n FROM events", one=True)["n"]
    alerts = sm.q("SELECT COUNT(*) n FROM alerts WHERE status='new'", one=True)["n"]
    return {"storage": rep, "projects": {r["status"]: r["n"] for r in proj},
            "tiers": {r["tier"]: r["n"] for r in tiers},
            "threats": {r["threat_level"]: r["n"] for r in threats},
            "segments_total": seg_total, "segments_motion": motion,
            "events": events, "open_alerts": alerts, "device": DEVICE}


@app.get("/api/status")                        # FR49 (2s polling target)
async def proc_status(request: Request):
    require(request)
    active = sm.q("SELECT COUNT(*) n FROM projects WHERE status='processing'", one=True)["n"]
    done = sm.q("SELECT COUNT(*) n FROM projects WHERE status='done'", one=True)["n"]
    segs = sm.q("SELECT COUNT(*) n FROM segments", one=True)["n"]
    return {"active_projects": active, "completed_projects": done,
            "segments_processed": segs, "device": DEVICE, "ts": sm.now()}


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
# Static UI
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


@app.get("/api/health")
async def health():
    return {"ok": True, "device": DEVICE}

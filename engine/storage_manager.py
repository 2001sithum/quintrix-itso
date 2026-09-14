"""
storage_manager.py — SQLite persistence + file archiving.
Central data layer for every functional requirement that touches the database.
WAL tuning per the system design doc.
"""
import os, sqlite3, json, time, datetime as dt, threading, shutil

DB_PATH = os.environ.get("QUINTRIX_DB", "quintrix_itso.db")
ARCHIVE_DIR = os.environ.get("QUINTRIX_ARCHIVE", "archive")
_lock = threading.RLock()

for sub in ("uploads", "segments", "thumbs", "keyframes", "tiers"):
    os.makedirs(os.path.join(ARCHIVE_DIR, sub), exist_ok=True)


def connect():
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA temp_store = MEMORY")
    con.execute("PRAGMA cache_size = -64000")
    con.execute("PRAGMA foreign_keys = ON")
    return con


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  full_name TEXT,
  password_hash TEXT NOT NULL,
  role TEXT DEFAULT 'User',            -- Administrator | SecurityOperator | User
  status TEXT DEFAULT 'pending',       -- pending | active | rejected
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER,
  created_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
-- A project is a *container* — a site, a camera, an investigation. It holds
-- many footages. It owns no media itself.
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  owner_id INTEGER,
  created_at TEXT
);
-- A footage is one uploaded recording and everything derived from it. This is
-- what earlier versions called a "project"; the rename happened when projects
-- became containers (see the migration in init_db).
CREATE TABLE IF NOT EXISTS footages (
  id TEXT PRIMARY KEY,
  project_id TEXT,
  job_id TEXT,
  owner_id INTEGER,
  name TEXT,
  filename TEXT,
  original_path TEXT,
  status TEXT DEFAULT 'uploaded',
  created_at TEXT,
  duration REAL DEFAULT 0,
  fps REAL DEFAULT 0,
  width INTEGER DEFAULT 0,
  height INTEGER DEFAULT 0,
  codec TEXT DEFAULT '',
  original_bytes INTEGER DEFAULT 0,
  segment_bytes INTEGER DEFAULT 0,
  stored_bytes INTEGER DEFAULT 0,
  pipeline_profile TEXT DEFAULT '',
  processing_ms REAL DEFAULT 0,
  frames_total INTEGER DEFAULT 0,
  frames_analyzed INTEGER DEFAULT 0,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS segments (
  id TEXT PRIMARY KEY,
  footage_id TEXT,
  idx INTEGER,
  start_time REAL,
  end_time REAL,
  ts TEXT,
  motion INTEGER DEFAULT 0,
  thumb TEXT DEFAULT '',
  objects TEXT DEFAULT '[]',
  actions TEXT DEFAULT '[]',
  sentiment_score REAL DEFAULT 0,
  sentiment_label TEXT DEFAULT 'neutral',
  threat_level TEXT DEFAULT 'low',
  hazard_level TEXT DEFAULT 'none',
  ssig REAL DEFAULT 0,
  tier TEXT DEFAULT '',
  tier_manual INTEGER DEFAULT 0,
  stored_path TEXT DEFAULT '',
  original_bytes INTEGER DEFAULT 0,
  stored_bytes INTEGER DEFAULT 0,
  metadata TEXT DEFAULT '{}',
  FOREIGN KEY(footage_id) REFERENCES footages(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  segment_id TEXT,
  footage_id TEXT,
  ts TEXT,
  objects TEXT,
  actions TEXT,
  tier TEXT,
  ssig REAL,
  FOREIGN KEY(segment_id) REFERENCES segments(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  segment_id TEXT,
  footage_id TEXT,
  ts TEXT,
  severity TEXT,
  ssig REAL,
  detail TEXT,
  status TEXT DEFAULT 'new',           -- new | acknowledged
  ack_by TEXT DEFAULT '',
  ack_at TEXT DEFAULT '',
  FOREIGN KEY(segment_id) REFERENCES segments(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS config (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  type TEXT,                           -- system | api | processing | audit | error
  severity TEXT DEFAULT 'info',
  user TEXT DEFAULT '',
  message TEXT,
  context TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS recommendations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  kind TEXT,
  message TEXT
);
-- Per-stage reduction telemetry. One row per pipeline stage per project: what
-- went in, what came out, and how long it took. This is what the Workflow
-- Simulator's reduction funnel reads, and it is recorded during the real run
-- rather than reconstructed afterwards, so the funnel reflects what actually
-- happened to that video.
CREATE TABLE IF NOT EXISTS stage_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  footage_id TEXT,
  stage INTEGER,
  name TEXT,
  unit TEXT,                           -- primary quantity reduced: segments | frames | bytes
  items_in REAL DEFAULT 0,
  items_out REAL DEFAULT 0,
  bytes_in INTEGER DEFAULT 0,
  bytes_out INTEGER DEFAULT 0,
  frames_in INTEGER DEFAULT 0,
  frames_out INTEGER DEFAULT 0,
  duration_ms REAL DEFAULT 0,
  detail TEXT DEFAULT '{}',
  FOREIGN KEY(footage_id) REFERENCES footages(id) ON DELETE CASCADE
);
-- Tier-3 deletion safeguard. LOW-tier video is not destroyed at scoring time;
-- it is parked here with a purge deadline so a wrong significance score can be
-- caught and reversed by an operator while the footage still exists.
CREATE TABLE IF NOT EXISTS purge_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  segment_id TEXT UNIQUE,
  footage_id TEXT,
  tier TEXT,
  ssig REAL DEFAULT 0,
  path TEXT,
  bytes INTEGER DEFAULT 0,
  queued_at TEXT,
  purge_after TEXT,
  status TEXT DEFAULT 'pending',       -- pending | purged | restored | held
  reason TEXT DEFAULT '',
  reviewed_by TEXT DEFAULT '',
  reviewed_at TEXT DEFAULT '',
  FOREIGN KEY(segment_id) REFERENCES segments(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_purge_status ON purge_queue(status, purge_after);
CREATE INDEX IF NOT EXISTS idx_stage_footage ON stage_stats(footage_id, stage);
CREATE INDEX IF NOT EXISTS idx_segments_footage ON segments(footage_id, idx);
CREATE INDEX IF NOT EXISTS idx_footages_project ON footages(project_id);
"""

DEFAULT_CONFIG = {
    "threshold_high": "0.7",
    "threshold_low": "0.4",
    "alert_threshold": "0.8",
    "motion_sensitivity": "0.001",
    "object_conf_threshold": "0.35",
    "action_min_frames": "16",
    "segment_seconds": "15",
    # Which pipeline runs. "fusion" is the deployed CCTV pipeline: R3D-18
    # action recognition + Track 1 suspicious objects + Track 2 context, joined
    # by engine/fusion.py and scored by the trained sentiment model. "legacy"
    # is the original X3D-S + COCO + MobileNetV3 path, kept runnable so the two
    # can be compared on the same footage. See docs/MODELS.md.
    "pipeline_profile": "fusion",
    # Action backend for the LEGACY profile only. The fusion profile always
    # uses R3D-18, because its scoring depends on UCF-Crime class severity that
    # Kinetics-400 labels cannot express.
    "action_model_backend": "x3d",
    "track1_conf_threshold": "0.35",
    "track2_conf_threshold": "0.35",
    # Detector frame sampling, in seconds. 1.0 means ~1 frame per second, which
    # is the reference pipeline's rate — running YOLO on every frame costs ~25x
    # more for detections that do not change that fast.
    "detection_stride_seconds": "1.0",
    # Tier-3 safeguard: hours a LOW-tier segment's video is retained in the
    # review queue before irreversible deletion. 0 restores the old
    # delete-immediately behaviour.
    "tier3_grace_hours": "24",
    # Pipeline worker pool size. The pipeline is CPU-bound and shells out to
    # ffmpeg, so running more jobs than the machine can carry reduces
    # throughput rather than raising it. Empty means "derive from core count".
    "max_concurrent_jobs": "",
    "context_rules": json.dumps({
        "night_boost": 0.1, "weapon_boost": 0.25, "crowd_boost": 0.15,
        "fire_boost": 0.20, "unattended_boost": 0.12
    }),
}


def _migrate_to_footages(con):
    """Convert the pre-hierarchy database in place.

    Earlier versions had one table, `projects`, where each row was a single
    uploaded recording. Projects are now *containers* that hold many footages,
    so the old table becomes `footages` and a new `projects` table sits above
    it. Child tables (segments, events, alerts, stage_stats, purge_queue) hang
    off a footage, so their `project_id` column is renamed `footage_id`.

    Detection is structural, not a version flag: the old shape is a `projects`
    table that has a `filename` column. Running against an already-migrated or
    brand-new database is a no-op.

    Existing recordings are grouped into projects by name — uploading the same
    file four times produced four rows with identical names, and those clearly
    belong together. Anything unique gets its own project, which is the safe
    default: over-grouping would merge unrelated footage.
    """
    cols = [r[1] for r in con.execute("PRAGMA table_info(projects)").fetchall()]
    if not cols or "filename" not in cols:
        return 0          # already migrated, or a fresh database

    print("[migration] projects -> projects/footages hierarchy")
    con.execute("PRAGMA foreign_keys = OFF")

    # 1. old projects table becomes footages (SQLite rewrites FK references)
    con.execute("ALTER TABLE projects RENAME TO footages")
    fcols = [r[1] for r in con.execute("PRAGMA table_info(footages)").fetchall()]
    if "project_id" not in fcols:
        con.execute("ALTER TABLE footages ADD COLUMN project_id TEXT")

    # 2. child tables now reference a footage
    for table in ("segments", "events", "alerts", "stage_stats", "purge_queue"):
        tcols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
        if "project_id" in tcols and "footage_id" not in tcols:
            con.execute(f"ALTER TABLE {table} RENAME COLUMN project_id TO footage_id")

    # 3. the new container table
    con.execute("""CREATE TABLE projects (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
        owner_id INTEGER, created_at TEXT)""")

    # 4. group footages by name — identical names are re-uploads of one source
    import uuid as _uuid
    groups = {}
    for r in con.execute("SELECT id, name, filename, owner_id, created_at "
                         "FROM footages ORDER BY created_at").fetchall():
        key = (r["name"] or r["filename"] or r["id"]).strip()
        groups.setdefault(key, []).append(r)

    for key, rows in groups.items():
        pid = _uuid.uuid4().hex
        con.execute("INSERT INTO projects(id,name,description,owner_id,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (pid, key, f"Migrated — {len(rows)} footage(s)",
                     rows[0]["owner_id"], rows[0]["created_at"]))
        for r in rows:
            con.execute("UPDATE footages SET project_id=? WHERE id=?", (pid, r["id"]))

    con.execute("PRAGMA foreign_keys = ON")
    print(f"[migration] {sum(len(v) for v in groups.values())} footage(s) "
          f"grouped into {len(groups)} project(s)")
    return len(groups)


def init_db():
    with _lock, connect() as con:
        # Must run before executescript: the new SCHEMA would otherwise create
        # an empty `footages` table alongside the old `projects` one and the
        # rename would then collide.
        _migrate_to_footages(con)
        con.executescript(SCHEMA)
        for k, v in DEFAULT_CONFIG.items():
            con.execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", (k, v))
        # migrate pre-existing DBs whose alerts table predates the cascading FK
        # (older schema had no FK at all, so deleted projects left orphaned alerts)
        fk_cols = [r[3] for r in con.execute("PRAGMA foreign_key_list(alerts)").fetchall()]
        if "segment_id" not in fk_cols:
            con.execute("PRAGMA foreign_keys = OFF")
            con.executescript("""
                CREATE TABLE alerts_new (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  segment_id TEXT,
                  footage_id TEXT,
                  ts TEXT,
                  severity TEXT,
                  ssig REAL,
                  detail TEXT,
                  status TEXT DEFAULT 'new',
                  ack_by TEXT DEFAULT '',
                  ack_at TEXT DEFAULT '',
                  FOREIGN KEY(segment_id) REFERENCES segments(id) ON DELETE CASCADE
                );
                INSERT INTO alerts_new
                  SELECT a.* FROM alerts a JOIN segments s ON s.id = a.segment_id;
                DROP TABLE alerts;
                ALTER TABLE alerts_new RENAME TO alerts;
            """)
            con.execute("PRAGMA foreign_keys = ON")
        # migrate pre-existing DBs that predate the editable footage name (CRUD)
        cols = [r[1] for r in con.execute("PRAGMA table_info(footages)").fetchall()]
        if "name" not in cols:
            con.execute("ALTER TABLE footages ADD COLUMN name TEXT")
            con.execute("UPDATE footages SET name = filename WHERE name IS NULL")
        # migrate pre-existing DBs that predate the fusion pipeline. Additive
        # columns only — rows analysed by the legacy pipeline stay readable and
        # simply report an empty fusion trace.
        seg_cols = [r[1] for r in con.execute("PRAGMA table_info(segments)").fetchall()]
        for col, decl in (("fusion", "TEXT DEFAULT '{}'"),
                          ("features", "TEXT DEFAULT '{}'"),
                          ("action_backend", "TEXT DEFAULT ''"),
                          ("sentiment_backend", "TEXT DEFAULT ''"),
                          ("pipeline_profile", "TEXT DEFAULT 'legacy'"),
                          ("frames_total", "INTEGER DEFAULT 0"),
                          ("frames_analyzed", "INTEGER DEFAULT 0"),
                          ("purge_state", "TEXT DEFAULT ''")):
            if col not in seg_cols:
                con.execute(f"ALTER TABLE segments ADD COLUMN {col} {decl}")
        proj_cols = [r[1] for r in con.execute("PRAGMA table_info(footages)").fetchall()]
        for col, decl in (("pipeline_profile", "TEXT DEFAULT ''"),
                          ("processing_ms", "REAL DEFAULT 0"),
                          ("frames_total", "INTEGER DEFAULT 0"),
                          ("frames_analyzed", "INTEGER DEFAULT 0"),
                          # sum of the intermediate segment files, kept apart
                          # from original_bytes so savings are always measured
                          # against the file the operator actually uploaded
                          ("segment_bytes", "INTEGER DEFAULT 0")):
            if col not in proj_cols:
                con.execute(f"ALTER TABLE footages ADD COLUMN {col} {decl}")
                if col == "segment_bytes":
                    # One-time repair. Before the segment_bytes column existed,
                    # the pipeline overwrote projects.original_bytes with the
                    # sum of the intermediate segment files, so every historical
                    # savings figure was measured against a baseline the
                    # pipeline itself produced. The uploaded files are still on
                    # disk, so the true baseline is recoverable: move the old
                    # value to segment_bytes and restore original_bytes from
                    # the file. Rows whose upload is gone keep what they have.
                    con.execute("UPDATE footages SET segment_bytes = original_bytes")
                    repaired = 0
                    for r in con.execute("SELECT id, original_path FROM footages").fetchall():
                        path = r["original_path"]
                        if path and os.path.exists(path):
                            con.execute("UPDATE footages SET original_bytes=? WHERE id=?",
                                        (os.path.getsize(path), r["id"]))
                            repaired += 1
                    if repaired:
                        print(f"[migration] restored true upload size on "
                              f"{repaired} footage(s)")
        con.commit()


def now():
    """UTC timestamp as a naive ISO string.

    Deliberately naive: every timestamp column, string comparison and the
    frontend's `new Date(iso + 'Z')` parsing assume this exact shape. Switching
    to an aware datetime would append "+00:00" and silently change ordering
    comparisons, so the tz is stripped after using the non-deprecated call.
    """
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat()


# ---- generic helpers -------------------------------------------------------
def q(sql, args=(), one=False):
    with _lock, connect() as con:
        cur = con.execute(sql, args)
        rows = cur.fetchall()
        con.commit()
        if one:
            return dict(rows[0]) if rows else None
        return [dict(r) for r in rows]


def execute(sql, args=()):
    with _lock, connect() as con:
        cur = con.execute(sql, args)
        con.commit()
        return cur.lastrowid


# ---- config ----------------------------------------------------------------
def get_config():
    return {r["key"]: r["value"] for r in q("SELECT key,value FROM config")}


def get_cfg(key, cast=float, default=None):
    row = q("SELECT value FROM config WHERE key=?", (key,), one=True)
    # An empty string means "unset — use the caller's default", which is how
    # derived settings like max_concurrent_jobs are expressed in config.
    if not row or row["value"] in (None, ""):
        return default
    try:
        return cast(row["value"])
    except Exception:
        return default if default is not None else row["value"]


def set_config(key, value):
    execute("INSERT INTO config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


# ---- logging (FR30/48/53/54/57) -------------------------------------------
def log(type_, message, severity="info", user="", context=None):
    execute("INSERT INTO logs(ts,type,severity,user,message,context) VALUES(?,?,?,?,?,?)",
            (now(), type_, severity, user, message, json.dumps(context or {})))


# ---- storage metrics (FR31/52) --------------------------------------------
def storage_report():
    rows = q("SELECT tier, COUNT(*) n, SUM(original_bytes) ob, SUM(stored_bytes) sb "
             "FROM segments WHERE tier!='' GROUP BY tier")
    by_tier, total_o, total_s = {}, 0, 0
    for r in rows:
        by_tier[r["tier"]] = {"count": r["n"], "original": r["ob"] or 0,
                              "stored": r["sb"] or 0}
        total_o += r["ob"] or 0
        total_s += r["sb"] or 0
    savings = round(100 * (1 - total_s / total_o), 1) if total_o else 0.0
    return {"by_tier": by_tier, "total_original": total_o,
            "total_stored": total_s, "storage_savings_percent": savings}


def add_recommendation(kind, message):
    execute("INSERT INTO recommendations(ts,kind,message) VALUES(?,?,?)",
            (now(), kind, message))


# ---- per-stage reduction telemetry -----------------------------------------
def record_stage(footage_id, stage, name, unit, items_in=0, items_out=0,
                 bytes_in=0, bytes_out=0, frames_in=0, frames_out=0,
                 duration_ms=0.0, detail=None):
    """Write one stage's before/after counters. Called by the pipeline as each
    stage finishes, so the numbers are measured, not modelled."""
    execute("INSERT INTO stage_stats(footage_id,stage,name,unit,items_in,items_out,"
            "bytes_in,bytes_out,frames_in,frames_out,duration_ms,detail) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (footage_id, stage, name, unit, items_in, items_out, bytes_in,
             bytes_out, frames_in, frames_out, duration_ms,
             json.dumps(detail or {})))


def stage_report(footage_id):
    """Ordered stage rows for one project, each with its reduction percentage
    on the stage's primary unit."""
    rows = q("SELECT * FROM stage_stats WHERE footage_id=? ORDER BY stage, id",
             (footage_id,))
    out = []
    for r in rows:
        try:
            r["detail"] = json.loads(r["detail"] or "{}")
        except Exception:
            r["detail"] = {}
        unit = r["unit"]
        a, b = ((r["bytes_in"], r["bytes_out"]) if unit == "bytes"
                else (r["frames_in"], r["frames_out"]) if unit == "frames"
                else (r["items_in"], r["items_out"]))
        r["reduction_percent"] = round(100 * (1 - b / a), 2) if a else 0.0
        r["kept_percent"] = round(100 * b / a, 2) if a else 0.0
        out.append(r)
    return out


# ---- Tier-3 deletion safeguard ---------------------------------------------
def queue_for_purge(segment_id, footage_id, tier, ssig, path, nbytes,
                    grace_hours, reason=""):
    """Park a segment's video instead of deleting it. Returns the deadline, or
    None when the grace period is 0 (caller deletes immediately)."""
    if grace_hours <= 0:
        return None
    deadline = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                + dt.timedelta(hours=float(grace_hours))).isoformat()
    execute("INSERT OR REPLACE INTO purge_queue(segment_id,footage_id,tier,ssig,"
            "path,bytes,queued_at,purge_after,status,reason) "
            "VALUES(?,?,?,?,?,?,?,?, 'pending', ?)",
            (segment_id, footage_id, tier, ssig, path, nbytes, now(), deadline, reason))
    execute("UPDATE segments SET purge_state='pending' WHERE id=?", (segment_id,))
    return deadline


def due_purges(limit=500):
    return q("SELECT * FROM purge_queue WHERE status='pending' AND purge_after<=? "
             "ORDER BY purge_after LIMIT ?", (now(), limit))


def purge_summary():
    rows = q("SELECT status, COUNT(*) n, SUM(bytes) b FROM purge_queue GROUP BY status")
    out = {r["status"]: {"count": r["n"], "bytes": r["b"] or 0} for r in rows}
    nxt = q("SELECT MIN(purge_after) m FROM purge_queue WHERE status='pending'",
            one=True)
    out["next_purge_at"] = nxt["m"] if nxt else None
    return out

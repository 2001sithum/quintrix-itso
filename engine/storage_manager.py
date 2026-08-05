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
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  job_id TEXT,
  owner_id INTEGER,
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
  stored_bytes INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS segments (
  id TEXT PRIMARY KEY,
  project_id TEXT,
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
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  segment_id TEXT,
  project_id TEXT,
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
  project_id TEXT,
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
"""

DEFAULT_CONFIG = {
    "threshold_high": "0.7",
    "threshold_low": "0.4",
    "alert_threshold": "0.8",
    "motion_sensitivity": "0.001",
    "object_conf_threshold": "0.35",
    "action_min_frames": "16",
    "segment_seconds": "15",
    "context_rules": json.dumps({
        "night_boost": 0.1, "weapon_boost": 0.25, "crowd_boost": 0.15
    }),
}


def init_db():
    with _lock, connect() as con:
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
                  project_id TEXT,
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
        con.commit()


def now():
    return dt.datetime.utcnow().isoformat()


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
    if not row:
        return default
    try:
        return cast(row["value"])
    except Exception:
        return row["value"]


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

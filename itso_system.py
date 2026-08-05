"""
itso_system.py — Master Integration Entry Point.
The public facade described in the system design doc: instantiate once, then
process videos, pull analytics, and run direct frame sentiment analysis.
"""
from engine import storage_manager as sm
from engine import itso_engine


class ITSOIntegratedSystem:
    def __init__(self, db_path=None, archive_dir=None):
        if db_path:
            sm.DB_PATH = db_path
        if archive_dir:
            sm.ARCHIVE_DIR = archive_dir
        sm.init_db()
        self.device = itso_engine.select_device()          # FR60

    # FR08 + full pipeline (synchronous variant for library use)
    def process_video(self, path, project_id):
        proj = sm.q("SELECT * FROM projects WHERE id=?", (str(project_id),), one=True)
        if not proj:
            import uuid, os
            sm.execute(
                "INSERT INTO projects(id,job_id,owner_id,filename,original_path,status,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (str(project_id), uuid.uuid4().hex, 1, os.path.basename(path),
                 path, "uploaded", sm.now()))
        itso_engine.process_project(str(project_id))
        return self.get_project(project_id)

    def get_project(self, project_id):
        p = sm.q("SELECT * FROM projects WHERE id=?", (str(project_id),), one=True)
        segs = sm.q("SELECT * FROM segments WHERE project_id=? ORDER BY idx",
                    (str(project_id),))
        return {"project": p, "segments": segs}

    def get_analytics_summary(self):
        rep = sm.storage_report()
        counts = sm.q("SELECT status, COUNT(*) n FROM projects GROUP BY status")
        tiers = sm.q("SELECT tier, COUNT(*) n FROM segments WHERE tier!='' GROUP BY tier")
        alerts = sm.q("SELECT COUNT(*) n FROM alerts WHERE status='new'", one=True)["n"]
        return {"storage_savings_percent": rep["storage_savings_percent"],
                "storage": rep,
                "projects": {r["status"]: r["n"] for r in counts},
                "tiers": {r["tier"]: r["n"] for r in tiers},
                "open_alerts": alerts,
                "device": self.device}

    def analyze_frame_sentiment(self, frame_numpy_array):    # FR13 direct API
        score, label, threat = itso_engine.analyze_frame_sentiment(frame_numpy_array)
        return {"sentiment_score": score, "sentiment_label": label, "threat_level": threat}

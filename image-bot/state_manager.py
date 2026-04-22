"""Atomic state manager for local job tracking and resume support.

Complements Google Sheets (the primary job queue) with a local state file
so interrupted jobs can be detected and resumed on restart.
"""

import json
import os
import tempfile
from datetime import datetime, timezone


class StateManager:
    """Manages state_{acc_name}.json for tracking per-job progress."""

    def __init__(self, acc_name: str):
        self.path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"state_{acc_name}.json",
        )
        self.state: dict = {}

    def load(self) -> dict:
        """Load existing state or create fresh one."""
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.state = json.load(f)
        else:
            self.state = {
                "acc_name": None,
                "status": "idle",
                "updated_at": None,
                "jobs": {},
            }
        return self.state

    def save(self) -> None:
        """Atomic write: write to tmp file then os.replace."""
        self.state["updated_at"] = datetime.now(timezone.utc).isoformat()
        parent = os.path.dirname(self.path) or "."
        os.makedirs(parent, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def start_job(self, row_num: int, acc_name: str) -> None:
        """Mark a job as started."""
        self.state["acc_name"] = acc_name
        self.state["status"] = "running"
        key = str(row_num)
        self.state["jobs"][key] = {
            "status": "pending",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "attempts": self.state.get("jobs", {}).get(key, {}).get("attempts", 0) + 1,
        }
        self.save()

    def update_job(self, row_num: int, status: str, **kwargs) -> None:
        """Update a single job's status and save."""
        key = str(row_num)
        entry = self.state["jobs"].setdefault(key, {})
        entry["status"] = status
        entry.update(kwargs)
        self.save()

    def finish_job(self, row_num: int, status: str = "success") -> None:
        """Mark a job as completed (success or failed)."""
        self.update_job(row_num, status)

    def get_interrupted_jobs(self) -> list[dict]:
        """Find jobs that were interrupted (not success/failed) on startup.

        Returns list of {"row_num": int, "status": str, "attempts": int}.
        """
        interrupted = []
        for key, info in self.state.get("jobs", {}).items():
            if info.get("status") not in ("success", "failed"):
                interrupted.append({
                    "row_num": int(key),
                    "status": info.get("status", "unknown"),
                    "attempts": info.get("attempts", 0),
                })
        return interrupted

    def set_idle(self) -> None:
        """Mark overall status as idle (bot finished or shutting down)."""
        self.state["status"] = "idle"
        self.save()

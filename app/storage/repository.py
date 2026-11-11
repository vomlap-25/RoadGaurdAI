"""Persistence interfaces and a SQLite development adapter.

Metadata and decision traces are stored in SQLite. Evidence images remain files
owned by the vision pipeline; only their server-generated relative references are
stored with records. A cloud adapter can implement Repository without changing API
or decision-engine business logic.
"""
from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Mapping


class Repository(ABC):
    @abstractmethod
    def health(self) -> bool: ...

    @abstractmethod
    def save_inspection(self, inspection: Mapping[str, Any], issues: list[dict[str, Any]], trace: list[dict[str, Any]]) -> None: ...

    @abstractmethod
    def list_locations(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_location(self, issue_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_trace(self, issue_id: str) -> list[dict[str, Any]] | None: ...

    @abstractmethod
    def review_location(self, issue_id: str, *, decision: str, reviewer: str | None, notes: str | None) -> dict[str, Any] | None: ...


class SQLiteRepository(Repository):
    """Thread/process-safe enough for local development using short SQLite transactions."""

    def __init__(self, database_path: str | Path = "artifacts/roadguard.sqlite3") -> None:
        self.database_path = Path(database_path)
        if str(self.database_path) != ":memory:":
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.database_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS inspections (
                    inspection_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detection_count INTEGER NOT NULL,
                    issue_count INTEGER NOT NULL,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS locations (
                    issue_id TEXT PRIMARY KEY,
                    inspection_id TEXT NOT NULL REFERENCES inspections(inspection_id) ON DELETE CASCADE,
                    priority_score REAL NOT NULL,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    reviewer TEXT,
                    review_notes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_locations_priority ON locations(priority_score DESC);
                CREATE INDEX IF NOT EXISTS idx_locations_status ON locations(status);
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_id TEXT NOT NULL REFERENCES locations(issue_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    trace_json TEXT NOT NULL,
                    UNIQUE(issue_id, sequence)
                );
            """)

    def health(self) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def save_inspection(self, inspection: Mapping[str, Any], issues: list[dict[str, Any]], trace: list[dict[str, Any]]) -> None:
        inspection_id = str(inspection["inspection_id"])
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO inspections(inspection_id,created_at,filename,status,detection_count,issue_count,error) VALUES(?,?,?,?,?,?,?)",
                (inspection_id, inspection["created_at"], inspection["filename"], inspection.get("status", "completed"), int(inspection.get("detection_count", 0)), len(issues), inspection.get("error")),
            )
            trace_by_issue: dict[str, list[dict[str, Any]]] = {str(i["issue_id"]): [] for i in issues}
            for event in trace:
                issue_id = event.get("issue_id")
                if issue_id is None:
                    # Agent traces commonly identify a representative detection, so
                    # attach to the issue containing that detection when possible.
                    detection_id = str(event.get("detection_id", ""))
                    for issue in issues:
                        if any(str(o.get("detection_id", o.get("track_id", ""))) == detection_id for o in issue.get("observations", [])):
                            issue_id = issue["issue_id"]
                            break
                if issue_id is not None and str(issue_id) in trace_by_issue:
                    trace_by_issue[str(issue_id)].append(event)
            for issue in issues:
                record = dict(issue)
                issue_id = str(record["issue_id"])
                score = float((record.get("priority") or {}).get("score", 0))
                conn.execute(
                    "INSERT INTO locations(issue_id,inspection_id,priority_score,status,record_json,created_at) VALUES(?,?,?,?,?,?)",
                    (issue_id, inspection_id, score, str(record.get("status", "prioritized")), json.dumps(record, allow_nan=False), inspection["created_at"]),
                )
                for sequence, event in enumerate(trace_by_issue[issue_id]):
                    conn.execute("INSERT INTO traces(issue_id,sequence,trace_json) VALUES(?,?,?)", (issue_id, sequence, json.dumps(event, default=str, allow_nan=False)))

    def list_locations(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT record_json FROM locations"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY priority_score DESC, created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        results = []
        for row in rows:
            record = json.loads(row["record_json"])
            current = self.get_location(str(record["issue_id"]))
            if current:
                results.append(current)
        return results

    def get_location(self, issue_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT record_json,status,reviewed_at,reviewer,review_notes FROM locations WHERE issue_id=?", (issue_id,)).fetchone()
        if row is None:
            return None
        record = json.loads(row["record_json"])
        record["status"] = row["status"]
        if row["reviewed_at"]:
            record["review"] = {"reviewed_at": row["reviewed_at"], "reviewer": row["reviewer"], "notes": row["review_notes"], "decision": row["status"]}
        return record

    def get_trace(self, issue_id: str) -> list[dict[str, Any]] | None:
        with self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM locations WHERE issue_id=?", (issue_id,)).fetchone()
            if exists is None:
                return None
            rows = conn.execute("SELECT trace_json FROM traces WHERE issue_id=? ORDER BY sequence", (issue_id,)).fetchall()
        return [json.loads(row["trace_json"]) for row in rows]

    def review_location(self, issue_id: str, *, decision: str, reviewer: str | None, notes: str | None) -> dict[str, Any] | None:
        from datetime import datetime, timezone
        reviewed_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            row = conn.execute("SELECT record_json FROM locations WHERE issue_id=?", (issue_id,)).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE locations SET status=?, reviewed_at=?, reviewer=?, review_notes=? WHERE issue_id=?", (decision, reviewed_at, reviewer, notes, issue_id))
            conn.execute(
                "INSERT INTO traces(issue_id,sequence,trace_json) SELECT ?, COALESCE(MAX(sequence),-1)+1, ? FROM traces WHERE issue_id=?",
                (issue_id, json.dumps({"evidence": {"reviewer": reviewer, "notes": notes}, "decision": "human_review_submitted", "action": decision, "outcome": f"human reviewer recorded {decision}", "issue_id": issue_id, "timestamp": reviewed_at}, allow_nan=False), issue_id),
            )
        return self.get_location(issue_id)


__all__ = ["Repository", "SQLiteRepository"]

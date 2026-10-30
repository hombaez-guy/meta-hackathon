"""Report store. MongoDB when MONGO_URI is set, JSON files under data/db/ otherwise.

Three collections: reports, findings, fixes. The Reviewer reads all three for history.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.settings import settings
from core.schemas import FixProposal, FixRecord, Report


class JsonStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, coll: str) -> Path:
        return self.root / f"{coll}.json"

    def _load(self, coll: str) -> list[dict[str, Any]]:
        p = self._path(coll)
        return json.loads(p.read_text()) if p.exists() else []

    def _save(self, coll: str, rows: list[dict[str, Any]]) -> None:
        self._path(coll).write_text(json.dumps(rows, indent=2))

    def insert(self, coll: str, doc: dict[str, Any]) -> None:
        rows = self._load(coll)
        rows.append(doc)
        self._save(coll, rows)

    def upsert(self, coll: str, key: str, doc: dict[str, Any]) -> None:
        rows = [r for r in self._load(coll) if r.get(key) != doc.get(key)]
        rows.append(doc)
        self._save(coll, rows)

    def find(self, coll: str, **where: Any) -> list[dict[str, Any]]:
        return [r for r in self._load(coll) if all(r.get(k) == v for k, v in where.items())]

    def all(self, coll: str) -> list[dict[str, Any]]:
        return self._load(coll)


class MongoStore:
    def __init__(self, uri: str, db: str):
        from core.mongo import get_db

        self.db = get_db()  # raises if Mongo is down; _backend() falls back to JSON

    def insert(self, coll: str, doc: dict[str, Any]) -> None:
        self.db[coll].insert_one(dict(doc))

    def upsert(self, coll: str, key: str, doc: dict[str, Any]) -> None:
        self.db[coll].replace_one({key: doc[key]}, dict(doc), upsert=True)

    def find(self, coll: str, **where: Any) -> list[dict[str, Any]]:
        return [self._clean(r) for r in self.db[coll].find(where)]

    def all(self, coll: str) -> list[dict[str, Any]]:
        return [self._clean(r) for r in self.db[coll].find()]

    @staticmethod
    def _clean(r: dict[str, Any]) -> dict[str, Any]:
        r.pop("_id", None)
        return r


def _backend():
    if settings.mongo_uri:
        try:
            return MongoStore(settings.mongo_uri, settings.mongo_db)
        except Exception:  # noqa: BLE001 - fall back rather than die on a missing Mongo
            pass
    return JsonStore(settings.db_dir)


class Store:
    """The only thing the rest of Foreman talks to."""

    def __init__(self):
        self.b = _backend()

    # Reports
    def save_report(self, report: Report) -> None:
        self.b.upsert("reports", "report_id", report.model_dump(by_alias=True))
        for f in report.review.findings:
            self.b.upsert(
                "findings",
                "finding_id",
                {**f.model_dump(exclude={"fix"}), "report_id": report.report_id, "created_at": report.created_at},
            )

    def get_report(self, report_id: str) -> Report | None:
        rows = self.b.find("reports", report_id=report_id)
        return Report.model_validate(rows[0]) if rows else None

    def latest_report(self) -> Report | None:
        rows = sorted(self.b.all("reports"), key=lambda r: r["created_at"])
        return Report.model_validate(rows[-1]) if rows else None

    def list_reports(self, limit: int = 30) -> list[Report]:
        rows = sorted(self.b.all("reports"), key=lambda r: r["created_at"], reverse=True)[:limit]
        return [Report.model_validate(r) for r in rows]

    # Fixes
    def record_fix(self, rec: FixRecord) -> None:
        self.b.upsert("fixes", "fix_id", rec.model_dump())

    def fixes(self, **where: Any) -> list[FixRecord]:
        return [FixRecord.model_validate(r) for r in self.b.find("fixes", **where)]

    def find_fix(self, fix_id: str) -> tuple[Report, FixProposal] | None:
        for rep in self.list_reports(limit=200):
            for f in rep.review.findings:
                if f.fix and f.fix.fix_id == fix_id:
                    return rep, f.fix
        return None

    # History for the Reviewer
    def history(self, days: int | None = None) -> dict[str, Any]:
        days = days or settings.history_days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        reports = [r for r in self.list_reports(limit=200) if r.created_at >= cutoff]
        findings = [f for f in self.b.all("findings") if f.get("created_at", "") >= cutoff]
        fixes = [f for f in self.b.all("fixes") if f.get("created_at", "") >= cutoff]
        return {
            "window_days": days,
            "report_count": len(reports),
            "reports": [
                {
                    "date": r.created_at[:16],
                    "status": r.review.overall_status,
                    "headline": r.review.headline,
                    "cpu": r.chip.cpu_percent,
                    "memory": r.chip.memory_percent,
                    "disk": r.chip.disk_percent,
                    "open_findings": [f.title for f in r.review.findings],
                }
                for r in sorted(reports, key=lambda r: r.created_at)
            ],
            "findings": [
                {"title": f["title"], "severity": f["severity"], "date": f.get("created_at", "")[:16]} for f in findings
            ],
            "fixes": [
                {
                    "title": f["title"],
                    "state": f["state"],
                    "applied_at": f.get("applied_at"),
                    "result": (f.get("result") or "")[:200],
                }
                for f in fixes
            ],
        }

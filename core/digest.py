"""Condenses Chuck's raw log scrape into what the Reviewer actually needs.

chuck.py returns up to 500 lines per file. The model shouldn't read all of that.
It gets: counts by level, errors grouped by normalized message with counts and
first/last seen, the files involved, and a short tail of the newest errors.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from core.schemas import ChuckCheck

_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_NUM = re.compile(r"\b\d+(?:\.\d+)?\b")
_HEX = re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{8,}\b", re.I)
_ID = re.compile(r"\b(?:req|request|trace|span|session|job|task|pid|id)[_-]?id?=\S+|\b[A-Za-z]+_?id=\S+", re.I)
_TOKEN = re.compile(r"\b(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{5,}\b", re.I)  # mixed alnum tokens like a00cf2, 7f3kq9


def _normalize(msg: str) -> str:
    """Strip timestamps, numbers and ids so the same error groups together."""
    m = _TS.sub("", msg)
    m = _ID.sub("ID", m)
    m = _HEX.sub("#", m)
    m = _TOKEN.sub("#", m)
    m = _NUM.sub("N", m)
    m = re.sub(r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b", "", m)
    return " ".join(m.split())[:160]


def digest_chuck(chuck: ChuckCheck, max_groups: int = 12, tail: int = 8) -> dict[str, Any]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for e in chuck.logs:
        if e.level not in ("WARNING", "ERROR", "CRITICAL", "FATAL"):
            continue
        key = (e.level, _normalize(e.message))
        g = groups.setdefault(key, {"level": e.level, "pattern": key[1], "count": 0, "files": set(), "first_seen": e.timestamp, "last_seen": e.timestamp, "example": e.message.strip()[:240]})
        g["count"] += 1
        g["files"].add(e.file.rsplit("/", 1)[-1])
        if e.timestamp:
            if not g["first_seen"] or e.timestamp < g["first_seen"]:
                g["first_seen"] = e.timestamp
            if not g["last_seen"] or e.timestamp > g["last_seen"]:
                g["last_seen"] = e.timestamp

    rank = {"FATAL": 0, "CRITICAL": 0, "ERROR": 1, "WARNING": 2}
    ordered = sorted(groups.values(), key=lambda g: (rank.get(g["level"], 3), -g["count"]))[:max_groups]
    for g in ordered:
        g["files"] = sorted(g["files"])

    newest_errors = [
        {"t": e.timestamp, "level": e.level, "file": e.file.rsplit("/", 1)[-1], "message": e.message.strip()[:200]}
        for e in chuck.logs
        if e.level in ("ERROR", "CRITICAL", "FATAL")
    ][:tail]

    return {
        "scraped_at": chuck.timestamp,
        "host": chuck.host,
        "window_hours": chuck.hours_searched,
        "counts": chuck.summary.model_dump(),
        "files": [f.model_dump() for f in chuck.files[:10]],
        "error_groups": ordered,
        "newest_errors": newest_errors,
        "derived_status": chuck.status,
    }


def one_line(chuck: ChuckCheck) -> str:
    s = chuck.summary
    return f"{s.total_entries} lines from {s.files_with_logs} file(s): {s.errors} error(s), {s.warnings} warning(s)."

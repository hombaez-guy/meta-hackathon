"""Where the Reviewer gets Chip's and Chuck's output.

chip.py and chuck.py store their results through mongo_store.py (repo root):
  health_results   one doc per Chip run      (health.json + stored_at)
  log_summaries    one doc per Chuck run     (logs.json minus the lines + stored_at)
  logs             one doc per log line      (summary_id -> log_summaries._id)

This module reads the newest of each and rebuilds the exact shapes ChipCheck and
ChuckCheck expect, so nothing downstream knows or cares where the data came from.
If Mongo is unreachable, or FOREMAN_INPUT=file, it reads data/inbox/*.json instead.

"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import settings
from core.schemas import ChipCheck, ChuckCheck

log = logging.getLogger("foreman")

# Keys mongo_store adds that are not part of the collector contracts
_MONGO_EXTRA = {"_id", "stored_at", "host", "summary_id", "raw_metrics"}


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def _clean(doc: dict[str, Any], keep_host: bool = False) -> dict[str, Any]:
    out = {k: _iso(v) for k, v in doc.items() if k not in _MONGO_EXTRA or (keep_host and k == "host")}
    return out


def _db():
    """Verified handle to the shared database. Raises if Mongo is down."""
    from core.mongo import get_db

    return get_db()


# ---------- Mongo readers ----------


def chip_from_mongo() -> tuple[ChipCheck, str] | None:
    db = _db()
    doc = db["health_results"].find_one(sort=[("stored_at", -1)])
    if not doc:
        return None
    return ChipCheck.model_validate(_clean(doc)), str(doc["_id"])


def chuck_from_mongo() -> tuple[ChuckCheck, str] | None:
    db = _db()
    summary = db["log_summaries"].find_one(sort=[("stored_at", -1)])
    if not summary:
        return None
    lines = [
        _clean(e) for e in db["logs"].find({"summary_id": summary["_id"]}).sort("timestamp", -1)
    ]
    payload = _clean(summary, keep_host=True)
    payload["logs"] = lines
    return ChuckCheck.model_validate(payload), str(summary["_id"])


# ---------- File readers ----------


def chip_from_file(path: Path | str | None = None) -> ChipCheck | None:
    p = Path(path or settings.chip_file)
    return ChipCheck.model_validate(json.loads(p.read_text())) if p.exists() else None


def chuck_from_file(path: Path | str | None = None) -> ChuckCheck | None:
    p = Path(path or settings.chuck_file)
    return ChuckCheck.model_validate(json.loads(p.read_text())) if p.exists() else None


# ---------- The one function the pipeline calls ----------


def load_inputs(
    chip_path: Path | str | None = None,
    chuck_path: Path | str | None = None,
    source: str | None = None,
) -> tuple[ChipCheck, ChuckCheck | None, dict[str, str]]:
    """Return (chip, chuck, provenance). source: auto | mongo | file. Explicit paths force file."""
    source = (source or settings.input_source or "auto").lower()
    if chip_path or chuck_path:
        source = "file"
    prov: dict[str, str] = {}

    chip = chuck = None
    if source in ("auto", "mongo"):
        try:
            got = chip_from_mongo()
            if got:
                chip, prov["chip"] = got[0], f"mongo:health_results/{got[1]}"
            got = chuck_from_mongo()
            if got:
                chuck, prov["chuck"] = got[0], f"mongo:log_summaries/{got[1]}"
        except Exception as e:  # noqa: BLE001 - Mongo down or not configured
            if source == "mongo":
                raise
            log.info("mongo not available (%s); reading data/inbox files", type(e).__name__)

    if chip is None and source != "mongo":
        chip = chip_from_file(chip_path)
        if chip:
            prov["chip"] = f"file:{chip_path or settings.chip_file}"
    if chuck is None and source != "mongo":
        chuck = chuck_from_file(chuck_path)
        if chuck:
            prov["chuck"] = f"file:{chuck_path or settings.chuck_file}"

    if chip is None:
        raise FileNotFoundError("No Chip result found in Mongo (health_results) or data/inbox/health.json")
    return chip, chuck, prov

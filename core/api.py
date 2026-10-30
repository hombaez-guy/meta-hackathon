"""FastAPI surface the dashboard (and later Muse) talks to."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.settings import settings
from core.pipeline import apply_fix, rerun_fix, run_review
from core.store import Store

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_: FastAPI):
    from core import scheduler

    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Foreman", version="0.1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

CONN_FILE = settings.data_dir / "connections.json"


def _conns() -> dict:
    return json.loads(CONN_FILE.read_text()) if CONN_FILE.exists() else {"server": None, "muse": None}


def _save_conns(c: dict) -> None:
    CONN_FILE.write_text(json.dumps(c, indent=2))


class ServerConn(BaseModel):
    name: str
    host: str = ""
    chip_path: str = "data/inbox/health.json"
    chuck_path: str = "data/inbox/logs.json"


class MuseConn(BaseModel):
    reviewer_model: str = settings.reviewer_model
    fixer_model: str = settings.fixer_model
    api_key_set: bool = bool(settings.model_api_key)


class RunRequest(BaseModel):
    chip_path: str | None = None
    chuck_path: str | None = None
    source: str | None = None  # auto | mongo | file


@app.get("/api/health")
def health():
    mongo = "off"
    if settings.mongo_uri:
        try:
            from core.mongo import get_db

            get_db()
            mongo = "connected"
        except Exception:  # noqa: BLE001
            mongo = "unreachable"
    return {"ok": True, "mock": settings.mock, "reviewer": settings.reviewer_model, "fixer": settings.fixer_model,
            "mongo": mongo, "mongo_db": settings.mongo_db, "input": settings.input_source,
            "review_interval_min": settings.review_interval_min, "server": settings.server_name}


@app.get("/api/connections")
def connections():
    c = _conns()
    c["muse"] = c.get("muse") or MuseConn().model_dump()
    return c


@app.post("/api/connect/server")
def connect_server(body: ServerConn):
    c = _conns()
    c["server"] = body.model_dump()
    _save_conns(c)
    return c["server"]


@app.post("/api/connect/muse")
def connect_muse(body: MuseConn):
    c = _conns()
    c["muse"] = {**body.model_dump(), "api_key_set": bool(settings.model_api_key)}
    _save_conns(c)
    return c["muse"]


@app.get("/api/reports")
def list_reports(limit: int = 30):
    out = []
    for r in Store().list_reports(limit):
        out.append(
            {
                "report_id": r.report_id,
                "created_at": r.created_at,
                "server": r.server,
                "status": r.review.overall_status,
                "headline": r.review.headline,
                "findings": len(r.review.findings),
                "open_fixes": len(r.open_fixes),
            }
        )
    return out


@app.get("/api/reports/latest")
def latest():
    r = Store().latest_report()
    if not r:
        raise HTTPException(404, "no reports yet")
    return r.model_dump(by_alias=True)


@app.get("/api/reports/{report_id}")
def get_report(report_id: str):
    r = Store().get_report(report_id)
    if not r:
        raise HTTPException(404, report_id)
    return r.model_dump(by_alias=True)


@app.get("/api/reports/{report_id}/html", response_class=HTMLResponse)
def report_html(report_id: str):
    r = Store().get_report(report_id)
    if not r or not r.html_path or not Path(r.html_path).exists():
        raise HTTPException(404, report_id)
    return Path(r.html_path).read_text()


@app.get("/api/metrics")
def metrics(limit: int = 30):
    """Series for the dashboard graphs."""
    rows = sorted(Store().list_reports(limit), key=lambda r: r.created_at)
    return [
        {"t": r.created_at[:16], "cpu": r.chip.cpu_percent, "memory": r.chip.memory_percent, "disk": r.chip.disk_percent,
         "status": r.review.overall_status, "findings": len(r.review.findings)}
        for r in rows
    ]


@app.get("/api/logs/search")
def log_search(q: str, limit: int = 50):
    """Search Chuck's events across stored reports."""
    q = q.lower()
    hits = []
    for r in Store().list_reports(100):
        if not r.chuck:
            continue
        for e in r.chuck.logs:
            if q in e.message.lower() or q in e.file.lower():
                hits.append({"report_id": r.report_id, "date": r.created_at[:16], "level": e.level.lower(),
                             "source": e.file.rsplit("/", 1)[-1], "message": e.message.strip(), "count": 1, "t": e.timestamp})
                if len(hits) >= limit:
                    return hits
    return hits


@app.post("/api/run")
async def run(body: RunRequest):
    c = _conns().get("server") or {}
    try:
        r = await run_review(body.chip_path, body.chuck_path, server=c.get("name", "default"), source=body.source)
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    return {"report_id": r.report_id, "status": r.review.overall_status}


@app.post("/api/tick")
async def tick(force: bool = False):
    """Scheduler pass on demand: review if new Chip/Chuck data exists (force=true reviews regardless)."""
    from core import scheduler

    return {"result": await scheduler.tick(force=force)}


@app.post("/api/fixes/{fix_id}/apply")
def apply(fix_id: str):
    try:
        return apply_fix(fix_id).model_dump()
    except KeyError:
        raise HTTPException(404, fix_id)


@app.post("/api/fixes/{fix_id}/rerun")
async def rerun(fix_id: str):
    try:
        return (await rerun_fix(fix_id)).model_dump()
    except KeyError:
        raise HTTPException(404, fix_id)


@app.get("/api/fixes")
def fixes():
    return [f.model_dump() for f in Store().fixes()]


# Serve the built dashboard if ui/dist exists
_dist = settings.data_dir.parent / "ui" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="ui")

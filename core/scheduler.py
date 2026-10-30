"""Foreman's own clock.

The cron runs Chip and Chuck every 30 minutes. This loop runs inside the Foreman
container, wakes every FOREMAN_REVIEW_INTERVAL_MIN minutes, and runs a review only when
the newest Chip/Chuck result is one it hasn't reviewed yet. So the two clocks don't need
to be synchronized, and a stalled collector doesn't produce duplicate reports.

FOREMAN_REVIEW_INTERVAL_MIN=0 turns it off (manual runs only, via CLI or the dashboard).
For the demo, set it to 1 and the whole loop plays out on a compressed clock.
"""

from __future__ import annotations

import asyncio
import logging

from config.settings import settings
from core.inputs import load_inputs
from core.pipeline import run_review

log = logging.getLogger("foreman")

_last_seen: dict[str, str] = {}
_task: asyncio.Task | None = None


def _fingerprint(prov: dict[str, str]) -> dict[str, str]:
    """What identifies 'the same inputs as last time'. Mongo ids when from Mongo, file mtimes when from files."""
    import os

    fp = {}
    for k, v in prov.items():
        if v.startswith("file:"):
            path = v[5:]
            try:
                fp[k] = f"{path}@{int(os.path.getmtime(path))}"
            except OSError:
                fp[k] = v
        else:
            fp[k] = v
    return fp


async def tick(force: bool = False) -> str:
    """One scheduler pass. Returns a short status line for the log."""
    global _last_seen
    try:
        _, _, prov = load_inputs()
    except FileNotFoundError:
        return "no inputs yet"
    fp = _fingerprint(prov)
    if not force and fp == _last_seen:
        return "no new inputs"
    rep = await run_review(server=settings.server_name)
    _last_seen = fp
    return f"reviewed -> {rep.report_id} ({rep.review.overall_status}, {len(rep.review.findings)} findings)"


async def _loop() -> None:
    interval = max(1, settings.review_interval_min) * 60
    log.info("scheduler: reviewing every %d min when new Chip/Chuck data appears", settings.review_interval_min)
    await asyncio.sleep(10)  # let the collectors' first run land
    while True:
        try:
            log.info("scheduler: %s", await tick())
        except Exception as e:  # noqa: BLE001 - keep the loop alive
            log.error("scheduler: review failed: %s: %s", type(e).__name__, str(e)[:200])
        await asyncio.sleep(interval)


def start() -> None:
    global _task
    if settings.review_interval_min <= 0 or _task:
        return
    _task = asyncio.get_event_loop().create_task(_loop())


def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None

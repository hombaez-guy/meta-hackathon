"""Orchestration: inbox JSON -> Reviewer -> The Fixer (per finding) -> store -> HTML report.

Also the rerun and apply paths the API calls.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from config.settings import settings
from core.agents import FixDeps, ReviewDeps, fixer_agent, reviewer_agent
from core.executor import execute
from core.inputs import load_inputs
from core.report import render_report
from core.schemas import ChipCheck, ChuckCheck, Finding, FixProposal, FixRecord, Report, ReviewerOutput, now_iso
from core.store import Store

log = logging.getLogger("foreman")


def _load_json(path: Path | str | None):
    if not path:
        return None
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else None


async def propose_fix(
    finding: Finding, chip: ChipCheck, chuck: ChuckCheck | None, store: Store, rejected: list[FixProposal] | None = None
) -> FixProposal:
    applied = [f.model_dump(include={"title", "state", "applied_at", "result"}) for f in store.fixes(state="applied")]
    deps = FixDeps(finding=finding, chip=chip, chuck=chuck, rejected=rejected or [], applied_history=applied)
    run = await fixer_agent.run("Propose one executable fix for this finding.", deps=deps)
    return run.output


async def run_review(
    chip_path: Path | str | None = None,
    chuck_path: Path | str | None = None,
    server: str = "default",
    source: str | None = None,
) -> Report:
    store = Store()
    chip, chuck, prov = load_inputs(chip_path, chuck_path, source)
    log.info("inputs: chip=%s chuck=%s", prov.get("chip"), prov.get("chuck", "none"))
    history = store.history()

    log.info("reviewer: %s | chip=%s chuck=%s history=%d reports", settings.reviewer_model, chip.status, chuck.status if chuck else "none", history["report_count"])
    run = await reviewer_agent.run("Write today's report.", deps=ReviewDeps(chip=chip, chuck=chuck, history=history, server=server))
    review = ReviewerOutput.from_draft(run.output)

    # The Reviewer hands each actionable finding to The Fixer
    for finding in review.findings:
        if finding.needs_fix:
            log.info("fixer: %s -> %s", settings.fixer_model, finding.title)
            try:
                finding.fix = await propose_fix(finding, chip, chuck, store)
            except Exception as e:  # noqa: BLE001 - a bad fix must not sink the report
                log.error("fixer failed for '%s': %s: %s", finding.title, type(e).__name__, str(e)[:200])
                finding.fix = None

    report = Report(server=server, chip=chip, chuck=chuck, review=review, reviewer_model=settings.reviewer_model, inputs=prov)
    html = render_report(report)
    out = settings.reports_dir / f"{report.report_id}.html"
    out.write_text(html)
    report.html_path = str(out)
    store.save_report(report)
    for f in report.review.findings:
        if f.fix:
            store.record_fix(_to_record(report, f.fix))
    log.info("report %s written (%s, %d findings)", report.report_id, review.overall_status, len(review.findings))
    return report


def _to_record(report: Report, fix: FixProposal) -> FixRecord:
    return FixRecord(
        fix_id=fix.fix_id,
        finding_id=fix.finding_id,
        report_id=report.report_id,
        title=fix.title,
        state=fix.state,
        attempt=fix.attempt,
        steps=fix.steps,
        result=fix.result or "",
        applied_at=fix.applied_at,
        proposal=fix.model_dump(),
    )


def _refresh(store: Store, report: Report) -> None:
    html = render_report(report)
    if report.html_path:
        Path(report.html_path).write_text(html)
    store.save_report(report)


async def rerun_fix(fix_id: str) -> FixProposal:
    """Admin rejected a proposal. Ask The Fixer for a different one and say what it changes."""
    store = Store()
    found = store.find_fix(fix_id)
    if not found:
        raise KeyError(fix_id)
    report, old = found
    finding = next(f for f in report.review.findings if f.finding_id == old.finding_id)
    old.state = "rejected"
    store.record_fix(_to_record(report, old))
    rejected = [FixProposal.model_validate(r.proposal) for r in store.fixes(finding_id=finding.finding_id, state="rejected") if r.proposal] or [old]
    new = await propose_fix(finding, report.chip, report.chuck, store, rejected=rejected)
    finding.fix = new
    store.record_fix(_to_record(report, new))
    _refresh(store, report)
    return new


def apply_fix(fix_id: str) -> FixProposal:
    """Admin approved. Run the steps, record the outcome. The Reviewer sees it next run."""
    store = Store()
    found = store.find_fix(fix_id)
    if not found:
        raise KeyError(fix_id)
    report, fix = found
    ok, output = execute(fix)
    fix.state = "applied" if ok else "failed"
    fix.applied_at = now_iso()
    fix.result = output
    store.record_fix(_to_record(report, fix))
    _refresh(store, report)
    return fix

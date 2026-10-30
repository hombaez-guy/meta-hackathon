"""The two Pydantic AI agents.

Reviewer  -> muse-wonder-1.0   reads Chip + Chuck + history, writes ReviewerOutput
The Fixer -> muse-spark-1.3    gets one Finding, returns one FixProposal

Both talk to Meta Model API through its OpenAI-compatible endpoint.
FOREMAN_MOCK=1 swaps in a local stand-in model so the pipeline runs with no key.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, ModelRetry, PromptedOutput, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from config.settings import settings
from core.digest import digest_chuck
from core.instructions import load_instructions
log = logging.getLogger("foreman")

from core.schemas import ChipCheck, ChuckCheck, Finding, FixProposal, ReviewerDraft, new_id


# ---------- Model wiring ----------


def _provider() -> OpenAIProvider:
    return OpenAIProvider(base_url=settings.model_api_base_url, api_key=settings.model_api_key or "missing")


def build_model(model_id: str, reasoning_effort: str):
    if settings.mock:
        from core.mock_models import mock_model

        return mock_model(model_id)
    return OpenAIChatModel(
        model_id,
        provider=_provider(),
        settings={"openai_reasoning_effort": reasoning_effort, "timeout": settings.model_timeout},
    )


# ---------- Dependencies ----------


@dataclass
class ReviewDeps:
    chip: ChipCheck
    chuck: ChuckCheck | None
    history: dict[str, Any]
    server: str = "default"


@dataclass
class FixDeps:
    finding: Finding
    chip: ChipCheck
    chuck: ChuckCheck | None
    rejected: list[FixProposal]
    applied_history: list[dict[str, Any]]


# ---------- Reviewer ----------

REVIEWER_PROMPT, REVIEWER_META = load_instructions("reviewer")

reviewer_agent: Agent[ReviewDeps, ReviewerDraft] = Agent(
    build_model(settings.reviewer_model, settings.reviewer_reasoning_effort),
    deps_type=ReviewDeps,
    output_type=PromptedOutput(ReviewerDraft, name="daily_report"),  # not NativeOutput: Meta's constrained decoding drops fields that have defaults
    instructions=REVIEWER_PROMPT,
    retries=2,
)


@reviewer_agent.instructions
def reviewer_context(ctx: RunContext[ReviewDeps]) -> str:
    d = ctx.deps
    chuck = digest_chuck(d.chuck) if d.chuck else None
    return (
        f"SERVER: {d.server}\n\n"
        f"CHIP (metrics):\n{json.dumps(d.chip.model_dump(by_alias=True), indent=1)}\n\n"
        f"CHUCK (log digest; Chuck is a scraper, you are the one reading the logs):\n{json.dumps(chuck, indent=1) if chuck else 'NOT PROVIDED - logs were not reviewed this run'}\n\n"
        f"HISTORY (last {d.history.get('window_days')} days, {d.history.get('report_count')} reports):\n"
        f"{json.dumps(d.history, indent=1)}"
    )


@reviewer_agent.output_validator
def reviewer_guardrails(ctx: RunContext[ReviewDeps], out: ReviewerDraft) -> ReviewerDraft:
    """Deterministic checks that don't depend on the model behaving. Contradictions go back to the model."""
    chip = ctx.deps.chip
    worst = max(chip.cpu_percent, chip.memory_percent, chip.disk_percent)
    floor: str = "critical" if worst > 90 else "warning" if worst > 75 else "healthy"
    rank = {"healthy": 0, "warning": 1, "critical": 2}
    if rank[out.overall_status] < rank[floor]:
        out.overall_status = floor  # the model can't call a 92% disk "healthy"
    if out.overall_status != "healthy" and not out.findings:
        log.warning("reviewer returned %s with no findings; asking the model to retry (headline: %s)", out.overall_status, out.headline[:80])
        raise ModelRetry(
            f"overall_status is {out.overall_status} and the headline says '{out.headline}', but findings is empty. "
            "Every issue you mention must be its own entry in findings with evidence, impact, severity and needs_fix. "
            "Return the same report with findings filled in."
        )
    if out.findings and out.no_issues_statement:
        out.no_issues_statement = ""
    if not out.findings and not out.no_issues_statement:
        out.no_issues_statement = "No action required. All metrics are inside thresholds and the logs show no errors above the reporting rules."
    return out


# ---------- The Fixer ----------

FIXER_PROMPT, FIXER_META = load_instructions("fixer")

fixer_agent: Agent[FixDeps, FixProposal] = Agent(
    build_model(settings.fixer_model, settings.fixer_reasoning_effort),
    deps_type=FixDeps,
    output_type=PromptedOutput(FixProposal, name="fix_proposal"),
    instructions=FIXER_PROMPT,
    retries=2,
)


@fixer_agent.instructions
def fixer_context(ctx: RunContext[FixDeps]) -> str:
    d = ctx.deps
    parts = [
        f"FINDING:\n{json.dumps(d.finding.model_dump(exclude={'fix'}), indent=1)}",
        f"CURRENT METRICS:\n{json.dumps(d.chip.model_dump(by_alias=True), indent=1)}",
    ]
    if d.chuck:
        parts.append(f"LOG ERROR GROUPS:\n{json.dumps(digest_chuck(d.chuck)['error_groups'], indent=1)}")
    if d.applied_history:
        parts.append(f"FIXES ALREADY APPLIED IN THE HISTORY WINDOW:\n{json.dumps(d.applied_history, indent=1)}")
    if d.rejected:
        parts.append(
            "THIS IS A RERUN. The admin rejected these proposals. Propose a materially different approach "
            f"and fill conflicts_with_previous:\n{json.dumps([r.model_dump() for r in d.rejected], indent=1)}"
        )
    return "\n\n".join(parts)


# Paths The Fixer may delete under (from fixer.xml <never>). Anything else with rm is sent back.
ALLOWED_RM_ROOTS = ("/tmp/", "/var/tmp/", "/var/log/", "/var/cache/")
BANNED = ("mkfs", "dd if=", "shutdown", "reboot", "iptables", "ufw ", "passwd", "visudo", "sshd", "userdel", "useradd", "chmod -R 777")
_RM = re.compile(r"\brm\s+((?:-\w+\s+)*)(.+?)(?=\s*(?:;|&&|\|\||$))")


def _check_command(cmd: str) -> str | None:
    """Return a reason if the command is outside the allowed action families, else None."""
    low = cmd.lower()
    for b in BANNED:
        if b in low:
            return f"uses '{b.strip()}'"
    for m in _RM.finditer(cmd):
        for target in m.group(2).split():
            if target.startswith("-") or re.match(r"^\d*[<>]", target) or target in ("2>&1", "&>"):
                continue  # flags and shell redirects are not targets
            t = target.rstrip("/*") + "/"
            if t == "/" or not any(t.startswith(root) for root in ALLOWED_RM_ROOTS):
                return f"rm target '{target}' is outside {', '.join(r.rstrip('/') for r in ALLOWED_RM_ROOTS)}"
    return None


@fixer_agent.output_validator
def fixer_guardrails(ctx: RunContext[FixDeps], fix: FixProposal) -> FixProposal:
    fix.finding_id = ctx.deps.finding.finding_id
    if not fix.fix_id.startswith("fix_") or len(fix.fix_id) != 14:
        fix.fix_id = new_id("fix")  # the model sometimes invents its own id format
    fix.model = settings.fixer_model
    fix.attempt = len(ctx.deps.rejected) + 1
    fix.state = "proposed"
    for i, step in enumerate(fix.steps, 1):
        reason = _check_command(step.command)
        if reason:
            log.warning("fixer step %d rejected (%s): %s", i, reason, step.command[:120])
            raise ModelRetry(
                f"Step {i} is not allowed: {reason}. Command was: {step.command}\n"
                "Stay inside the allowed action families and the allowed rm paths (/tmp, /var/tmp, /var/log, /var/cache, docker prune). "
                "Return the whole proposal again with that step replaced or removed."
            )
    if any(s.risk == "high" for s in fix.steps):
        fix.risk = "high"
    elif any(s.risk == "medium" for s in fix.steps):
        fix.risk = "medium" if fix.risk == "low" else fix.risk
    if ctx.deps.rejected and not fix.conflicts_with_previous:
        fix.conflicts_with_previous = "No new issues versus the previous proposal."
    return fix

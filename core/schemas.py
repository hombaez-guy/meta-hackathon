"""Every record Foreman reads or writes. The contract between Chip, Chuck, the Reviewer, the Fixer, the DB and the UI."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Status = Literal["healthy", "warning", "critical"]
Severity = Literal["info", "low", "medium", "high", "critical"]
FixState = Literal["proposed", "applied", "failed", "rejected", "superseded"]
Risk = Literal["low", "medium", "high"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------- Inputs (what Chip and Chuck hand us) ----------


class ChipCheck(BaseModel):
    """Chip: server metrics. Matches chip.py's health.json."""

    status: Status
    timestamp: str
    summary: str = ""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    disk_percent: float = 0.0
    issues: list[str] = []
    recommendations: list[str] = []
    source: str = Field(default="chip", alias="_source")

    model_config = {"populate_by_name": True}


class LogEntry(BaseModel):
    """One line from chuck.py."""

    file: str = ""
    line: int = 0
    timestamp: str | None = None
    level: str = "INFO"
    message: str = ""


class LogSummary(BaseModel):
    total_entries: int = 0
    files_with_logs: int = 0
    files_scanned: int = 0
    by_level: dict[str, int] = {}
    errors: int = 0
    warnings: int = 0


class LogFile(BaseModel):
    path: str
    entries: int = 0
    modified: str | None = None


class ChuckCheck(BaseModel):
    """Chuck: log scrape. Matches chuck.py's logs.json exactly. No AI in Chuck; the Reviewer does the reading."""

    timestamp: str
    host: str = ""
    system: str = ""
    search_paths: list[str] = []
    hours_searched: int | None = None
    filter_level: str | None = None
    filter_keyword: str | None = None
    summary: LogSummary = LogSummary()
    files: list[LogFile] = []
    logs: list[LogEntry] = []

    @property
    def status(self) -> Status:
        if self.summary.errors >= 5 or any(e.level in ("CRITICAL", "FATAL") for e in self.logs):
            return "critical"
        if self.summary.errors or self.summary.warnings:
            return "warning"
        return "healthy"


# ---------- Findings and fixes ----------


class FixStep(BaseModel):
    description: str
    command: str
    risk: Risk = "low"
    reversible: bool = True


class FixProposal(BaseModel):
    """What the Fixer returns. Executable, explained, with side effects named."""

    fix_id: str = Field(default_factory=lambda: new_id("fix"))
    finding_id: str = ""
    title: str
    explanation: str = Field(description="Why this fixes the issue, in plain language")
    steps: list[FixStep]
    side_effects: list[str] = Field(default=[], description="What else this touches, including downtime")
    rollback: str = Field(default="", description="How to undo it")
    risk: Risk = "low"
    conflicts_with_previous: str = Field(
        default="",
        description="On a rerun: whether this alternative introduces any issue the previous proposal didn't",
    )
    state: FixState = "proposed"
    attempt: int = 1
    model: str = ""
    created_at: str = Field(default_factory=now_iso)
    applied_at: str | None = None
    result: str | None = None


class Finding(BaseModel):
    finding_id: str = Field(default_factory=lambda: new_id("fnd"))
    title: str
    severity: Severity
    source: Literal["chip", "chuck", "both"] = "both"
    evidence: str = Field(description="The specific numbers or log lines that back this up")
    impact: str = Field(description="What happens if it's ignored")
    needs_fix: bool = False
    trend: str = Field(default="", description="New, recurring, worsening, or improving, with reference to history")
    first_seen: str | None = None
    fix: FixProposal | None = None


class FindingDraft(BaseModel):
    """What the Reviewer model writes. No ids, no fix; the pipeline adds those."""

    title: str
    severity: Severity
    source: Literal["chip", "chuck", "both"] = "both"
    evidence: str = Field(description="The specific numbers or log lines that back this up")
    impact: str = Field(description="What happens if it's ignored")
    needs_fix: bool = False
    trend: str = Field(
        default="",
        max_length=90,
        description="Short tag, under 12 words: 'new', 'recurring since <date>', 'worsening: 61% -> 83%', 'improving'. Detail goes in evidence.",
    )


class ReviewerDraft(BaseModel):
    """The schema the Reviewer model must satisfy. Kept small on purpose."""

    overall_status: Status
    headline: str = Field(description="One sentence. The thing the admin needs to know first.")
    summary: str = Field(description="Two to four sentences on the state of the server today")
    metrics_note: str = Field(default="", description="One line reading of CPU, memory, disk")
    logs_note: str = Field(default="", description="One line reading of the logs")
    trend_note: str = Field(default="", description="What changed vs. the history window")
    findings: list[FindingDraft] = Field(
        default=[],
        description="One entry per issue. Must be non-empty whenever overall_status is warning or critical.",
    )
    no_issues_statement: str = Field(
        default="",
        description="Only when findings is empty: an explicit statement that no action is required",
    )


class ReviewerOutput(BaseModel):
    """Stored form of the review. Findings carry ids and fixes."""

    overall_status: Status
    headline: str = Field(description="One sentence. The thing the admin needs to know first.")
    summary: str = Field(description="Two to four sentences on the state of the server today")
    metrics_note: str = Field(default="", description="One line reading of CPU, memory, disk")
    logs_note: str = Field(default="", description="One line reading of the logs")
    trend_note: str = Field(default="", description="What changed vs. the history window")
    findings: list[Finding] = []
    no_issues_statement: str = Field(
        default="",
        description="Set only when findings is empty: an explicit statement that no action is required",
    )

    @field_validator("findings")
    @classmethod
    def cap_findings(cls, v: list[Finding]) -> list[Finding]:
        # One page. Six findings max, ordered by severity.
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        return sorted(v, key=lambda f: order[f.severity])[:6]

    @classmethod
    def from_draft(cls, d: ReviewerDraft) -> "ReviewerOutput":
        return cls(
            overall_status=d.overall_status,
            headline=d.headline,
            summary=d.summary,
            metrics_note=d.metrics_note,
            logs_note=d.logs_note,
            trend_note=d.trend_note,
            findings=[Finding(**f.model_dump()) for f in d.findings],
            no_issues_statement=d.no_issues_statement,
        )


# ---------- Stored records ----------


class Report(BaseModel):
    report_id: str = Field(default_factory=lambda: new_id("rpt"))
    created_at: str = Field(default_factory=now_iso)
    server: str = "default"
    chip: ChipCheck
    chuck: ChuckCheck | None = None
    review: ReviewerOutput
    reviewer_model: str = ""
    html_path: str | None = None
    inputs: dict[str, str] = {}  # where chip/chuck came from: mongo:<collection>/<id> or file:<path>

    @property
    def open_fixes(self) -> list[FixProposal]:
        return [f.fix for f in self.review.findings if f.fix and f.fix.state == "proposed"]


class FixRecord(BaseModel):
    """What goes in the DB when a fix is applied, rejected, or fails. The Reviewer reads these next run."""

    fix_id: str
    finding_id: str
    report_id: str
    title: str
    state: FixState
    attempt: int
    steps: list[FixStep]
    result: str = ""
    applied_at: str | None = None
    created_at: str = Field(default_factory=now_iso)
    proposal: dict = Field(default={}, description="Full FixProposal snapshot, so reruns can see what was rejected")

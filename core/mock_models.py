"""Stand-in models for FOREMAN_MOCK=1. They read the same prompt the real model would and
return threshold-driven JSON, so the whole pipeline (agents, validators, store, report, API,
UI) can be exercised with no API key. Not clever on purpose."""

from __future__ import annotations

import json
import re

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


def _system_text(messages: list[ModelMessage]) -> str:
    out = []
    for m in messages:
        if getattr(m, "instructions", None):
            out.append(str(m.instructions))
        for p in getattr(m, "parts", []):
            if p.part_kind in ("system-prompt", "user-prompt"):
                out.append(str(p.content))
    return "\n".join(out)


def _block(text: str, label: str) -> dict | list | None:
    i = text.find(label + ":")
    if i < 0:
        return None
    rest = text[i + len(label) + 1 :].lstrip()
    if not rest or rest[0] not in "{[":
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(rest)
        return obj
    except json.JSONDecodeError:
        return None


def _reviewer(text: str) -> dict:
    chip = _block(text, "CHIP (metrics)") or {}
    chuck = _block(text, "CHUCK (log digest; Chuck is a scraper, you are the one reading the logs)")
    hist = _block(text, "HISTORY (last 7 days, 0 reports)") or _block_hist(text)
    cpu, mem, disk = chip.get("cpu_percent", 0), chip.get("memory_percent", 0), chip.get("disk_percent", 0)
    findings = []
    for name, val in (("Disk", disk), ("Memory", mem), ("CPU", cpu)):
        if val > 75:
            sev = "critical" if val > 90 else "high"
            findings.append(
                {
                    "title": f"{name} usage at {val}%",
                    "severity": sev,
                    "source": "chip",
                    "evidence": f"{name.lower()}_percent = {val} (warning 75, critical 90)",
                    "impact": f"{name} will hit the ceiling and the service will degrade or stop writing." if name != "CPU" else "Request latency climbs and the service queue backs up.",
                    "needs_fix": True,
                    "trend": "new" if not (hist or {}).get("report_count") else "recurring",
                }
            )
    if chuck:
        for ev in chuck.get("error_groups", []):
            if ev.get("level") in ("CRITICAL", "FATAL", "ERROR") and (ev.get("count", 1) >= 5 or ev["level"] != "ERROR"):
                findings.append(
                    {
                        "title": f"{(ev.get('files') or ['log'])[0]}: repeated {ev['level'].lower()} ({ev.get('count')}x)",
                        "severity": "high" if ev["level"] == "ERROR" else "critical",
                        "source": "chuck",
                        "evidence": ev.get("example", "")[:200],
                        "impact": "Errors at this rate mean a failing dependency; the agent app is returning bad responses.",
                        "needs_fix": True,
                        "trend": "new",
                    }
                )
    worst = max(cpu, mem, disk)
    status = "critical" if worst > 90 else "warning" if worst > 75 else "healthy"
    if findings and status == "healthy":
        status = "warning"
    return {
        "overall_status": status,
        "headline": f"{len(findings)} issue(s) need attention." if findings else "Server is healthy. No action required.",
        "summary": f"CPU {cpu}%, memory {mem}%, disk {disk}%. "
        + ("Logs show repeated errors." if chuck and chuck.get("counts", {}).get("errors", 0) else "Logs are quiet.")
        + (" This is the first report in the window, so today is the baseline." if not (hist or {}).get("report_count") else ""),
        "metrics_note": f"CPU {cpu}%, memory {mem}%, disk {disk}%",
        "logs_note": (f"{chuck['counts']['total_entries']} lines, {chuck['counts']['errors']} errors, {chuck['counts']['warnings']} warnings" if chuck else "Logs were not reviewed this run."),
        "trend_note": "No prior reports in the window." if not (hist or {}).get("report_count") else f"{hist['report_count']} prior report(s) in the window.",
        "findings": findings,
        "no_issues_statement": "" if findings else "No action required. All metrics are inside thresholds and the logs show no errors above the reporting rules.",
    }


def _block_hist(text: str) -> dict | None:
    m = re.search(r"HISTORY \(last \d+ days, (\d+) reports\)", text)
    return {"report_count": int(m.group(1))} if m else None


def _fixer(text: str) -> dict:
    finding = _block(text, "FINDING") or {}
    rerun = "THIS IS A RERUN" in text
    title = finding.get("title", "").lower()
    if "disk" in title:
        steps = [
            {"description": "Show current disk usage", "command": "df -h /", "risk": "low", "reversible": True},
            {"description": "Clear the apt cache", "command": "sudo apt-get clean", "risk": "low", "reversible": True},
            {"description": "Vacuum journal logs older than 7 days", "command": "sudo journalctl --vacuum-time=7d", "risk": "low", "reversible": False},
            {"description": "Verify", "command": "df -h /", "risk": "low", "reversible": True},
        ]
        if rerun:
            steps = [
                {"description": "Show the largest directories under /var", "command": "sudo du -xh /var --max-depth=2 | sort -rh | head -15", "risk": "low", "reversible": True},
                {"description": "Prune unused docker images and build cache", "command": "docker system prune -af", "risk": "medium", "reversible": False},
                {"description": "Verify", "command": "df -h /", "risk": "low", "reversible": True},
            ]
        return {
            "title": "Free disk space by clearing caches and rotated logs" if not rerun else "Free disk space by pruning docker artifacts",
            "explanation": "The root volume is filling with package cache and old journal entries. These steps remove data that is already redundant and show you the new free space afterward." if not rerun else "Different approach: reclaim space from docker images and build cache instead of system logs. This frees more but removes any image not currently in use.",
            "steps": steps,
            "side_effects": ["No service restarts", "Journal entries older than 7 days are gone"] if not rerun else ["Unused docker images are removed; next deploy pulls them again", "No service restarts"],
            "rollback": "Nothing to roll back; the removed data is regenerated on demand." if not rerun else "Re-pull removed images with docker pull.",
            "risk": "low" if not rerun else "medium",
            "conflicts_with_previous": "" if not rerun else "Introduces one new risk: a cold next deploy while images re-download. No data loss, no downtime.",
        }
    if "memory" in title:
        return {
            "title": "Restart the process holding memory",
            "explanation": "Memory has climbed without coming back down, which points at a leak in the agent app. A restart returns the memory and the health endpoint confirms it's back.",
            "steps": [
                {"description": "Show top memory consumers", "command": "ps aux --sort=-%mem | head -5", "risk": "low", "reversible": True},
                {"description": "Restart the agent service", "command": "sudo systemctl restart agent-app", "risk": "medium", "reversible": True},
                {"description": "Verify", "command": "free -m && systemctl is-active agent-app", "risk": "low", "reversible": True},
            ],
            "side_effects": ["agent-app is down for roughly 5 seconds during restart"],
            "rollback": "The service restarts itself; nothing to undo.",
            "risk": "medium",
            "conflicts_with_previous": "" if not rerun else "No new issues versus the previous proposal.",
        }
    return {
        "title": "Restart the failing service",
        "explanation": "The log errors come from one source. Restarting it clears the stuck state; the verify step confirms it is active again.",
        "steps": [
            {"description": "Check the service status", "command": "systemctl status agent-app --no-pager", "risk": "low", "reversible": True},
            {"description": "Restart it", "command": "sudo systemctl restart agent-app", "risk": "medium", "reversible": True},
            {"description": "Verify", "command": "systemctl is-active agent-app && curl -sf http://localhost:8080/health", "risk": "low", "reversible": True},
        ],
        "side_effects": ["agent-app is down for roughly 5 seconds"],
        "rollback": "Nothing to undo; the service is simply restarted.",
        "risk": "medium",
        "conflicts_with_previous": "" if not rerun else "No new issues versus the previous proposal.",
    }


def mock_model(model_id: str) -> FunctionModel:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        text = _system_text(messages)
        payload = _fixer(text) if "AGENT fixer" in text else _reviewer(text)
        return ModelResponse(parts=[TextPart(content=json.dumps(payload))])

    return FunctionModel(fn, model_name=f"mock:{model_id}")

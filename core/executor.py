"""Runs an approved fix. Two modes:

  shell  run each step's command with bash on this host (the sidecar deploy case)
  muse   hand the whole fix to Muse Code via `muse exec --trust-workspace`, same pattern as chip.py

Nothing here runs until apply_fix() is called, which only the API's Apply button calls.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from config.settings import settings
from core.schemas import FixProposal


def _log(line: str) -> None:
    settings.logs_dir.mkdir(exist_ok=True)
    with open(settings.logs_dir / "fixes.log", "a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} {line}\n")


def _shell(fix: FixProposal) -> tuple[bool, str]:
    out: list[str] = []
    for i, step in enumerate(fix.steps, 1):
        if step.command.strip() == "echo MANUAL":
            out.append(f"[{i}] MANUAL: {step.description}")
            continue
        out.append(f"[{i}] $ {step.command}")
        try:
            r = subprocess.run(
                ["bash", "-lc", step.command],
                capture_output=True,
                text=True,
                timeout=settings.execute_timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            out.append(f"    timed out after {settings.execute_timeout}s")
            return False, "\n".join(out)
        if r.stdout.strip():
            out.append("    " + r.stdout.strip().replace("\n", "\n    "))
        if r.returncode != 0:
            out.append(f"    exit {r.returncode}: {r.stderr.strip()[:500]}")
            return False, "\n".join(out)
    return True, "\n".join(out)


def _muse(fix: FixProposal) -> tuple[bool, str]:
    base = ["muse"] if shutil.which("muse") else [sys.executable, "-m", "muse"]
    plan = "\n".join(f"{i}. {s.description}: `{s.command}`" for i, s in enumerate(fix.steps, 1))
    prompt = (
        f"Apply this approved server fix exactly as written, one step at a time, and report each command's output.\n"
        f"Fix: {fix.title}\n{plan}\nStop at the first failing step and say which one failed."
    )
    env = {**os.environ, "CI": "1", "TERM": "dumb", "NO_COLOR": "1"}
    try:
        r = subprocess.run(
            base + ["exec", "--trust-workspace", prompt],
            capture_output=True,
            text=True,
            timeout=settings.execute_timeout * len(fix.steps),
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "muse exec timed out"
    ok = r.returncode == 0 and "failed" not in r.stdout.lower()
    return ok, (r.stdout or r.stderr).strip()


def execute(fix: FixProposal) -> tuple[bool, str]:
    _log(f"APPLY {fix.fix_id} {fix.title} via {settings.executor}")
    ok, output = _muse(fix) if settings.executor == "muse" else _shell(fix)
    _log(f"RESULT {fix.fix_id} {'ok' if ok else 'FAILED'}")
    return ok, output

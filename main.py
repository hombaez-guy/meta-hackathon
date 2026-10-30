#!/usr/bin/env python3
"""Foreman entry point.

  python main.py review --chip data/inbox/chip.json --chuck data/inbox/chuck.json
  python main.py apply <fix_id>
  python main.py rerun <fix_id>
  python main.py serve            # FastAPI + dashboard on :8000
  python main.py history          # what the Reviewer will read next run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import settings  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(settings.logs_dir / "foreman.log")],
)


def run_test(args) -> None:
    """One command to prove the loop. Never edits chip.py or chuck.py; only calls them."""
    import shutil
    import subprocess

    from core.pipeline import run_review

    inbox = settings.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    samples = settings.data_dir / "samples"
    root = Path(__file__).resolve().parent

    if args.scenario == "live":
        # Never review yesterday's files by accident: clear the inbox, then run the collectors.
        for f in (settings.chip_file, settings.chuck_file):
            f.unlink(missing_ok=True)

        def run_collector(label: str, cmd: list[str], out: Path, sample: str) -> None:
            print(f"{label}")
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            tail = [ln for ln in (r.stdout + r.stderr).strip().splitlines() if ln.strip()][-3:]
            for ln in tail:
                print(f"     {ln[:140]}")
            if r.returncode != 0:
                print(f"     exit code {r.returncode}")
            if out.exists():
                print(f"     ok -> {out.name}")
            else:
                print(f"     no output file written; using sample {sample} (NOT real data)")
                shutil.copy(samples / sample, out)

        run_collector("1/3  chip.py", [sys.executable, str(root / "chip.py"), "--prompt", "quick", "--output", str(settings.chip_file), "--timeout", "45"],
                      settings.chip_file, "health_critical.json")
        run_collector("2/3  chuck.py", [sys.executable, str(root / "chuck.py"), "--path", args.log_path, "--hours", "0", "--output", str(settings.chuck_file)],
                      settings.chuck_file, "logs_errors.json")
    else:
        which = "critical" if args.scenario == "critical" else "healthy"
        shutil.copy(samples / f"health_{which}.json", settings.chip_file)
        shutil.copy(samples / ("logs_errors.json" if which == "critical" else "logs_quiet.json"), settings.chuck_file)
        print(f"1/3  copied sample health_{which}.json  -> data/inbox/health.json")
        print(f"2/3  copied sample logs_{'errors' if which == 'critical' else 'quiet'}.json -> data/inbox/logs.json")

    mode = "MOCK (no API calls)" if settings.mock else f"LIVE  reviewer={settings.reviewer_model} fixer={settings.fixer_model}"
    print(f"3/3  review  [{mode}]")
    if not settings.mock and not settings.model_api_key:
        print("     MODEL_API_KEY is empty. Put it in .env, or run with FOREMAN_MOCK=1.")
        sys.exit(1)
    import platform

    host = platform.node().split(".")[0] or "local"
    if args.scenario == "live":
        # chip.py/chuck.py may have stored to Mongo as well as the files; honor --source, default auto
        rep = asyncio.run(run_review(server=host, source=args.source or "auto"))
    else:
        rep = asyncio.run(run_review(settings.chip_file, settings.chuck_file, server=host))
    print(f"\n{rep.review.overall_status.upper()}: {rep.review.headline}")
    for f in rep.review.findings:
        print(f"  [{f.severity}] {f.title}" + (f"  -> fix {f.fix.fix_id} ({f.fix.title})" if f.fix else ""))
    if not rep.review.findings:
        print(f"  {rep.review.no_issues_statement}")
    print(f"\nreport: {rep.html_path}")
    print("next:   python main.py serve   then open http://localhost:8000")


def run_probe(args) -> None:
    """Ask the Reviewer the same question two ways and show the raw JSON each time.

    native   = constrained decoding (response_format json_schema), what the pipeline uses today
    prompted = the model writes JSON as text, we validate it with Pydantic
    No guardrails, no retries, no Fixer. If native comes back with empty findings and prompted
    doesn't, the fix is to switch the Reviewer to PromptedOutput.
    """
    from pydantic_ai import Agent, NativeOutput, PromptedOutput
    from pydantic_ai.messages import ModelResponse, TextPart

    from core.agents import REVIEWER_PROMPT, ReviewDeps, build_model, reviewer_context
    from core.schemas import ReviewerDraft
    from core.store import Store

    from core.inputs import load_inputs

    chip, chuck, prov = load_inputs(args.chip, args.chuck, args.source)
    print(f"inputs: {prov}")
    deps = ReviewDeps(chip=chip, chuck=chuck, history=Store().history(), server="probe")
    effort = args.effort or settings.reviewer_reasoning_effort
    print(f"model={settings.reviewer_model} effort={effort} mock={settings.mock} timeout={args.timeout}s per call\n")

    modes = {"native": NativeOutput(ReviewerDraft, name="daily_report"), "prompted": PromptedOutput(ReviewerDraft, name="daily_report")}
    if args.mode != "both":
        modes = {args.mode: modes[args.mode]}

    out_dir = settings.logs_dir
    for name, output_type in modes.items():
        agent = Agent(build_model(settings.reviewer_model, effort), deps_type=ReviewDeps, output_type=output_type, instructions=REVIEWER_PROMPT, retries=0)
        agent.instructions(reviewer_context)
        print(f"=== {name} ===")
        import time
        t0 = time.time()

        async def go():
            return await asyncio.wait_for(agent.run("Write today's report.", deps=deps), timeout=args.timeout)

        try:
            run = asyncio.run(go())
        except asyncio.TimeoutError:
            print(f"TIMED OUT after {args.timeout}s\n")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"FAILED: {type(e).__name__}: {str(e)[:600]}\n")
            (out_dir / f"probe_{name}_error.txt").write_text(str(e))
            continue
        raws = [p.content for m in run.all_messages() if isinstance(m, ModelResponse) for p in m.parts if isinstance(p, TextPart)]
        (out_dir / f"probe_{name}.json").write_text("\n\n---\n\n".join(raws))
        print(f"{time.time() - t0:.0f}s, {len(raws)} response(s), saved to logs/probe_{name}.json")
        for r in raws:
            print(f"--- raw ({len(r)} chars) ---\n{r[:1200]}{'...' if len(r) > 1200 else ''}")
        out = run.output
        print(f"\nPARSED: {out.overall_status.upper()} | {len(out.findings)} findings | {out.headline}")
        for f in out.findings:
            print(f"  [{f.severity}] {f.title}  needs_fix={f.needs_fix}")
        print(f"usage: {run.usage}\n")


def main() -> None:
    p = argparse.ArgumentParser(prog="foreman")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("review", help="Run the Reviewer (and The Fixer) on today's check files")
    r.add_argument("--chip", default=None, help="explicit Chip file (forces file mode)")
    r.add_argument("--chuck", default=None, help="explicit Chuck file (forces file mode)")
    r.add_argument("--server", default="default")
    r.add_argument("--source", choices=["auto", "mongo", "file"], default=None, help="where to read Chip/Chuck from (default: FOREMAN_INPUT or auto)")

    a = sub.add_parser("apply", help="Apply an approved fix")
    a.add_argument("fix_id")

    rr = sub.add_parser("rerun", help="Reject a fix and ask The Fixer for another")
    rr.add_argument("fix_id")

    pr = sub.add_parser("probe", help="One Reviewer call, no Fixer. Prints the raw model JSON so you can see what Muse Wonder wrote")
    pr.add_argument("--chip", default=None)
    pr.add_argument("--chuck", default=None)
    pr.add_argument("--source", choices=["auto", "mongo", "file"], default=None)
    pr.add_argument("--timeout", type=int, default=150)
    pr.add_argument("--mode", choices=["native", "prompted", "both"], default="both")
    pr.add_argument("--effort", choices=["minimal", "low", "medium", "high"], default=None, help="override REVIEWER_REASONING_EFFORT")

    sub.add_parser("serve", help="Start the API and dashboard")
    sub.add_parser("history", help="Print the history window the Reviewer reads")

    t = sub.add_parser("test", help="Tonight's test: run Chip and Chuck (or samples), then a full review")
    t.add_argument("--scenario", choices=["live", "critical", "healthy"], default="critical",
                   help="live = run chip.py and chuck.py on this machine; critical/healthy = use the sample files")
    t.add_argument("--log-path", default="./logs", help="Where chuck.py looks for logs in live mode")
    t.add_argument("--source", choices=["auto", "mongo", "file"], default=None, help="live mode: read the results from Mongo or from the files chip.py/chuck.py wrote")

    args = p.parse_args()

    if args.cmd == "review":
        from core.pipeline import run_review

        rep = asyncio.run(run_review(args.chip, args.chuck, server=args.server, source=args.source))
        print(f"\n{rep.review.overall_status.upper()}: {rep.review.headline}")
        for f in rep.review.findings:
            print(f"  [{f.severity}] {f.title}" + (f"  -> fix {f.fix.fix_id}" if f.fix else ""))
        print(f"\nreport: {rep.html_path}")

    elif args.cmd == "apply":
        from core.pipeline import apply_fix

        fix = apply_fix(args.fix_id)
        print(f"{fix.state.upper()} {fix.title}\n{fix.result}")

    elif args.cmd == "rerun":
        from core.pipeline import rerun_fix

        fix = asyncio.run(rerun_fix(args.fix_id))
        print(json.dumps(fix.model_dump(), indent=2))

    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("core.api:app", host=settings.api_host, port=settings.api_port, reload=False)

    elif args.cmd == "probe":
        run_probe(args)

    elif args.cmd == "test":
        run_test(args)

    elif args.cmd == "history":
        from core.store import Store

        print(json.dumps(Store().history(), indent=2))


if __name__ == "__main__":
    main()
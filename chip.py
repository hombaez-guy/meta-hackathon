#!/usr/bin/env python3

import sys
import subprocess
import shutil
import importlib.util
import argparse
import json
import re
import platform
import os
from datetime import datetime, timezone

from mongo_store import store_metrics, store_health_result


PACKAGE_NAME = "muse"
MODULE_NAME = "muse"

# --- 1. Pre-programmed Prompts ---

JSON_SCHEMA = """
{{
  "status": "healthy | warning | critical",
  "timestamp": "ISO 8601 timestamp string",
  "summary": "1-sentence summary string",
  "cpu_percent": 0.0,
  "memory_percent": 0.0,
  "disk_percent": 0.0,
  "issues": ["list of issue strings"],
  "recommendations": ["list of recommendation strings"]
}}
"""

PREPROGRAMMED_PROMPTS = {
    "system_health": f"""
        You are a system health checker. Analyze the system metrics below.

        RULES:
        1. Return ONLY a valid JSON object. No markdown, no ```json fences, no explanation.
        2. Use exactly this schema: {JSON_SCHEMA}
        3. Set status to "critical" if any metric > 90%, "warning" if > 75%, otherwise "healthy".

        SYSTEM METRICS:
        {{metrics}}
""",

    "quick": f"""
Check this system health data and return ONLY a valid JSON object matching this schema: {JSON_SCHEMA}
No other text. SYSTEM METRICS: {{metrics}}
""",

    "detailed": f"""
You are a senior SRE. Do a detailed analysis of CPU, memory, disk, and processes from the metrics below.
Return ONLY a valid JSON object. No markdown. Use this schema: {JSON_SCHEMA}
Add any failing/high-usage items to "issues" and fixes to "recommendations".
SYSTEM METRICS: {{metrics}}
""",

    "disk": f"""
Analyze only disk/storage health from the metrics below.
Return ONLY a valid JSON object. No markdown. Use this schema: {JSON_SCHEMA}
SYSTEM METRICS: {{metrics}}
""",
}

def is_installed(module):
    return shutil.which(module) is not None or importlib.util.find_spec(module) is not None

def install_package(package):
    print(f"Installing {package}...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", package])
        print(f"{package} installed successfully!")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install {package}: {e}")
        sys.exit(1)


def collect_system_health():
    metrics = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "system": platform.system(),
        "node": platform.node(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
    }

    try:
        import psutil
        metrics.update({
            "cpu_percent": psutil.cpu_percent(interval=1),
            "cpu_count": psutil.cpu_count(),
            "memory_percent": psutil.virtual_memory().percent,
            "memory_used_gb": round(psutil.virtual_memory().used / (1024**3), 2),
            "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
            "disk_percent": psutil.disk_usage("/").percent,
            "disk_used_gb": round(psutil.disk_usage("/").used / (1024**3), 2),
            "disk_total_gb": round(psutil.disk_usage("/").total / (1024**3), 2),
            "boot_time": datetime.fromtimestamp(psutil.boot_time(), timezone.utc).isoformat(),
            "top_processes": [
                {"pid": p.info["pid"], "name": p.info["name"], "cpu": p.info["cpu_percent"], "memory": p.info["memory_percent"]}
                for p in sorted(psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]), key=lambda x: x.info["memory_percent"] or 0, reverse=True)[:5]
            ]
        })
    except ImportError:
        # Fallback without psutil
        total, used, free = shutil.disk_usage("/")
        metrics.update({
            "disk_percent": round((used / total) * 100, 2),
            "disk_used_gb": round(used / (1024**3), 2),
            "disk_total_gb": round(total / (1024**3), 2),
            "load_average": os.getloadavg() if hasattr(os, "getloadavg") else None,
        })
    return metrics


def fallback_health_json(metrics):
    """Generate valid JSON locally if muse fails"""
    cpu = metrics.get("cpu_percent", 0) or 0
    mem = metrics.get("memory_percent", 0) or 0
    disk = metrics.get("disk_percent", 0) or 0
    
    highest = max(cpu, mem, disk)
    status = "critical" if highest > 90 else "warning" if highest > 75 else "healthy"
    
    issues = []
    recommendations = []
    if cpu > 75: issues.append(f"High CPU: {cpu}%"); recommendations.append("Check top CPU processes")
    if mem > 75: issues.append(f"High Memory: {mem}%"); recommendations.append("Check top memory processes")
    if disk > 75: issues.append(f"High Disk: {disk}%"); recommendations.append("Clean up disk space")
    if not issues: issues, recommendations = [], ["No action needed"]
    
    return {
        "status": status,
        "timestamp": metrics.get("timestamp"),
        "summary": f"Local check: CPU {cpu}%, Memory {mem}%, Disk {disk}%",
        "cpu_percent": cpu,
        "memory_percent": mem,
        "disk_percent": disk,
        "issues": issues,
        "recommendations": recommendations,
        "_source": "local_fallback - muse did not return JSON"
    }

def run_muse(prompt, debug=False, trust_workspace=True, timeout=60):
    base = [MODULE_NAME] if shutil.which(MODULE_NAME) else [sys.executable, "-m", MODULE_NAME]
    
    # Correct order for your build: muse exec --trust-workspace "prompt"
    command = base + ["exec"]
    if trust_workspace:
        command.append("--trust-workspace")
    command.append(prompt)

    if debug:
        print(f"\n[DEBUG] Running: muse exec --trust-workspace <prompt {len(prompt)} chars>")
        print(f"[DEBUG] Timeout: {timeout}s")

    env = os.environ.copy()
    env.update({"CI": "1", "TERM": "dumb", "NO_COLOR": "1"})

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=env
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"muse timed out after {timeout}s") from e

    if debug:
        print(f"[DEBUG] Return code: {result.returncode}")
        print(f"[DEBUG] STDOUT:\n{result.stdout}\n")
        print(f"[DEBUG] STDERR:\n{result.stderr}\n")

    if not result.stdout.strip():
        raise RuntimeError(f"muse returned no output. STDERR: {result.stderr.strip()}")
    
    return result.stdout.strip()

def extract_json(text):
    if not text:
        raise ValueError("muse returned empty output")
    # Remove markdown fences
    cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
    # Find first { to last }
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in muse output")
    return json.loads(cleaned[start:end+1])

def main():
    parser = argparse.ArgumentParser(description="Install and run muse for system health check")
    parser.add_argument("--prompt", choices=PREPROGRAMMED_PROMPTS.keys(), default="system_health", help="Pre-programmed prompt to use")
    parser.add_argument("--output", default="health.json", help="File to save JSON result to")
    parser.add_argument("--install-only", action="store_true", help="Only install, don't run")
    parser.add_argument("--debug", default=False, action="store_true")
    parser.add_argument("--no-trust-workspace", action="store_false", dest="trust_workspace", help="Disable --trust-workspace flag")
    parser.set_defaults(trust_workspace=True)
    parser.add_argument("--timeout", type=int, default=60, help="Seconds to wait for muse")

    args = parser.parse_args()

    # Install dependencies if needed
    if not is_installed(MODULE_NAME):
        install_package(PACKAGE_NAME)
    else:
        print(f"{PACKAGE_NAME} is already installed.")

    if not is_installed("psutil"):
        install_package("psutil")

    if not is_installed("pymongo"):
            install_package("pymongo")

    if args.install_only:
        return

    # Collect + Build Prompt
    print("Collecting system health...")
    metrics = collect_system_health()

    # Store metrics in DB
    store_metrics(metrics)
    
    prompt = PREPROGRAMMED_PROMPTS[args.prompt].format(metrics=json.dumps(metrics, indent=2))

    # Run Muse
    print(f"Running muse with prompt: '{args.prompt}'...")
    try:
        raw_output = run_muse(prompt, debug=args.debug, trust_workspace=args.trust_workspace, timeout=args.timeout)
        health_json = extract_json(raw_output)
        health_json["_source"] = "muse"
        store_health_result(health_json, metrics=metrics)
    except Exception as e:
        print(f"Warning: {e}")
        print("Using local fallback instead - you will still get valid JSON.")
        health_json = fallback_health_json(metrics)
        store_health_result(health_json, metrics=metrics)



    # Output
    print(json.dumps(health_json, indent=2))
    with open(args.output, "w") as f:
        json.dump(health_json, f, indent=2)
    print(f"\nSaved to {args.output}")

if __name__ == "__main__":
    main()
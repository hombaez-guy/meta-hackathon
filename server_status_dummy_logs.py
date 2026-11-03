#!/usr/bin/env python3
"""Generate synthetic server and application logs for status-tracker testing.

Examples:
    python server_status_dummy_logs.py
    python server_status_dummy_logs.py --count 500 --output ./logs/test.log --seed 7
    python server_status_dummy_logs.py --format text --count 100 --interval 5
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HOSTS = ("web-01", "web-02", "api-01")
SERVICES = ("api", "nginx", "postgres", "worker")


def make_record(
    rng: random.Random,
    timestamp: datetime,
    host: str,
    outage: bool,
) -> dict[str, Any]:
    """Create one synthetic log event with fields useful to status trackers."""
    kind = rng.choices(
        ("health_check", "resource_snapshot", "http_request", "service_event"),
        weights=(42, 25, 25, 8),
        k=1,
    )[0]

    if outage:
        status = rng.choices(("critical", "warning"), weights=(75, 25), k=1)[0]
    else:
        status = rng.choices(("ok", "warning", "critical"), weights=(91, 8, 1), k=1)[0]

    record: dict[str, Any] = {
        "timestamp": timestamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "host": host,
        "service": rng.choice(SERVICES),
        "event": kind,
        "status": status,
        "message": "",
    }

    if kind == "health_check":
        record["check"] = rng.choice(("/health", "/ready", "database_connection"))
        record["response_ms"] = rng.randint(800, 5000) if status == "critical" else rng.randint(12, 450)
        record["message"] = {
            "ok": "Health check passed",
            "warning": "Health check slow; response time above target",
            "critical": "Health check failed; service did not respond",
        }[status]
    elif kind == "resource_snapshot":
        record["cpu_percent"] = round(rng.uniform(86, 99.8) if status == "critical" else rng.uniform(8, 78), 1)
        record["memory_percent"] = round(rng.uniform(90, 99.5) if status == "critical" else rng.uniform(24, 82), 1)
        record["disk_percent"] = round(rng.uniform(86, 97) if status == "warning" else rng.uniform(30, 84), 1)
        record["message"] = "Resource usage sampled"
    elif kind == "http_request":
        code = 503 if status == "critical" else (429 if status == "warning" else rng.choice((200, 200, 200, 201, 204)))
        record["method"] = rng.choice(("GET", "POST"))
        record["path"] = rng.choice(("/api/status", "/api/health", "/api/jobs"))
        record["status_code"] = code
        record["response_ms"] = rng.randint(900, 4000) if status == "critical" else rng.randint(15, 700)
        record["message"] = f"HTTP request completed with status {code}"
    else:
        record["message"] = {
            "ok": "Service started successfully",
            "warning": "Connection pool nearing configured limit",
            "critical": "Connection refused while contacting upstream service",
        }[status]
        if status == "critical":
            record["error_type"] = rng.choice(("ConnectionError", "TimeoutError", "ServiceUnavailable"))

    return record


def render_text(record: dict[str, Any]) -> str:
    """Render a record as a readable, grep-friendly single-line log."""
    parts = [
        f"{record['timestamp']} level={record['status'].upper()}",
        f"host={record['host']}",
        f"service={record['service']}",
        f"event={record['event']}",
        f'message="{record["message"]}"',
    ]
    for key, value in record.items():
        if key not in {"timestamp", "status", "host", "service", "event", "message"}:
            parts.append(f"{key}={value}")
    return " ".join(parts)


def generate_logs(
    output: Path,
    count: int,
    interval: float,
    output_format: str,
    seed: int | None,
    start_time: datetime,
) -> int:
    rng = random.Random(seed)
    output.parent.mkdir(parents=True, exist_ok=True)

    # Simulate occasional short incidents so dashboards can exercise alert and
    # recovery behavior. Incident lengths are measured in generated records.
    outage_remaining = 0
    written = 0
    with output.open("w", encoding="utf-8") as log_file:
        for index in range(count):
            if outage_remaining == 0 and rng.random() < 0.035:
                outage_remaining = rng.randint(3, 9)
            is_outage = outage_remaining > 0
            if is_outage:
                outage_remaining -= 1

            timestamp = start_time + timedelta(seconds=index * interval)
            record = make_record(rng, timestamp, rng.choice(HOSTS), is_outage)
            if output_format == "json":
                line = json.dumps(record, separators=(",", ":"), sort_keys=True)
            else:
                line = render_text(record)
            log_file.write(line + "\n")
            written += 1

    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dummy_server.log"), help="Output log file (default: dummy_server.log)")
    parser.add_argument("--count", type=int, default=200, help="Number of log records to generate (default: 200)")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between record timestamps (default: 10)")
    parser.add_argument("--format", choices=("json", "text"), default="json", dest="output_format", help="One JSON object per line or readable text (default: json)")
    parser.add_argument("--seed", type=int, default=None, help="Seed for repeatable output")
    parser.add_argument("--start", type=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")), default=None,
                        help="Start timestamp, e.g. 2026-10-30T12:00:00Z (default: current UTC time)")
    args = parser.parse_args()
    if args.count < 0:
        parser.error("--count must be zero or greater")
    if args.interval < 0:
        parser.error("--interval must be zero or greater")
    return args


def main() -> None:
    args = parse_args()
    start_time = args.start or datetime.now(timezone.utc)
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    else:
        start_time = start_time.astimezone(timezone.utc)
    total = generate_logs(args.output, args.count, args.interval, args.output_format, args.seed, start_time)
    print(f"Wrote {total} {args.output_format} log records to {args.output}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Finds log files, parses them, and returns a JSON object of logs.

Usage:
    python collect_logs.py
    python collect_logs.py --path /var/log --hours 24
    python collect_logs.py --path ./logs --level ERROR --output logs.json
    python collect_logs.py --keyword "failed" --max-lines 100
"""

import argparse
import gzip
import json
import os
import platform
import re
import sys
from collections import Counter, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path

from mongo_store import store_logs


# Common log locations by OS
DEFAULT_PATHS = {
    "Darwin": ["/var/log", "~/Library/Logs", "/usr/local/var/log", "./logs", "."],
    "Linux": ["/var/log", "./logs", "."],
    "Windows": [r"C:\Windows\Logs", "./logs", "."],
}

LOG_EXTENSIONS = {".log", ".txt", ".out", ".err"}
LOG_NAMES = {"syslog", "messages", "auth.log", "system.log"}

LEVELS = ["DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL", "FATAL"]

# Parses: 2026-05-13T14:30:00, 2026-05-13 14:30:00, [2026-05-13 14:30:00]
TIMESTAMP_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)
LEVEL_RE = re.compile(r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b", re.IGNORECASE)

def parse_timestamp(text):
    match = TIMESTAMP_RE.search(text)
    if not match:
        return None
    ts_str = match.group(1).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None

def parse_log_line(line, file_path, line_num):
    level_match = LEVEL_RE.search(line)
    level = level_match.group(1).upper() if level_match else "INFO"
    if level == "WARN":
        level = "WARNING"
    
    dt = parse_timestamp(line)
    return {
        "file": str(file_path),
        "line": line_num,
        "timestamp": dt.isoformat() if dt else None,
        "level": level,
        "message": line.strip()
    }

def read_tail(file_path, max_lines):
    """Reads last N lines efficiently, handles .gz files"""
    try:
        opener = gzip.open if file_path.suffix == ".gz" else open
        with opener(file_path, "rt", encoding="utf-8", errors="ignore") as f:
            # deque with maxlen automatically keeps only last N lines
            return deque(f, maxlen=max_lines)
    except (PermissionError, OSError, UnicodeDecodeError):
        return []

def find_log_files(paths, max_files=50):
    files = []
    for p in paths:
        path = Path(p).expanduser()
        if not path.exists():
            continue
        if path.is_file():
            files.append(path)
            continue
        
        # rglob for nested logs, but don't go too deep in "." 
        pattern = "**/*" if str(path) not in [".", "./logs"] else "*"
        for candidate in path.glob(pattern):
            try:
                if candidate.is_file() and (
                    candidate.suffix.lower() in LOG_EXTENSIONS or 
                    candidate.name.lower() in LOG_NAMES or
                    candidate.suffix == ".gz" and candidate.stem.endswith(".log")
                ):
                    files.append(candidate)
            except (PermissionError, OSError):
                continue
    
    # Sort by most recently modified first, dedupe
    unique = list(dict.fromkeys(files))
    unique.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
    return unique[:max_files]

def collect_logs(search_paths=None, hours=24, level=None, keyword=None, max_files=50, max_lines_per_file=500, include_null_timestamps=False):
    """Core function - returns a dict. Can be imported and used in other scripts."""
    if search_paths is None:
        search_paths = DEFAULT_PATHS.get(platform.system(), ["./logs", "."])
    
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours) if hours else None
    
    log_files = find_log_files(search_paths, max_files)
    all_logs = []
    files_scanned = []
    level_counter = Counter()

    for file_path in log_files:
        lines = read_tail(file_path, max_lines_per_file)
        if not lines:
            continue
            
        file_logs = []
        for i, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            if keyword and keyword.lower() not in line.lower():
                continue
            
            entry = parse_log_line(line, file_path, i)
            
            # NEW: Filter out null timestamps
            if not include_null_timestamps and entry["timestamp"] is None:
                continue

            # Filter by time
            if cutoff and entry["timestamp"]:
                try:
                    if datetime.fromisoformat(entry["timestamp"]) < cutoff:
                        continue
                except ValueError:
                    pass
            
            if level and entry["level"] != level.upper():
                continue

            file_logs.append(entry)
            level_counter[entry["level"]] += 1

        if file_logs:
            files_scanned.append({
                "path": str(file_path),
                "entries": len(file_logs),
                "modified": datetime.fromtimestamp(file_path.stat().st_mtime, timezone.utc).isoformat()
            })
            all_logs.extend(file_logs)

    # Sort and final safety filter - ensures no nulls slip through
    all_logs = [log for log in all_logs if log["timestamp"] is not None]
    all_logs.sort(key=lambda x: x["timestamp"], reverse=True)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "system": platform.system(),
        "search_paths": [str(Path(p).expanduser()) for p in search_paths],
        "hours_searched": hours,
        "filter_level": level.upper() if level else None,
        "filter_keyword": keyword,
        "summary": {
            "total_entries": len(all_logs),
            "files_with_logs": len(files_scanned),
            "files_scanned": len(log_files),
            "by_level": dict(level_counter),
            "errors": level_counter.get("ERROR", 0) + level_counter.get("CRITICAL", 0) + level_counter.get("FATAL", 0),
            "warnings": level_counter.get("WARNING", 0)
        },
        "files": files_scanned,
        "logs": all_logs
    }


def main():
    parser = argparse.ArgumentParser(description="Collect logs and return as JSON")
    parser.add_argument("--path", action="append", dest="paths", help="Path to search (can be used multiple times). Default: OS log locations")
    parser.add_argument("--hours", type=int, default=24, help="Only logs from last N hours (0 = no limit)")
    parser.add_argument("--level", choices=LEVELS, help="Only return this level, e.g. ERROR")
    parser.add_argument("--keyword", help="Only return lines containing this keyword")
    parser.add_argument("--max-files", type=int, default=50, help="Max log files to scan")
    parser.add_argument("--max-lines", type=int, default=500, help="Max lines to read per file (from end of file)")
    parser.add_argument("--output", default="logs.json", help="File to save JSON to")
    parser.add_argument("--no-save", action="store_true", help="Don't save to file, only print")
    parser.add_argument("--include-null-timestamps", action="store_true", help="Keep log lines with no parseable timestamp")
    args = parser.parse_args()

    result = collect_logs(
        search_paths=args.paths,
        hours=args.hours if args.hours > 0 else None,
        level=args.level,
        keyword=args.keyword,
        max_files=args.max_files,
        max_lines_per_file=args.max_lines,
        include_null_timestamps=args.include_null_timestamps 
    )

    # Store db
    store_logs(result)

    # Returns the JSON object to stdout
    print(json.dumps(result, indent=2))

    if not args.no_save:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nSaved {result['summary']['total_entries']} entries from {result['summary']['files_with_logs']} files to {args.output}", file=sys.stderr)

    return result

if __name__ == "__main__":
    main()
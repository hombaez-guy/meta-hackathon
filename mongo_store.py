#!/usr/bin/env python3
"""
Store system health metrics and logs in MongoDB.

Usage:
    from mongo_store import store_metrics, store_health_result, store_logs

    metrics = collect_system_health()
    store_metrics(metrics)

Set your connection string via environment variable:
    export MONGO_URI="mongodb://localhost:27017"
    # or for Atlas:
    export MONGO_URI="mongodb+srv://user:pass@cluster.mongodb.net/"
"""

import os
import copy
from datetime import datetime, timezone

try:
    from pymongo import MongoClient, ASCENDING, DESCENDING
    from pymongo.errors import PyMongoError
except ImportError:
    MongoClient = None

_client = None

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGO_DB", "health_monitor")


def get_client(uri=None):
    """Get a cached MongoDB client"""
    global _client
    if MongoClient is None:
        raise ImportError("pymongo not installed. Run: pip install pymongo")

    if _client is None:
        _client = MongoClient(uri or MONGO_URI, serverSelectionTimeoutMS=5000)
        _client.admin.command("ping")  # fail fast if unreachable
    return _client


def _sanitize(obj):
    """Make a dict safe to insert into MongoDB"""
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(i) for i in obj]
    if isinstance(obj, (datetime,)):
        return obj
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _ensure_indexes(db):
    db["system_metrics"].create_index([("stored_at", DESCENDING)])
    db["system_metrics"].create_index([("host", ASCENDING), ("timestamp", DESCENDING)])
    db["health_results"].create_index([("stored_at", DESCENDING)])
    db["health_results"].create_index([("status", ASCENDING)])
    db["logs"].create_index([("timestamp", DESCENDING)])
    db["logs"].create_index([("level", ASCENDING), ("timestamp", DESCENDING)])
    db["log_summaries"].create_index([("stored_at", DESCENDING)])


def store_metrics(metrics, collection="system_metrics", db_name=None, uri=None, host=None):
    """
    Store a single metrics dict in MongoDB.

    Args:
        metrics: dict from collect_system_health()
        collection: collection name
        db_name: defaults to MONGO_DB env / "health_monitor"
        uri: defaults to MONGO_URI env / localhost
        host: optional override for hostname field

    Returns:
        Inserted document ID as string, or None on failure
    """
    try:
        db = get_client(uri)[db_name or DB_NAME]
        _ensure_indexes(db)

        doc = copy.deepcopy(metrics)
        doc = _sanitize(doc)
        doc["stored_at"] = datetime.now(timezone.utc)
        if host:
            doc["host"] = host
        elif "node" in doc and "host" not in doc:
            doc["host"] = doc["node"]

        result = db[collection].insert_one(doc)
        print(f"Stored metrics in {db.name}.{collection} id={result.inserted_id}")
        return str(result.inserted_id)

    except PyMongoError as e:
        print(f"Warning: Failed to store metrics in MongoDB: {e}")
        return None


def store_health_result(health_json, metrics=None, uri=None, db_name=None):
    """Store the Muse / fallback health JSON, optionally with raw metrics"""
    doc = copy.deepcopy(health_json)
    if metrics:
        doc["raw_metrics"] = metrics
    return store_metrics(doc, collection="health_results", uri=uri, db_name=db_name)


def store_logs(logs_json, store_entries=True, uri=None, db_name=None):
    """
    Store collect_logs() output.
    Stores 1 summary doc in `log_summaries`, and bulk-inserts
    each log line into `logs` (if store_entries=True).
    """
    try:
        db = get_client(uri)[db_name or DB_NAME]
        _ensure_indexes(db)

        summary = {k: v for k, v in logs_json.items() if k != "logs"}
        summary = _sanitize(summary)
        summary["stored_at"] = datetime.now(timezone.utc)

        result = db["log_summaries"].insert_one(summary)
        summary_id = result.inserted_id
        print(f"Stored log summary in {db.name}.log_summaries id={summary_id}")

        entries_inserted = 0
        if store_entries and logs_json.get("logs"):
            entries = []
            for entry in logs_json["logs"]:
                e = _sanitize(copy.deepcopy(entry))
                e["summary_id"] = summary_id
                e["stored_at"] = datetime.now(timezone.utc)
                # Convert ISO timestamp string to datetime for Mongo date queries
                if isinstance(e.get("timestamp"), str):
                    try:
                        e["timestamp"] = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                    except ValueError:
                        pass
                entries.append(e)

            if entries:
                db["logs"].insert_many(entries, ordered=False)
                entries_inserted = len(entries)
                print(f"Stored {entries_inserted} log entries in {db.name}.logs")

        return {"summary_id": str(summary_id), "entries_inserted": entries_inserted}

    except PyMongoError as e:
        print(f"Warning: Failed to store logs in MongoDB: {e}")
        return None


# --- Optional: query helpers ---

def get_latest_metrics(limit=10, collection="system_metrics", uri=None, db_name=None):
    db = get_client(uri)[db_name or DB_NAME]
    return list(db[collection].find().sort("stored_at", DESCENDING).limit(limit))


def get_latest_health(uri=None, db_name=None):
    results = get_latest_metrics(limit=1, collection="health_results", uri=uri, db_name=db_name)
    return results[0] if results else None


def close_client():
    global _client
    if _client:
        _client.close()
        _client = None
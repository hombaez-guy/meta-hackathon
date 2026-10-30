"""One place to get a verified Mongo handle.

The mongo_store.get_client() caches the client before its ping, so after one failed
ping every later call returns the dead client without raising. We ping ourselves, every time,
so callers either get a live database or an exception they can fall back from.
"""

from __future__ import annotations

from config.settings import settings


def get_db():
    """Return the shared database handle, or raise if Mongo is not reachable."""
    if not settings.mongo_uri:
        raise ConnectionError("MONGO_URI is empty")
    import mongo_store  # repo root

    client = mongo_store.get_client(settings.mongo_uri)
    client.admin.command("ping")  # cheap, and the only honest liveness check
    return client[settings.mongo_db or mongo_store.DB_NAME]

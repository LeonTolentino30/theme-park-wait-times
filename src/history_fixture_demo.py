"""Opt-in local fixture demo. Does not read collector SQLite or contact a provider."""
from app import create_app
from .history_snapshots import fixture_store

if __name__ == "__main__":
    create_app({"PUBLIC_DEPLOYMENT": True, "HISTORY_SNAPSHOT_STORE": fixture_store()}).run(
        host="127.0.0.1", port=5001, debug=False, use_reloader=False)

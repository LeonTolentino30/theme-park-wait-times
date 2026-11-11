"""Explicit loopback-only private snapshot preview. Never opens SQLite or uploads."""
from app import create_app
from .history_real_snapshot import PrivateLocalStore, ROOT, BUNDLE_NAME

if __name__ == "__main__":
    store = PrivateLocalStore(ROOT/"private/themeparks/snapshots"/BUNDLE_NAME)
    create_app({"HISTORY_PRIVATE_LOCAL": True, "HISTORY_SNAPSHOT_STORE": store}).run(
        host="127.0.0.1", port=5002, debug=False, use_reloader=False)

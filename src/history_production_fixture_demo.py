"""Offline production-path fixture demonstration. No credentials, network, real data or SQLite."""
from app import create_app
from . import history_snapshots as snapshots
from .history_r2 import ObjectResult, ValidatedSnapshotCache


class FixtureObjectStore:
    def __init__(self):
        self._store = snapshots.fixture_store()
    def fetch(self, key, *, etag=None):
        if key == "manifest.json" and etag == '"fixture-manifest"':
            return ObjectResult(304, b"", etag)
        return ObjectResult(200, self._store.get(key), '"fixture-manifest"' if key == "manifest.json" else None)
    def get(self, key):
        return self.fetch(key).body


def demo_app():
    provider = ValidatedSnapshotCache(FixtureObjectStore(), allow_fixtures=True)
    return create_app({"PUBLIC_DEPLOYMENT": True, "HISTORY_PRODUCTION_ENABLED": True,
                       "HISTORY_PRODUCTION_FIXTURE_DEMO": True, "HISTORY_SNAPSHOT_PROVIDER": provider})


if __name__ == "__main__":
    demo_app().run(host="127.0.0.1", port=5003, debug=False, use_reloader=False)

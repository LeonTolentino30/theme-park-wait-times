"""Disabled-by-default production configuration. Constructing it performs no network I/O."""
import os
from .history_r2 import R2Config, R2ReadOnlyStore, ValidatedSnapshotCache


def production_config(env=None):
    env = os.environ if env is None else env
    enabled = env.get("QUEUE_ATLAS_HISTORY_ENABLED", "0")
    if enabled not in {"0", "1"}:
        raise ValueError("History activation must explicitly be 0 or 1")
    config = {"PUBLIC_DEPLOYMENT": True}
    if enabled == "1":
        if any(name in env for name in ("QUEUE_ATLAS_R2_PUBLISH_ACCESS_KEY_ID", "QUEUE_ATLAS_R2_PUBLISH_SECRET_ACCESS_KEY")):
            raise ValueError("Production history must not receive publisher credentials")
        store = R2ReadOnlyStore(R2Config.from_env(env))
        config.update(HISTORY_PRODUCTION_ENABLED=True, HISTORY_SNAPSHOT_PROVIDER=ValidatedSnapshotCache(store))
    return config

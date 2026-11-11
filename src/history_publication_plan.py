"""Local dry-run planning only. No transport, credential loading, upload or SQLite access."""
import hashlib
from . import history_snapshots as snapshots

EXPECTED_SHA256 = "931fc9fffabdae743eb10871e8e75babea4b023a7859d4a4f311f78c26c29de7"


def plan_from_store(store, *, expected_sha256=EXPECTED_SHA256):
    data = snapshots.load(store, allow_real=True)
    if data["schema_version"] != 2 or data["dataset_kind"] != "collected_observations":
        raise snapshots.SnapshotError("Only a validated v2 collected snapshot can be planned")
    manifest_raw = store.get("manifest.json")
    manifest = snapshots.decode(manifest_raw)
    raw = store.get(manifest["key"])
    if hashlib.sha256(raw).hexdigest() != expected_sha256 or manifest["sha256"] != expected_sha256:
        raise snapshots.SnapshotError("Snapshot does not match the approved digest")
    return {
        "mode": "DRY RUN - NO UPLOAD OR ACTIVATION",
        "bucket": "queue-atlas-history",
        "snapshot_key": manifest["key"], "manifest_key": "manifest.json",
        "snapshot_sha256": expected_sha256, "snapshot_bytes": len(raw),
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(), "manifest_bytes": len(manifest_raw),
        "observation_count": len(data["records"]),
        "coverage_start": data["coverage_start"], "coverage_end": data["coverage_end"],
        "snapshot_generated_at": data["generated_at"],
        "ready_for_upload": False,
        "blockers": ["Authenticated r2.dev/custom-domain configuration checks remain unverified",
                     "Real-observation upload is not authorized", "Public activation/publication/deployment are not authorized"],
        "publication_order": [
            "After approval, read and preserve any existing manifest and its ETag; do not assume this is the first manifest",
            "Create the immutable snapshot object with If-None-Match: *; never overwrite different content",
            "Read back through the read-only identity and verify exact SHA-256 and byte length before manifest changes",
            "Publish manifest.json last: If-None-Match: * if absent, otherwise If-Match: the preflight ETag",
            "Read back and validate manifest plus object with the read-only identity",
            "Only after separate Git/Render approval enable the history flag; verify all 21 archived chart combinations"],
        "rollback": [
            "Keep the previous manifest bytes, ETag and referenced object before switching",
            "For a display/privacy incident, disable QUEUE_ATLAS_HISTORY_ENABLED and restart only Flask/Gunicorn after approval",
            "Restore the approved previous manifest conditionally against the current manifest ETag; never replace immutable data objects",
            "Restart only website workers to clear validated caches: older generation timestamps are rejected by an already-running newer cache",
            "For first publication without a prior manifest, disable history; do not invent a predecessor or delete real objects without approval"],
        "render_environment_names": ["QUEUE_ATLAS_HISTORY_ENABLED", "QUEUE_ATLAS_R2_ACCOUNT_ID", "QUEUE_ATLAS_R2_BUCKET",
                                     "QUEUE_ATLAS_R2_READ_ACCESS_KEY_ID", "QUEUE_ATLAS_R2_READ_SECRET_ACCESS_KEY"],
        "render_must_not_receive": ["publisher credentials", "local SQLite files", "private snapshot files", "raw provider responses"],
        "raw_observation_json": "remains disabled",
    }

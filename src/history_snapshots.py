"""Strict snapshots; collected data requires explicit local-only opt-in. No SQLite/network access."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Protocol
from uuid import UUID
from .park_map import map_catalog
from .themeparks_provider import PARK_IDS

VERSION = 2
SUPPORTED_VERSIONS = {1, 2}
MAX_BYTES = 8_000_000
MAX_RECORDS = 20_000
GAP_SECONDS = 450
STATUSES = {"OPERATING", "CLOSED", "DOWN", "REFURBISHMENT", "UNKNOWN", "UNAVAILABLE"}
FLAGS = {"missing_entity", "missing_wait", "unknown_status", "stale_provider",
         "non_operating_wait", "missing_provider_timestamp", "future_provider_timestamp"}


class SnapshotError(ValueError):
    """Caller must use a generic message; never include storage internals."""


class SnapshotInputError(ValueError):
    pass


class SnapshotStore(Protocol):
    """Future adapters must bound reads, authenticate privately, and use fixed keys."""
    def get(self, key: str) -> bytes: ...


class MemorySnapshotStore:
    """Read-only in-memory adapter with no filesystem/network fallback."""
    def __init__(self, objects):
        self._objects = MappingProxyType(dict(objects))

    def get(self, key):
        return self._objects[key]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError("Duplicate field")
        result[key] = value
    return result


def decode(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise SnapshotError("Invalid snapshot size")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(SnapshotError("Nonfinite number")))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise SnapshotError("Invalid snapshot encoding") from error


def utc(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value):
        raise SnapshotError("Expected UTC timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SnapshotError("Invalid timestamp") from error


def public_registry():
    from .current_waits import PARK_NAMES
    park_ids = {name: ident for ident, name in PARK_NAMES.items()}
    return {f["id"]: {"name": f["properties"]["name"], "park": f["properties"]["park_name"], "park_id": park_ids[f["properties"]["park_name"]]}
            for f in map_catalog()["features"]}


def validate(data, registry=None, *, allow_real=False):
    registry = public_registry() if registry is None else registry
    if not isinstance(data, dict) or set(data) != {"schema_version", "dataset_kind", "source", "generated_at", "coverage_start", "coverage_end", "records"}:
        raise SnapshotError("Invalid snapshot fields")
    if type(data["schema_version"]) is not int or data["schema_version"] not in SUPPORTED_VERSIONS:
        raise SnapshotError("Unsupported schema version")
    kinds = {"synthetic_test_fixture"} | ({"collected_observations"} if allow_real else set())
    if data["dataset_kind"] not in kinds or data["source"] != "ThemeParks.wiki":
        raise SnapshotError("Dataset kind is not enabled")
    if data["dataset_kind"] == "collected_observations" and data["schema_version"] != 2:
        raise SnapshotError("Collected snapshots require v2 park mappings")
    start, end, generated = (utc(data[k]) for k in ("coverage_start", "coverage_end", "generated_at"))
    if not start <= end <= generated or end-start > timedelta(days=7):
        raise SnapshotError("Invalid coverage")
    records = data["records"]
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise SnapshotError("Invalid record count")
    seen = set()
    fields = {"attraction_id", "observed_at", "provider_last_changed_at", "status", "wait_minutes", "present", "quality_flags"}
    if data["schema_version"] == 2:
        fields.add("park_id")
    for row in records:
        if not isinstance(row, dict) or set(row) != fields:
            raise SnapshotError("Invalid record fields")
        if not isinstance(row["attraction_id"], str) or row["attraction_id"] not in registry:
            raise SnapshotError("Unsupported attraction")
        try:
            if str(UUID(row["attraction_id"])) != row["attraction_id"]:
                raise ValueError()
        except ValueError as error:
            raise SnapshotError("Expected canonical attraction UUID") from error
        if data["schema_version"] == 2 and (not isinstance(row["park_id"], str) or row["park_id"] not in PARK_IDS or row["park_id"] != registry[row["attraction_id"]].get("park_id")):
            raise SnapshotError("Attraction/park mismatch")
        when = utc(row["observed_at"])
        if not start <= when <= end:
            raise SnapshotError("Observation outside coverage")
        changed = row["provider_last_changed_at"]
        if changed is not None and utc(changed) > when:
            raise SnapshotError("Future provider timestamp")
        if not isinstance(row["status"], str) or row["status"] not in STATUSES or type(row["present"]) is not bool:
            raise SnapshotError("Invalid operating state")
        wait = row["wait_minutes"]
        if wait is not None and (type(wait) is not int or not 0 <= wait <= 1440):
            raise SnapshotError("Invalid standby wait")
        flags = row["quality_flags"]
        if not isinstance(flags, list) or any(not isinstance(flag, str) or flag not in FLAGS for flag in flags) or len(set(flags)) != len(flags):
            raise SnapshotError("Invalid quality flags")
        key = (row["attraction_id"], when)
        if key in seen:
            raise SnapshotError("Duplicate observation")
        seen.add(key)
    return data


def package(data, registry=None, *, allow_real=False):
    """Content-addressed naming: changed content must receive a different key."""
    validate(data, registry, allow_real=allow_real)
    raw = canonical(data)
    if len(raw) > MAX_BYTES:
        raise SnapshotError("Oversized snapshot")
    digest = hashlib.sha256(raw).hexdigest()
    version = data["schema_version"]
    key = f"history/v{version}/{digest}.json"
    manifest = canonical({"schema_version": version, "dataset_kind": data["dataset_kind"], "key": key, "sha256": digest, "bytes": len(raw)})
    return MemorySnapshotStore({"manifest.json": manifest, key: raw})


def load(store, registry=None, *, allow_real=False):
    try:
        manifest = decode(store.get("manifest.json"))
        if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "dataset_kind", "key", "sha256", "bytes"}:
            raise SnapshotError("Invalid manifest")
        digest = manifest["sha256"]
        if type(manifest["schema_version"]) is not int or manifest["schema_version"] not in SUPPORTED_VERSIONS or manifest["dataset_kind"] not in ({"synthetic_test_fixture"} | ({"collected_observations"} if allow_real else set())):
            raise SnapshotError("Invalid manifest version")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or manifest["key"] != f"history/v{manifest['schema_version']}/{digest}.json":
            raise SnapshotError("Invalid content address")
        size = manifest["bytes"]
        if type(size) is not int or not 0 < size <= MAX_BYTES:
            raise SnapshotError("Invalid manifest size")
        raw = store.get(manifest["key"])
        if not isinstance(raw, bytes) or len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise SnapshotError("Integrity mismatch")
        data = validate(decode(raw), registry, allow_real=allow_real)
        if data["schema_version"] != manifest["schema_version"] or data["dataset_kind"] != manifest["dataset_kind"]:
            raise SnapshotError("Manifest/snapshot version mismatch")
        if canonical(data) != raw:
            raise SnapshotError("Noncanonical snapshot")
        return data
    except Exception as error:
        raise SnapshotError("Historical snapshot unavailable") from error


def fixture_store():
    # Only this reviewed fixture is read; there is no command-line database/path input.
    path = Path(__file__).resolve().parents[1] / "tests/fixtures/history_snapshot_v1.json"
    return package(decode(path.read_bytes()))


def window(params, data, *, now=None, registry=None):
    registry = public_registry() if registry is None else registry
    if set(params)-{"attraction_id", "hours", "mode"} or params.get("attraction_id") not in registry:
        raise SnapshotInputError("Choose a supported attraction.")
    if params.get("hours", "24") not in {"6", "24", "168"} or params.get("mode", "archive") not in {"archive", "rolling"}:
        raise SnapshotInputError("Choose 6, 24, or 168 hours and archive or rolling mode.")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise SnapshotError("Clock must be timezone-aware")
    if utc(data["generated_at"]) > now:
        raise SnapshotError("Snapshot generated in the future")
    mode = params.get("mode", "archive")
    end = min(utc(data["coverage_end"]), now) if mode == "archive" else now
    start = end-timedelta(hours=int(params.get("hours", "24")))
    rows = sorted((r for r in data["records"] if r["attraction_id"] == params["attraction_id"] and start <= utc(r["observed_at"]) <= end), key=lambda r: utc(r["observed_at"]))
    points, previous = [], None
    for row in rows:
        when = utc(row["observed_at"])
        numeric = row["status"] == "OPERATING" and row["present"] and row["wait_minutes"] is not None and "stale_provider" not in row["quality_flags"]
        points.append({"when": when, "wait": row["wait_minutes"] if numeric else None,
                       "event": None if numeric else ("Wait unavailable" if row["status"] == "OPERATING" else row["status"].title()),
                       "gap": previous is not None and (when-previous).total_seconds() > GAP_SECONDS})
        previous = when
    return {"start": start, "end": end, "mode": mode, "points": points,
            "name": registry[params["attraction_id"]]["name"],
            "generated": utc(data["generated_at"]), "coverage_start": utc(data["coverage_start"]), "coverage_end": utc(data["coverage_end"]),
            "stale": (now-utc(data["coverage_end"])).total_seconds() > 1800,
            "meaningful": len([p for p in points if p["wait"] is not None]) >= 2,
            "fixture": data["dataset_kind"] == "synthetic_test_fixture",
            "incomplete": not points or start < utc(data["coverage_start"]) or end > utc(data["coverage_end"]) or any(p["gap"] for p in points) or (points[0]["when"]-start).total_seconds() > GAP_SECONDS or (end-points[-1]["when"]).total_seconds() > GAP_SECONDS}

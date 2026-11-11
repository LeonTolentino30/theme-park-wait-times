"""Read-only extraction mechanics and owned synthetic fixture tools.

Real source access is separately gated in history_real_snapshot; no network client.
"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
import argparse
import json
import sqlite3
import tempfile
import time
from . import history_snapshots as snapshots
from .collection_pilot import APPROVED, connect as create_collector_fixture

DATE_FLOOR = snapshots.utc("2026-11-08T00:00:00Z")
# November 8-15 are inclusive UTC calendar dates; the upper bound is exclusive.
DATE_CEILING = snapshots.utc("2026-11-16T00:00:00Z")


class ExtractionError(ValueError):
    """Safe, generic error; never include a database path or source value."""


@dataclass(frozen=True)
class ExtractionScope:
    attraction_ids: tuple[str, ...]
    start: str
    end: str

    def __post_init__(self):
        if (not isinstance(self.attraction_ids, tuple) or len(self.attraction_ids) != 7
                or any(not isinstance(i, str) for i in self.attraction_ids)
                or frozenset(self.attraction_ids) != APPROVED):
            raise ExtractionError("Explicitly allowlist exactly the seven verified attractions")
        try:
            start, end = snapshots.utc(self.start), snapshots.utc(self.end)
        except snapshots.SnapshotError as error:
            raise ExtractionError("Invalid UTC extraction bounds") from error
        if not DATE_FLOOR <= start < end <= DATE_CEILING or end-start > timedelta(days=7):
            raise ExtractionError("Use at most seven days inside November 8-15 UTC, with an exclusive end")


def micros(value):
    """Integer arithmetic avoids SQLite julianday/float rounding at boundaries."""
    dt = snapshots.utc(value)-datetime(1970, 1, 1, tzinfo=timezone.utc)
    return dt.days*86_400_000_000 + dt.seconds*1_000_000 + dt.microseconds


class FixtureDatabase:
    """Owns only a newly created temporary collector-schema database, not a supplied path."""
    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="queue-atlas-extraction-fixture-")
        self._path = Path(self._temporary.name)/"fixture.sqlite3"
        self.writer = create_collector_fixture(self._path)
        registry = snapshots.public_registry()
        with self.writer:
            for park in sorted({r["park_id"] for r in registry.values()}):
                self.writer.execute("INSERT INTO parks(id,offset_seconds,next_due) VALUES(?,0,0)", (park,))
            for ident, meta in registry.items():
                self.writer.execute("INSERT INTO attractions VALUES(?,?,?)", (ident, meta["park_id"], meta["name"]))

    @property
    def path(self):
        return self._path

    def add(self, ident, fetched_at, *, status="OPERATING", wait=15, changed=None,
            present=1, flags=(), outcome="success", source="ThemeParks.wiki", park_id=None):
        park = park_id or snapshots.public_registry()[ident]["park_id"]
        with self.writer:
            slot = self.writer.execute("SELECT count(*) FROM runs").fetchone()[0]
            run = self.writer.execute("INSERT INTO runs(park_id,slot,attempted_at,completed_at,outcome,raw_body,headers,error) VALUES(?,?,0,0,?,?,?,?)",
                                      (park, slot, outcome, b"FIXTURE_PRIVATE_RAW_RESPONSE", '{"Authorization":"FIXTURE_SECRET"}', "FIXTURE_LOCAL_PATH")).lastrowid
            self.writer.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (run, ident, park, fetched_at, changed, status, wait, source, present, json.dumps(list(flags))))

    def close(self):
        self.writer.close()
        self._temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _read_fixture(source, scope):
    if type(source) is not FixtureDatabase or type(scope) is not ExtractionScope:
        raise ExtractionError("Only owned fixture databases and explicit scopes are enabled")
    expected = Path(source._temporary.name)/"fixture.sqlite3"
    if (source.path.is_symlink() or source.path.resolve() != expected.resolve()
            or source.path.resolve().parent != Path(source._temporary.name).resolve()):
        raise ExtractionError("Fixture source identity mismatch")
    # Existing WAL sidecars are required: never create them or ignore a live WAL.
    if not all(Path(str(source.path)+suffix).is_file() for suffix in ("-wal", "-shm")):
        raise ExtractionError("Existing fixture WAL sidecars are required")
    return _read_rows(source.path, scope)


def _read_rows(path, scope, *, immutable=False):
    """Internal allowlisted reader; real callers must enforce their own source gate."""
    started, db = time.monotonic(), None
    try:
        db = sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro"+("&immutable=1" if immutable else ""), uri=True, timeout=0.25, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA temp_store=MEMORY")
        db.create_function("utc_micros", 1, micros, deterministic=True)
        columns = {
            "observations": {"run_id", "attraction_id", "park_id", "fetched_at", "provider_last_changed_at", "status", "standby_wait_minutes", "present", "quality_flags", "source"},
            "runs": {"id", "park_id", "outcome"},
        }
        def authorize(action, arg1, arg2, *_):
            if action == sqlite3.SQLITE_READ:
                return sqlite3.SQLITE_OK if arg2 in columns.get(arg1, set()) else sqlite3.SQLITE_DENY
            if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION}:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        db.set_authorizer(authorize)
        db.set_progress_handler(lambda: int(time.monotonic()-started > 3), 1000)
        db.execute("BEGIN")
        marks = ",".join("?" for _ in scope.attraction_ids)
        rows = db.execute(f"""SELECT o.attraction_id,o.park_id,o.fetched_at,o.provider_last_changed_at,
                    o.status,o.standby_wait_minutes,o.present,o.quality_flags,r.park_id AS run_park_id
                FROM observations o JOIN runs r ON r.id=o.run_id
                WHERE o.attraction_id IN ({marks}) AND r.outcome='success' AND o.source='ThemeParks.wiki'
                AND utc_micros(o.fetched_at)>=? AND utc_micros(o.fetched_at)<?
                ORDER BY utc_micros(o.fetched_at),o.attraction_id,o.run_id LIMIT ?""",
                (*scope.attraction_ids, micros(scope.start), micros(scope.end), snapshots.MAX_RECORDS+1)).fetchall()
        if db.total_changes != 0 or len(rows) > snapshots.MAX_RECORDS:
            raise ExtractionError("Extraction limit exceeded")
        return rows
    except (sqlite3.Error, OSError, snapshots.SnapshotError) as error:
        raise ExtractionError("Fixture read-only extraction unavailable") from error
    finally:
        if db is not None:
            db.close()


def extract_fixture(source, scope, *, generated_at=None):
    """Return only invented fixture fields in memory; no file/cloud output."""
    rows = _read_fixture(source, scope) # connection closes before validation/processing
    return _build_snapshot(rows, scope, generated_at=generated_at)


def _build_snapshot(rows, scope, *, generated_at=None, allow_real=False):
    """Build after the read transaction closes; preserve original timestamp strings."""
    records = []
    for row in rows:
        if row["park_id"] != row["run_park_id"] or type(row["present"]) is not int or row["present"] not in (0, 1):
            raise ExtractionError("Fixture observation identity/state mismatch")
        try:
            flags = snapshots.decode(row["quality_flags"].encode("utf-8"))
        except (snapshots.SnapshotError, AttributeError) as error:
            raise ExtractionError("Invalid fixture quality flags") from error
        records.append({"attraction_id": row["attraction_id"], "park_id": row["park_id"],
                        "observed_at": row["fetched_at"], "provider_last_changed_at": row["provider_last_changed_at"],
                        "status": row["status"], "wait_minutes": row["standby_wait_minutes"],
                        "present": bool(row["present"]), "quality_flags": flags})
    start = min(records, key=lambda r: snapshots.utc(r["observed_at"]))["observed_at"] if records else scope.start
    end = max(records, key=lambda r: snapshots.utc(r["observed_at"]))["observed_at"] if records else scope.start
    data = {"schema_version": 2, "dataset_kind": "collected_observations" if allow_real else "synthetic_test_fixture", "source": "ThemeParks.wiki",
            "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
            "coverage_start": start, "coverage_end": end, "records": records}
    snapshots.validate(data, allow_real=allow_real)
    return data


def quality_summary(data, *, allow_real=False):
    """Aggregate fixture metadata only: no individual observation arrays."""
    snapshots.validate(data, allow_real=allow_real)
    totals = {"records": len(data["records"]), "numeric": 0, "missing_waits": 0, "genuine_zeroes": 0,
              "closed": 0, "down": 0, "gaps_over_450_seconds": 0}
    per_attraction, previous, largest = {}, {}, 0.0
    for row in sorted(data["records"], key=lambda r: snapshots.utc(r["observed_at"])):
        ident, when = row["attraction_id"], snapshots.utc(row["observed_at"])
        numeric = row["status"] == "OPERATING" and row["present"] and row["wait_minutes"] is not None and "stale_provider" not in row["quality_flags"]
        per_attraction.setdefault(ident, {"records": 0, "numeric": 0})
        per_attraction[ident]["records"] += 1
        per_attraction[ident]["numeric"] += int(numeric)
        totals["numeric"] += int(numeric)
        totals["missing_waits"] += int(row["wait_minutes"] is None)
        totals["genuine_zeroes"] += int(numeric and row["wait_minutes"] == 0)
        totals["closed"] += int(row["status"] == "CLOSED")
        totals["down"] += int(row["status"] == "DOWN")
        if ident in previous:
            interval = (when-previous[ident]).total_seconds()
            largest = max(largest, interval)
            totals["gaps_over_450_seconds"] += int(interval > snapshots.GAP_SECONDS)
        previous[ident] = when
    return {"dataset_kind": data["dataset_kind"], "schema_version": data["schema_version"],
            "coverage_start": data["coverage_start"], "coverage_end": data["coverage_end"],
            "counts": totals, "per_attraction": per_attraction, "largest_interval_seconds": largest,
            "notice": "PRIVATE LOCAL COLLECTED POSTED WAITS. Not live waits or measured queue durations." if allow_real else "INVENTED TEST FIXTURES ONLY. No real observations were read or exported."}


def write_fixture_bundle(data, destination):
    """Write-once fixture artifact directory; refuse any existing destination."""
    store = snapshots.package(data)
    manifest = snapshots.decode(store.get("manifest.json"))
    root = Path(destination)
    root.mkdir(exist_ok=False)
    target = root/manifest["key"]
    target.parent.mkdir(parents=True)
    with target.open("xb") as stream:
        stream.write(store.get(manifest["key"]))
    # Publishing the manifest last leaves interrupted partial bundles inactive.
    with (root/"manifest.json").open("xb") as stream:
        stream.write(store.get("manifest.json"))
    return manifest


def seed_demo(source):
    """Invented, fractional-second records; never queries a real data source."""
    cases = [
        ("2026-11-08T12:00:00.123456+00:00", "OPERATING", 0, 1, ()),
        ("2026-11-08T12:05:00.000001Z", "CLOSED", None, 1, ("missing_wait",)),
        ("2026-11-08T12:10:00.500000+00:00", "DOWN", None, 1, ("missing_wait",)),
        ("2026-11-08T12:15:00.999999Z", "OPERATING", 20, 1, ()),
        ("2026-11-08T12:35:00.010000Z", "UNAVAILABLE", None, 0, ("missing_entity",)),
        ("2026-11-08T12:40:00.000001+00:00", "OPERATING", None, 1, ("missing_wait",)),
        ("2026-11-08T12:45:00.000002+00:00", "OPERATING", 15, 1, ()),
    ]
    for ident in sorted(APPROVED):
        for when, status, wait, present, flags in cases:
            source.add(ident, when, status=status, wait=wait, present=present, flags=flags)


def main():
    parser = argparse.ArgumentParser(description="Synthetic extraction dry-run only; no real database option")
    parser.add_argument("--dry-run-fixtures", action="store_true", required=True)
    parser.parse_args()
    scope = ExtractionScope(tuple(sorted(APPROVED)), "2026-11-08T00:00:00Z", "2026-11-15T00:00:00Z")
    with FixtureDatabase() as source:
        seed_demo(source)
        print(json.dumps(quality_summary(extract_fixture(source, scope)), indent=2))


if __name__ == "__main__":
    main()

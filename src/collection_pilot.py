"""Finite foreground pilot. No network or database activity on import."""
import argparse
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import logging
import math
import os
import queue
from pathlib import Path
import re
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

from .themeparks_provider import (ThemeParksProvider, ProviderError, InvalidResponse,
                                 RateLimited, load_registry, utc_now, aware_utc)

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "private/themeparks/pilot"
DB = PRIVATE / "pilot.sqlite3"
INTERVAL = 300
DURATION = 7 * 24 * 3600
APPROVED = frozenset(("61079a31-4165-4fb0-b36f-c01c5971f80a", "578bbd12-1975-4ec3-9879-ea641c780342",
    "fa743143-281b-4b5b-b87b-d49fcb006772", "6be23178-7d00-4884-9e88-76787da1df86",
    "6af80308-647d-4d8b-bcf6-37517a93bdbc", "ec25d9a7-b4d4-4ebf-a6c4-c18389351764",
    "750939c5-a69e-408a-8d55-66c272fa265e"))


@contextmanager
def exclusive_lock(path):
    """Kernel-managed lock: crash releases ownership; the harmless file remains."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Another pilot process holds the lock.") from None
        yield
    finally:
        stream.close()


def connect(path):
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS pilot (
      id INTEGER PRIMARY KEY CHECK(id=1), started REAL NOT NULL, deadline REAL NOT NULL,
      last_seen REAL NOT NULL, blocked_until REAL NOT NULL DEFAULT 0, halted TEXT);
    CREATE TABLE IF NOT EXISTS parks (
      id TEXT PRIMARY KEY, offset_seconds INTEGER NOT NULL, next_due REAL NOT NULL, failures INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS attractions (
      id TEXT PRIMARY KEY, park_id TEXT NOT NULL REFERENCES parks(id), alias TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS runs (
      id INTEGER PRIMARY KEY, park_id TEXT NOT NULL REFERENCES parks(id), slot INTEGER NOT NULL,
      attempted_at REAL NOT NULL, completed_at REAL, outcome TEXT NOT NULL, error TEXT,
      http_status INTEGER, headers TEXT, raw_body BLOB, UNIQUE(park_id,slot));
    CREATE TABLE IF NOT EXISTS observations (
      run_id INTEGER NOT NULL REFERENCES runs(id), attraction_id TEXT NOT NULL REFERENCES attractions(id),
      park_id TEXT NOT NULL, fetched_at TEXT NOT NULL, provider_last_changed_at TEXT,
      status TEXT NOT NULL, standby_wait_minutes INTEGER CHECK(standby_wait_minutes>=0),
      source TEXT NOT NULL CHECK(source='ThemeParks.wiki'), present INTEGER NOT NULL,
      quality_flags TEXT NOT NULL, PRIMARY KEY(run_id,attraction_id));
    """)
    return db


def cache_delay(headers, now):
    headers = {k.lower(): v for k, v in headers.items()}
    age = 0
    try:
        age = max(0, int(headers.get("age", "0")))
    except ValueError:
        pass
    ages = re.findall(r"(?:^|,)\s*(?:s-maxage|max-age)\s*=\s*\"?(\d+)", headers.get("cache-control", ""), re.I)
    delay = max([INTERVAL] + [max(0, int(value)-age) for value in ages])
    try:
        expiry = aware_utc(parsedate_to_datetime(headers["expires"])).timestamp()
        delay = max(delay, expiry-now)
    except (KeyError, ValueError, TypeError, OverflowError):
        pass
    return delay


class RequestDeadlineExceeded(ProviderError):
    pass


class Pilot:
    def __init__(self, db, provider, registry, *, clock=utc_now, monotonic=time.monotonic):
        self.db, self.provider = db, provider
        self.registry = tuple(a for a in registry if a.collectable)
        self.clock, self.monotonic = clock, monotonic
        self.monotonic_stop = None

    def now(self):
        return aware_utc(self.clock()).timestamp()

    def initialize(self, resume=False):
        now = self.now()
        pilot = self.db.execute("SELECT * FROM pilot").fetchone()
        if pilot is not None:
            if not resume:
                raise RuntimeError("Pilot already exists; use resume without resetting its deadline.")
            if now < pilot["last_seen"]:
                raise RuntimeError("System clock moved backwards; restore UTC clock before resuming.")
            stored = {(r["id"], r["park_id"], r["alias"]) for r in self.db.execute("SELECT * FROM attractions")}
            current = {(a.attraction_id, a.park_id, a.alias) for a in self.registry}
            if stored != current:
                raise RuntimeError("Registry differs from pilot snapshot; manual review required.")
            with self.db:
                self.db.execute("UPDATE runs SET outcome='interrupted', error='Interrupted before completion' WHERE outcome='in_progress'")
        else:
            if resume:
                raise RuntimeError("No pilot exists to resume.")
            if {a.attraction_id for a in self.registry} != APPROVED or len(self.registry) != 7:
                raise RuntimeError("Pilot requires exactly the seven approved attraction UUIDs.")
            with self.db:
                self.db.execute("INSERT INTO pilot(id,started,deadline,last_seen) VALUES(1,?,?,?)", (now, now+DURATION, now))
                for index, park in enumerate(sorted({a.park_id for a in self.registry})):
                    offset = index*5
                    self.db.execute("INSERT INTO parks(id,offset_seconds,next_due) VALUES(?,?,?)", (park,offset,now+offset))
                self.db.executemany("INSERT INTO attractions VALUES(?,?,?)", [(a.attraction_id,a.park_id,a.alias) for a in self.registry])
        deadline = self.db.execute("SELECT deadline FROM pilot").fetchone()[0]
        self.monotonic_stop = self.monotonic() + max(0, deadline-now)

    def bounded_live(self, park_id, remaining):
        # A socket timeout alone does not bound a slow-drip HTTP body.
        # Only the foreground thread writes SQLite; a timed-out daemon is never retried.
        result = queue.Queue(maxsize=1)
        def request():
            try:
                result.put((True, self.provider.live(park_id, self.registry)))
            except BaseException as exc:
                result.put((False, exc))
        worker = threading.Thread(target=request, daemon=True)
        worker.start()
        try:
            ok, value = result.get(timeout=min(self.provider.timeout, remaining))
        except queue.Empty:
            raise RequestDeadlineExceeded("Provider exceeded total request budget; manual review required.") from None
        if not ok:
            raise value
        return value

    def tick(self):
        now = self.now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            pilot = self.db.execute("SELECT * FROM pilot").fetchone()
            if pilot is None:
                raise RuntimeError("Initialize the pilot first.")
            if now < pilot["last_seen"]:
                raise RuntimeError("System clock moved backwards; collection stopped.")
            self.db.execute("UPDATE pilot SET last_seen=?", (now,))
            if now >= pilot["deadline"] or (self.monotonic_stop is not None and self.monotonic() >= self.monotonic_stop):
                self.db.commit()
                return "expired"
            if pilot["halted"]:
                raise RuntimeError("Pilot halted for manual review: " + pilot["halted"])
            if now < pilot["blocked_until"]:
                self.db.commit()
                return "waiting"
            park = self.db.execute("SELECT * FROM parks WHERE next_due<=? ORDER BY next_due,id LIMIT 1", (now,)).fetchone()
            if park is None:
                self.db.commit()
                return "waiting"
            # Never start a request that could outlive the original deadline.
            remaining = min(pilot["deadline"]-now, self.monotonic_stop-self.monotonic() if self.monotonic_stop is not None else float('inf'))
            if remaining <= self.provider.timeout:
                self.db.commit()
                return "expired"
            slot = int((now-pilot["started"])/INTERVAL)
            cursor = self.db.execute("INSERT OR IGNORE INTO runs(park_id,slot,attempted_at,outcome) VALUES(?,?,?,'in_progress')", (park["id"],slot,now))
            if not cursor.rowcount:
                self.db.execute("UPDATE parks SET next_due=? WHERE id=?", (now+INTERVAL,park["id"]))
                self.db.commit()
                return "waiting"
            run_id = cursor.lastrowid
            self.db.execute("UPDATE parks SET next_due=? WHERE id=?", (now+INTERVAL,park["id"]))
            self.db.commit()  # Persist claim before the network; crashes never replay this slot.
        except BaseException:
            self.db.rollback()
            raise
        outcome, error, code, observations, delay, halt = "success", None, 200, (), INTERVAL, None
        try:
            observations = self.bounded_live(park["id"], remaining)
            expected = {a.attraction_id for a in self.registry if a.park_id == park["id"]}
            if {o.attraction_id for o in observations} != expected or len(observations) != len(expected):
                raise InvalidResponse("Normalized attraction batch differs from registry.")
        except KeyboardInterrupt:
            outcome, error = "interrupted", "Interrupted during provider request"
        except ProviderError as exc:
            outcome, error, code = "error", str(exc), exc.http_status
            delay = max(INTERVAL, min(3600, INTERVAL * 2**min(park["failures"],4)))
            if isinstance(exc, RateLimited) or exc.retry_after_seconds is not None:
                delay = max(delay, exc.retry_after_seconds or INTERVAL)
                with self.db:
                    self.db.execute("UPDATE pilot SET blocked_until=max(blocked_until,?)", (self.now()+delay,))
            if isinstance(exc, (InvalidResponse, RequestDeadlineExceeded)) or code in (301,302,307,308,400,401,403,404):
                halt = "Provider schema, identity, or permission failure"
        completed = self.now()
        headers = getattr(self.provider, "response_headers", {})
        delay = max(delay, cache_delay(headers, completed))
        with self.db:
            self.db.execute("UPDATE runs SET completed_at=?,outcome=?,error=?,http_status=?,headers=?,raw_body=? WHERE id=?",
                            (completed,outcome,error,code,json.dumps(headers),getattr(self.provider,"last_response_body",None),run_id))
            if outcome == "success":
                for o in observations:
                    self.db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,attraction_id) DO NOTHING",
                        (run_id,o.attraction_id,o.park_id,o.fetched_at.isoformat(),o.provider_last_changed_at.isoformat() if o.provider_last_changed_at else None,o.status,o.standby_wait_minutes,o.source,int(o.present),json.dumps(o.quality_flags)))
            self.db.execute("UPDATE parks SET next_due=max(next_due,?),failures=? WHERE id=?", (completed+delay,0 if outcome=="success" else park["failures"]+1,park["id"]))
            if halt:
                self.db.execute("UPDATE pilot SET halted=?", (halt,))
        logging.getLogger("queueatlas.pilot").log(logging.INFO if outcome=="success" else logging.WARNING,
            "park=%s run=%s outcome=%s rows=%s error=%s",park["id"],run_id,outcome,len(observations) if outcome=="success" else 0,error)
        if outcome == "interrupted":
            raise KeyboardInterrupt
        if halt:
            raise RuntimeError("Pilot halted for manual review: " + halt)
        return "collected"

    def run(self, stop):
        while not stop.is_set():
            if self.tick() == "expired":
                return
            deadline = self.db.execute("SELECT deadline FROM pilot").fetchone()[0]
            remaining = min(deadline-self.now(), self.monotonic_stop-self.monotonic())
            stop.wait(max(0, min(1, remaining)))  # No sleep beyond the deadline.


def report(path=DB, *, now=None):
    # Read-only inspection must never create/initialize a database or contact provider.
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        p = db.execute("SELECT * FROM pilot").fetchone()
        if p is None:
            raise RuntimeError("Pilot has not been initialized.")
        end = min(aware_utc(now or utc_now()).timestamp(),p["deadline"])
        rides=[]
        for ride in db.execute("SELECT a.*,p.offset_seconds FROM attractions a JOIN parks p ON a.park_id=p.id ORDER BY alias"):
            expected=max(0,math.ceil((end-p["started"]-ride["offset_seconds"])/INTERVAL))
            counts=db.execute("SELECT count(*) records,sum(present) present,sum(standby_wait_minutes IS NULL) null_waits,sum(status='OPERATING' AND standby_wait_minutes=0) operating_zeroes,sum(status='OPERATING' AND standby_wait_minutes IS NOT NULL) numeric_operating_waits FROM observations WHERE attraction_id=?",(ride["id"],)).fetchone()
            runs=db.execute("SELECT count(*) attempts,sum(outcome='success') successes FROM runs WHERE park_id=?",(ride["park_id"],)).fetchone()
            count=counts["records"]
            statuses=dict(db.execute("SELECT status,count(*) FROM observations WHERE attraction_id=? GROUP BY status",(ride["id"],)))
            rides.append({"alias":ride["alias"],"expected_five_minute_slots":expected,"records":count,
                "coverage_fraction":min(1,count/expected) if expected else None,"missing_records":max(0,expected-count),
                "unattempted_slots":max(0,expected-runs["attempts"]),"absent_entities":count-(counts["present"] or 0),
                "numeric_operating_waits":counts["numeric_operating_waits"] or 0,"null_waits":counts["null_waits"] or 0,"operating_zeroes":counts["operating_zeroes"] or 0,"statuses":statuses})
        outcomes=dict(db.execute("SELECT outcome,count(*) FROM runs GROUP BY outcome"))
        attempts=sum(outcomes.values())
        gaps={}
        for park in db.execute("SELECT id FROM parks"):
            times=[p["started"]]+[r[0] for r in db.execute("SELECT completed_at FROM runs WHERE park_id=? AND outcome='success' ORDER BY completed_at",(park[0],))]+[end]
            gaps[park[0]]=max([0]+[b-a for a,b in zip(times,times[1:])])
        return {"source":"ThemeParks.wiki","attribution":"Powered by ThemeParks.wiki - https://www.themeparks.wiki",
            "started_utc":datetime.fromtimestamp(p["started"],timezone.utc).isoformat(),
            "deadline_utc":datetime.fromtimestamp(p["deadline"],timezone.utc).isoformat(),
            "deadline_new_york":datetime.fromtimestamp(p["deadline"],ZoneInfo("America/New_York")).isoformat(),
            "halted":p["halted"],"attempts":attempts,"outcomes":outcomes,
            "error_rate":(attempts-outcomes.get("success",0))/attempts if attempts else None,
            "longest_success_gap_seconds":gaps,"rides":rides,
            "note":"Expected slots include pauses/backoff/outages. Missing records are never filled. Present closed/unknown records count toward ingestion coverage, not usable wait coverage."}


def main():
    parser=argparse.ArgumentParser(description="Private finite pilot; start/resume perform live requests")
    parser.add_argument("command",choices=("start","resume","report"))
    args=parser.parse_args()
    try:
        if args.command=="report":
            print(json.dumps(report(),indent=2))
            return
        registry=load_registry()
        PRIVATE.mkdir(parents=True,exist_ok=True)
        with exclusive_lock(PRIVATE/"pilot.lock"):
            logging.Formatter.converter=time.gmtime
            logging.basicConfig(filename=PRIVATE/"pilot.log",level=logging.INFO,format="%(asctime)sZ %(levelname)s %(message)s")
            db=connect(DB)
            try:
                pilot=Pilot(db,ThemeParksProvider(),registry)
                pilot.initialize(resume=args.command=="resume")
                logging.info("Pilot %s; deadline_utc=%s", args.command, datetime.fromtimestamp(db.execute("SELECT deadline FROM pilot").fetchone()[0],timezone.utc).isoformat())
                print("Foreground pilot active; Ctrl+C to stop. Original seven-day deadline is preserved.",flush=True)
                pilot.run(threading.Event())
                logging.info("Pilot deadline reached; collector stopped.")
                print("Pilot deadline reached. Collection stopped.")
            finally:
                db.close()
    except KeyboardInterrupt:
        logging.info("Foreground pilot interrupted; no further requests.")
        print("Pilot stopped; resume retains the original deadline.")
    except (ProviderError,RuntimeError,sqlite3.Error,OSError) as exc:
        logging.error("Pilot stopped: %s", exc)
        parser.exit(1,f"Pilot stopped: {exc}\n")
    except Exception:
        logging.exception("Unexpected collector failure; no automatic restart.")
        parser.exit(1,"Pilot stopped due to an unexpected local error; inspect the private log.\n")


if __name__=="__main__":
    main()

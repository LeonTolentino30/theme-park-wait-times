"""Offline pilot tests: temporary SQLite only; never activates the real pilot."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
import json
import sqlite3
import tempfile
import threading
import unittest

from src import collection_pilot as cp
from src import themeparks_provider as tp

START=datetime(2026,11,8,16,tzinfo=timezone.utc)
PARKS=sorted(tp.PARK_IDS)
ENTRIES=tuple(tp.Attraction(f"Fixture {i}",f"Fixture provider {i}",PARKS[0 if i<5 else 1],f"00000000-0000-4000-8000-{i+1:012d}",False,"verified") for i in range(7))


class Clock:
    def __init__(self): self.seconds=0
    def now(self): return START+timedelta(seconds=self.seconds)
    def monotonic(self): return self.seconds


class FakeProvider:
    timeout=15
    response_headers={}
    last_response_body=b'{"fixture":true}'
    def __init__(self, clock):
        self.clock=clock
        self.calls=[]
        self.error=None
    def live(self,park,registry):
        self.calls.append((park,self.clock.seconds))
        if self.error: raise self.error
        return tuple(tp.Observation(a.alias,a.park_id,a.attraction_id,"OPERATING",0,None,self.clock.now(),True,("missing_provider_timestamp",)) for a in registry if a.park_id==park)


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"test.sqlite3"
        self.db=cp.connect(self.path)
        self.clock=Clock()
        self.provider=FakeProvider(self.clock)
        self.approved=patch.object(cp,"APPROVED",frozenset(a.attraction_id for a in ENTRIES))
        self.approved.start()
        self.pilot=cp.Pilot(self.db,self.provider,ENTRIES,clock=self.clock.now,monotonic=self.clock.monotonic)
        self.pilot.initialize()
    def tearDown(self):
        self.db.close()
        self.approved.stop()
        self.tmp.cleanup()
    def count(self, table): return self.db.execute("SELECT count(*) FROM "+table).fetchone()[0]

    def test_database_write_and_same_tick_idempotency(self):
        self.assertEqual(self.pilot.tick(),"collected")
        self.assertEqual(self.count("runs"),1)
        self.assertEqual(self.count("observations"),5)
        self.assertEqual(self.pilot.tick(),"waiting")
        self.assertEqual(len(self.provider.calls),1)
        row=self.db.execute("SELECT * FROM observations LIMIT 1").fetchone()
        self.assertEqual(row["standby_wait_minutes"],0)
        self.assertEqual(row["source"],"ThemeParks.wiki")
        self.assertIsNone(row["provider_last_changed_at"])
        self.assertEqual(json.loads(row["quality_flags"]),["missing_provider_timestamp"])
        self.assertTrue(row["fetched_at"].endswith("+00:00"))

    def test_staggered_parks_and_five_minimum_minutes(self):
        self.pilot.tick()
        self.clock.seconds=5
        self.pilot.tick()
        self.clock.seconds=299
        self.assertEqual(self.pilot.tick(),"waiting")
        self.clock.seconds=300
        self.pilot.tick()
        for park in PARKS:
            times=[t for p,t in self.provider.calls if p==park]
            self.assertTrue(all(b-a>=300 for a,b in zip(times,times[1:])))

    def test_unique_run_and_observation_constraints(self):
        self.pilot.tick()
        row=self.db.execute("SELECT * FROM runs").fetchone()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db:
                self.db.execute("INSERT INTO runs(park_id,slot,attempted_at,outcome) VALUES(?,?,?,?)",(row["park_id"],row["slot"],row["attempted_at"],"success"))
        columns=[r[1] for r in self.db.execute("PRAGMA table_info(observations)")]
        values=tuple(self.db.execute("SELECT * FROM observations LIMIT 1").fetchone())
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db:
                self.db.execute("INSERT INTO observations VALUES("+','.join('?' for _ in columns)+")",values)

    def test_outage_records_failure_without_invented_rows(self):
        self.provider.error=tp.ProviderError("Provider connection failed.")
        self.pilot.tick()
        self.assertEqual(self.count("observations"),0)
        self.assertEqual(self.db.execute("SELECT outcome FROM runs").fetchone()[0],"error")
        self.clock.seconds=1
        self.pilot.tick()
        self.assertEqual(len(self.provider.calls),1)

    def test_retry_backoff_growth(self):
        self.provider.error=tp.ProviderError("Temporary failure")
        self.pilot.tick()
        self.clock.seconds=300
        self.pilot.tick()  # Other park was also due.
        self.pilot.tick()  # First park's second failure.
        due=self.db.execute("SELECT next_due FROM parks WHERE id=?",(PARKS[0],)).fetchone()[0]
        self.assertEqual(due,START.timestamp()+900)

    def test_rate_limit_blocks_both_parks_and_survives_resume(self):
        self.provider.error=tp.RateLimited(900)
        self.pilot.tick()
        self.clock.seconds=300
        self.assertEqual(self.pilot.tick(),"waiting")
        self.pilot.initialize(resume=True)
        self.assertEqual(self.pilot.tick(),"waiting")
        self.assertEqual(len(self.provider.calls),1)
        self.clock.seconds=900
        self.provider.error=None
        self.assertEqual(self.pilot.tick(),"collected")

    def test_http_503_retry_after(self):
        self.provider.error=tp.ProviderError("Unavailable",http_status=503,retry_after_seconds=1200)
        self.pilot.tick()
        self.clock.seconds=600
        self.assertEqual(self.pilot.tick(),"waiting")

    def test_cache_header_delay(self):
        self.provider.response_headers={"Cache-Control":"public, max-age=900", "Age":"100"}
        self.pilot.tick()
        due=self.db.execute("SELECT next_due FROM parks WHERE id=?",(PARKS[0],)).fetchone()[0]
        self.assertEqual(due,START.timestamp()+800)

    def test_seven_day_deadline_and_no_late_request(self):
        deadline=self.db.execute("SELECT deadline FROM pilot").fetchone()[0]
        self.assertEqual(deadline-START.timestamp(),604800)
        self.clock.seconds=604790
        self.assertEqual(self.pilot.tick(),"expired")
        self.clock.seconds=604800
        self.assertEqual(self.pilot.tick(),"expired")
        self.assertEqual(self.provider.calls,[])

    def test_resume_keeps_deadline_and_skips_missed_intervals(self):
        self.pilot.tick()
        original=self.db.execute("SELECT deadline FROM pilot").fetchone()[0]
        self.clock.seconds=86400
        self.pilot.initialize(resume=True)
        self.assertEqual(self.db.execute("SELECT deadline FROM pilot").fetchone()[0],original)
        self.pilot.tick()
        self.assertEqual(self.count("runs"),2)
        self.assertEqual(self.count("observations"),7)  # Five initial + two other-park rows, no backfill.

    def test_resume_after_deadline_never_requests(self):
        self.clock.seconds=604801
        self.pilot.initialize(resume=True)
        self.assertEqual(self.pilot.tick(),"expired")
        self.assertEqual(self.provider.calls,[])

    def test_restart_without_resume_refused(self):
        with self.assertRaises(RuntimeError): self.pilot.initialize()

    def test_clock_rollback_stops(self):
        self.clock.seconds=-1
        with self.assertRaises(RuntimeError): self.pilot.tick()
        with self.assertRaises(RuntimeError): self.pilot.initialize(resume=True)
        self.assertEqual(self.provider.calls,[])

    def test_monotonic_deadline_cannot_extend_during_session(self):
        self.pilot.monotonic_stop=-1
        self.assertEqual(self.pilot.tick(),"expired")
        self.assertEqual(self.provider.calls,[])

    def test_keyboard_interrupt_is_recorded_and_resumable(self):
        self.provider.error=KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.pilot.tick()
        self.assertEqual(self.db.execute("SELECT outcome FROM runs").fetchone()[0],"interrupted")
        self.assertEqual(self.count("observations"),0)
        self.pilot.initialize(resume=True)
        self.assertEqual(self.pilot.tick(),"waiting")

    def test_pre_set_stop_event_no_request(self):
        stop=threading.Event(); stop.set()
        self.pilot.run(stop)
        self.assertEqual(self.provider.calls,[])

    def test_crashed_claim_not_replayed(self):
        with self.db:
            self.db.execute("INSERT INTO runs(park_id,slot,attempted_at,outcome) VALUES(?,0,?,'in_progress')",(PARKS[0],START.timestamp()))
        self.pilot.initialize(resume=True)
        self.assertEqual(self.db.execute("SELECT outcome FROM runs").fetchone()[0],"interrupted")
        self.assertEqual(self.pilot.tick(),"waiting")
        self.assertEqual(self.provider.calls,[])

    def test_schema_failure_halts_persistently(self):
        self.provider.error=tp.InvalidResponse("Invalid live response envelope.")
        with self.assertRaises(RuntimeError): self.pilot.tick()
        self.clock.seconds=1000
        self.pilot.initialize(resume=True)
        with self.assertRaises(RuntimeError): self.pilot.tick()
        self.assertEqual(len(self.provider.calls),1)

    def test_database_rollback_on_observation_error(self):
        # Tampered observation breaks a foreign key; transaction must not partially persist.
        original=self.provider.live
        def tampered(park,registry):
            rows=list(original(park,registry))
            rows[1]=tp.Observation(rows[1].alias,park,rows[1].attraction_id,"OPERATING",-1,None,self.clock.now(),True,())
            return tuple(rows)
        self.provider.live=tampered
        with self.assertRaises(sqlite3.IntegrityError): self.pilot.tick()
        self.assertEqual(self.count("observations"),0)
        self.assertEqual(self.db.execute("SELECT outcome FROM runs").fetchone()[0],"in_progress")

    def test_closed_null_and_operating_zero_preserved(self):
        def states(park,registry):
            rows=[]
            for i,a in enumerate(x for x in registry if x.park_id==park):
                status,wait=("CLOSED",0) if i==0 else ("UNAVAILABLE",None) if i==1 else ("OPERATING",0)
                rows.append(tp.Observation(a.alias,park,a.attraction_id,status,wait,None,self.clock.now(),i!=1,()))
            return tuple(rows)
        self.provider.live=states
        self.pilot.tick()
        rows=self.db.execute("SELECT status,standby_wait_minutes FROM observations").fetchall()
        self.assertIn(("CLOSED",0),[tuple(r) for r in rows])
        self.assertIn(("UNAVAILABLE",None),[tuple(r) for r in rows])
        self.assertIn(("OPERATING",0),[tuple(r) for r in rows])

    def test_report_coverage_missing_errors_and_counts(self):
        self.pilot.tick()
        self.clock.seconds=600
        report=cp.report(self.path,now=self.clock.now())
        self.assertEqual(report["attempts"],1)
        self.assertEqual(report["error_rate"],0)
        self.assertEqual(len(report["rides"]),7)
        first=next(r for r in report["rides"] if r["alias"]=="Fixture 0")
        self.assertEqual(first["expected_five_minute_slots"],2)
        self.assertEqual(first["missing_records"],1)
        self.assertEqual(first["operating_zeroes"],1)
        self.assertEqual(first["coverage_fraction"],0.5)
        self.assertIn("-05:00",report["deadline_new_york"])

    def test_total_request_budget_halts_without_second_request(self):
        gate=threading.Event()
        self.provider.timeout=0.02
        def hangs(*args): gate.wait(1); return ()
        self.provider.live=hangs
        try:
            with self.assertRaises(RuntimeError): self.pilot.tick()
            self.assertIsNotNone(self.db.execute("SELECT halted FROM pilot").fetchone()[0])
        finally: gate.set()


class UtilityTests(unittest.TestCase):
    def test_exclusive_lock_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"test.lock"
            with cp.exclusive_lock(path):
                with self.assertRaises(RuntimeError):
                    with cp.exclusive_lock(path): pass
            with cp.exclusive_lock(path): pass

    def test_cache_age_expiry_and_invalid_values(self):
        now=START.timestamp()
        self.assertEqual(cp.cache_delay({"Cache-Control":"max-age=30"},now),300)
        self.assertEqual(cp.cache_delay({"Cache-Control":"s-maxage=1200, max-age=900","Age":"100"},now),1100)
        self.assertEqual(cp.cache_delay({"Expires":"Sun, 08 Nov 2026 17:00:00 GMT"},now),3600)
        self.assertEqual(cp.cache_delay({"Age":"bad","Expires":"bad"},now),300)

    def test_report_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"absent.sqlite3"
            with self.assertRaises(sqlite3.OperationalError): cp.report(path)
            self.assertFalse(path.exists())


if __name__=="__main__": unittest.main()

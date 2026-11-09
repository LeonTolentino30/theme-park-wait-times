"""Dashboard uses fabricated observations in temporary databases; never contacts provider."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from src import current_waits as cw
from src.collection_pilot import connect, APPROVED
from src.themeparks_provider import Attraction

NOW=datetime(2026,11,8,20,tzinfo=timezone.utc)
PARKS=list(cw.PARK_NAMES)
IDS=sorted(APPROVED)
ENTRIES=tuple(Attraction(f"Fixture {i}",f"Fixture Ride {i}",PARKS[0 if i<5 else 1],entity,False,"verified") for i,entity in enumerate(IDS))


class CurrentWaitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"fixture.sqlite3"
        self.db=connect(self.path)
        self.registry=patch("src.current_waits.load_registry",return_value=ENTRIES)
        self.registry.start()
        self.clock=patch("src.current_waits.utc_now",return_value=NOW)
        self.clock.start()
        with patch("app.load_new_model",return_value=object()):
            self.app=create_app({"TESTING":True,"CURRENT_WAITS_DB":self.path,"CURRENT_WAITS_REGISTRY":"unused"})
        self.client=self.app.test_client()
        with self.db:
            for park in PARKS:
                self.db.execute("INSERT INTO parks(id,offset_seconds,next_due) VALUES(?,0,0)",(park,))
            self.db.executemany("INSERT INTO attractions VALUES(?,?,?)",[(a.attraction_id,a.park_id,a.alias) for a in ENTRIES])
    def tearDown(self):
        self.db.close()
        self.registry.stop(); self.clock.stop()
        self.tmp.cleanup()
    def seed(self,park=None,*,age=60,status="OPERATING",wait=15,outcome="success",present=1,provider_age=3600):
        park=park or PARKS[0]
        when=NOW-timedelta(seconds=age)
        slot=self.db.execute("SELECT count(*) FROM runs").fetchone()[0]
        with self.db:
            cursor=self.db.execute("INSERT INTO runs(park_id,slot,attempted_at,completed_at,outcome,error,raw_body) VALUES(?,?,?,?,?,?,?)",(park,slot,when.timestamp(),when.timestamp(),outcome,"SECRET C:/private/path",b"SECRET raw response"))
            run=cursor.lastrowid
            if outcome=="success":
                for a in ENTRIES:
                    if a.park_id==park:
                        self.db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?)",(run,a.attraction_id,park,when.isoformat(),(NOW-timedelta(seconds=provider_age)).isoformat(),status,wait,"ThemeParks.wiki",present,'[]'))
        return run
    def cards(self,data): return [a for p in data["parks"] for a in p["attractions"]]

    def test_endpoint_structure_and_grouped_seven_rides(self):
        for park in PARKS: self.seed(park)
        response=self.client.get("/api/current-waits")
        self.assertEqual(response.status_code,200)
        data=response.get_json()
        self.assertEqual([p["name"] for p in data["parks"]],list(cw.PARK_NAMES.values()))
        self.assertEqual([len(p["attractions"]) for p in data["parks"]],[5,2])
        self.assertEqual({a["attraction_id"] for a in self.cards(data)},APPROVED)
        self.assertEqual(data["attribution"]["url"],"https://www.themeparks.wiki")
        self.assertEqual(response.headers["Cache-Control"],"no-store")

    def test_recent_collection_is_not_provider_last_change(self):
        self.seed(provider_age=7200)
        row=self.cards(cw.read_current_waits(self.path))[0]
        self.assertEqual(row["freshness"],"recent")
        self.assertNotEqual(row["observed_at"],row["provider_last_changed_at"])
        self.assertIn("EST",row["observed_at_display"])

    def test_stale_observations_historical(self):
        self.seed(age=901)
        row=self.cards(self.client.get("/api/current-waits").get_json())[0]
        self.assertEqual(row["freshness"],"historical")
        self.assertEqual(row["freshness_label"],"Historical snapshot")
        self.assertEqual(row["posted_wait_minutes"],15)
        self.assertIn("Historical snapshot",self.client.get("/").get_data(as_text=True))

    def test_future_times_not_recent(self):
        self.seed(age=-120)
        self.assertEqual(self.cards(cw.read_current_waits(self.path))[0]["freshness"],"historical")

    def test_empty_database_returns_placeholders(self):
        response=self.client.get("/api/current-waits")
        self.assertEqual(response.status_code,503)
        self.assertEqual(len(self.cards(response.get_json())),7)
        self.assertTrue(all(a["posted_wait_minutes"] is None for a in self.cards(response.get_json())))
        self.assertEqual(self.client.get("/").status_code,200)

    def test_missing_database_not_created(self):
        absent=Path(self.tmp.name)/"missing/subfolder/absent.sqlite3"
        self.app.config["CURRENT_WAITS_DB"]=absent
        response=self.client.get("/api/current-waits")
        self.assertEqual(response.status_code,503)
        self.assertFalse(absent.exists())
        self.assertFalse(absent.parent.exists())
        self.assertNotIn(str(absent),response.get_data(as_text=True))

    def test_corrupt_database_safe(self):
        path=Path(self.tmp.name)/"corrupt.sqlite3"
        path.write_bytes(b"not sqlite SECRET")
        self.app.config["CURRENT_WAITS_DB"]=path
        response=self.client.get("/api/current-waits")
        self.assertEqual(response.status_code,503)
        self.assertNotIn("SECRET",response.get_data(as_text=True))

    def test_statuses_closed_zero_and_operating_zero(self):
        for status,wait,label in (("CLOSED",0,"Closed"),("DOWN",0,"Temporarily down"),("REFURBISHMENT",None,"Refurbishment"),("UNAVAILABLE",None,"Unavailable"),("OPERATING",None,"Wait unavailable"),("OPERATING",0,"0 min")):
            with self.subTest(status=status,wait=wait):
                self.seed(age=10-status.count('X'),status=status,wait=wait)
                row=self.cards(cw.read_current_waits(self.path))[0]
                self.assertEqual(row["wait_label"],label)
                self.assertEqual(row["posted_wait_minutes"],0 if status=="OPERATING" and wait==0 else None)

    def test_latest_success_not_failed_or_old_rows(self):
        self.seed(age=800,wait=50)
        self.seed(age=100,wait=25)
        self.seed(age=20,outcome="error")
        self.assertEqual(self.cards(cw.read_current_waits(self.path))[0]["posted_wait_minutes"],25)

    def test_latest_absent_entity_does_not_resurrect_previous_wait(self):
        self.seed(age=600,wait=40)
        run=self.seed(age=20)
        with self.db:
            self.db.execute("DELETE FROM observations WHERE run_id=? AND attraction_id=?",(run,IDS[0]))
        row=self.cards(cw.read_current_waits(self.path))[0]
        self.assertEqual(row["status"],"UNAVAILABLE")
        self.assertIsNone(row["posted_wait_minutes"])

    def test_raw_data_logs_paths_never_in_response(self):
        self.seed()
        text=self.client.get("/api/current-waits").get_data(as_text=True)
        for forbidden in ("SECRET","raw_body","sqlite3","quality_flags","C:/private"):
            self.assertNotIn(forbidden,text)
        self.assertEqual(self.client.get("/private/themeparks/pilot/pilot.sqlite3").status_code,404)

    def test_reads_during_wal_writer_and_no_database_modification(self):
        self.seed()
        before=self.db.execute("SELECT count(*) FROM observations").fetchone()[0]
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("UPDATE observations SET standby_wait_minutes=999")
        try:
            data=cw.read_current_waits(self.path)
            self.assertEqual(self.cards(data)[0]["posted_wait_minutes"],15)
        finally:
            self.db.rollback()
        self.assertEqual(self.db.execute("SELECT count(*) FROM observations").fetchone()[0],before)
        self.assertEqual(self.db.execute("PRAGMA journal_mode").fetchone()[0],"wal")

    def test_frontend_rendering_and_original_prediction_form(self):
        self.seed()
        html=self.client.get("/").get_data(as_text=True)
        for expected in ("Current Posted Waits","Fixture Ride 0","Islands of Adventure","Universal Studios Florida","Provider last change","Recent stored observation",'href="https://www.themeparks.wiki"','id="prediction-form"',"SYNTHETIC MODEL ESTIMATES, NOT LIVE PARK WAIT TIMES"):
            self.assertIn(expected,html)
        self.assertNotIn("SECRET",html)
        with self.client.get("/static/current-waits.js") as response:
            self.assertEqual(response.status_code,200)
            self.assertNotIn("setInterval",response.get_data(as_text=True))

    def test_api_readonly_method_and_no_provider_requests(self):
        self.seed()
        with patch("src.themeparks_provider.ThemeParksProvider.live",side_effect=AssertionError("No HTTP permitted")):
            self.assertEqual(self.client.get("/api/current-waits").status_code,200)
        self.assertEqual(self.client.post("/api/current-waits").status_code,405)
        self.assertFalse(self.app.debug)

    def test_bad_registry_graceful(self):
        self.registry.stop()
        with patch("src.current_waits.load_registry",return_value=()):
            self.assertEqual(self.client.get("/api/current-waits").status_code,503)
            self.assertEqual(self.client.get("/").status_code,200)
        self.registry.start()

    def test_invalid_timestamp_safe(self):
        self.seed()
        with self.db:
            self.db.execute("UPDATE observations SET fetched_at='bad timestamp'")
        data=self.client.get("/api/current-waits").get_json()
        self.assertFalse(data["available"])
        self.assertTrue(all(a["freshness"]=="unavailable" for a in self.cards(data)))


if __name__=="__main__": unittest.main()

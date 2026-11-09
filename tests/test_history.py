"""Offline history tests: fabricated observations, temporary SQLite only."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from src import history as h
from src.collection_pilot import connect,APPROVED
from src.themeparks_provider import Attraction

NOW=datetime(2026,11,8,20,tzinfo=timezone.utc)
ID=sorted(APPROVED)[0]
PARK=next(iter(h.PARK_NAMES))
RIDE=Attraction("Fixture ride","Fixture provider ride",PARK,ID,False,"verified")


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"history.sqlite3"
        self.db=connect(self.path)
        with self.db:
            self.db.execute("INSERT INTO parks(id,offset_seconds,next_due) VALUES(?,0,0)",(PARK,))
            self.db.execute("INSERT INTO attractions VALUES(?,?,?)",(ID,PARK,RIDE.alias))
        self.registry=patch("src.history.load_registry",return_value=(RIDE,));self.registry.start()
        self.clock=patch("src.history.utc_now",return_value=NOW);self.clock.start()
        with patch("app.load_new_model",return_value=object()):
            self.app=create_app({"TESTING":True,"CURRENT_WAITS_DB":self.path,"CURRENT_WAITS_REGISTRY":"unused"})
        self.client=self.app.test_client()
    def tearDown(self):
        self.db.close();self.registry.stop();self.clock.stop();self.tmp.cleanup()
    def seed(self,age=300,wait=20,status="OPERATING",present=1,outcome="success",when=None):
        when=when or NOW-timedelta(seconds=age)
        slot=self.db.execute("SELECT count(*) FROM runs").fetchone()[0]
        with self.db:
            run=self.db.execute("INSERT INTO runs(park_id,slot,attempted_at,completed_at,outcome,raw_body,error) VALUES(?,?,?,?,?,?,?)",(PARK,slot,when.timestamp(),when.timestamp(),outcome,b"SECRET", "SECRET C:/private/path")).lastrowid
            if outcome=="success":
                self.db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?)",(run,ID,PARK,when.isoformat(),None,status,wait,"ThemeParks.wiki",present,'[]'))
        return run
    def read(self,**params):
        return h.read_history({"attraction_id":ID,**params},self.path,"unused",now=NOW)
    def get(self,**params):
        return self.client.get("/api/history",query_string={"attraction_id":ID,**params})

    def test_response_structure_real_samples_and_chronology(self):
        self.seed(age=300,wait=40);self.seed(age=600,wait=15)
        response=self.get(hours="6")
        self.assertEqual(response.status_code,200)
        data=response.get_json()
        self.assertEqual([p["posted_wait_minutes"] for p in data["points"]],[15,40])
        self.assertEqual(data["timezone"],"America/New_York")
        self.assertTrue(data["meaningful_chart"])
        self.assertEqual(response.headers["Cache-Control"],"no-store")
        self.assertEqual(data["attribution"]["url"],"https://www.themeparks.wiki")

    def test_six_hour_filter_including_boundary(self):
        self.seed(age=21601);self.seed(age=21600,wait=10);self.seed(age=60,wait=30)
        data=self.read(hours="6")
        self.assertEqual([p["posted_wait_minutes"] for p in data["points"]],[10,30])

    def test_all_supported_windows(self):
        self.seed(age=3600);self.seed(age=12*3600);self.seed(age=3*86400)
        self.assertEqual(len(self.read(hours="6")["points"]),1)
        self.assertEqual(len(self.read(hours="24")["points"]),2)
        self.assertEqual(len(self.read(hours="168")["points"]),3)

    def test_explicit_timezone_range(self):
        self.seed(age=3600);self.seed(age=600)
        data=self.read(start="2026-11-08T13:30:00-05:00",end="2026-11-08T14:30:00-05:00")
        self.assertEqual(len(data["points"]),1)
        self.assertIn("EST",data["requested_period_display"])

    def test_invalid_attraction_and_unknown_fields(self):
        self.assertEqual(self.client.get("/api/history").status_code,400)
        for value in ("unknown","' OR 1=1 --", "", "not-a-uuid"):
            self.assertEqual(self.client.get("/api/history",query_string={"attraction_id":value}).status_code,400)
        self.assertEqual(self.get(hours="6",raw="true").status_code,400)

    def test_invalid_window_values(self):
        for hours in ("0","7","24.0","-6","169","999999",""):
            with self.subTest(hours=hours):self.assertEqual(self.get(hours=hours).status_code,400)

    def test_invalid_date_ranges(self):
        pairs=[("bad","bad"),("2026-11-08","2026-11-09"),("2026-11-08T20:00:00Z","2026-11-08T19:00:00Z"),("2026-11-08T20:00:00Z","2026-11-08T20:00:00Z"),("2026-11-08T19:00:00Z","2026-11-09T20:00:00Z"),("2026-10-01T00:00:00Z","2026-11-01T00:00:00Z")]
        for start,end in pairs:
            with self.subTest(start=start,end=end):self.assertEqual(self.get(start=start,end=end).status_code,400)
        self.assertEqual(self.get(start="2026-11-08T19:00:00Z").status_code,400)
        self.assertEqual(self.get(hours="6",start="2026-11-08T19:00:00Z",end="2026-11-08T20:00:00Z").status_code,400)

    def test_duplicate_query_parameters_rejected(self):
        response=self.client.get("/api/history",query_string=[("attraction_id",ID),("hours","6"),("hours","24")])
        self.assertEqual(response.status_code,400)

    def test_closed_down_refurbishment_not_zero(self):
        for status in ("CLOSED","DOWN","REFURBISHMENT"):
            self.seed(age=900-len(status),status=status,wait=0)
        data=self.read()
        self.assertTrue(all(p["posted_wait_minutes"] is None and p["event"] for p in data["points"]))
        self.assertFalse(data["meaningful_chart"])

    def test_missing_wait_and_unavailable_record(self):
        self.seed(age=600,wait=None)
        self.seed(age=300,wait=None,status="UNAVAILABLE",present=0)
        points=self.read()["points"]
        self.assertEqual(len(points),2)
        self.assertTrue(all(p["posted_wait_minutes"] is None for p in points))

    def test_genuine_zero_operating_wait_preserved(self):
        self.seed(age=600,wait=0);self.seed(age=300,wait=5)
        self.assertEqual([p["posted_wait_minutes"] for p in self.read()["points"]],[0,5])

    def test_collection_gap_breaks_without_invented_rows(self):
        self.seed(age=1200,wait=10);self.seed(age=300,wait=30)
        data=self.read()
        self.assertEqual(len(data["points"]),2)
        self.assertFalse(data["points"][0]["break_before"])
        self.assertTrue(data["points"][1]["break_before"])
        self.assertEqual([p["posted_wait_minutes"] for p in data["points"]],[10,30])

    def test_adjacent_samples_not_gap(self):
        self.seed(age=615);self.seed(age=300)
        self.assertFalse(self.read()["points"][1]["break_before"])

    def test_empty_and_one_sample_message(self):
        self.assertEqual(self.get().status_code,200)
        self.assertFalse(self.read()["meaningful_chart"])
        self.seed()
        data=self.read()
        self.assertFalse(data["meaningful_chart"])
        self.assertIn("Not enough",data["message"])

    def test_failed_runs_and_future_records_excluded(self):
        self.seed(age=600);self.seed(age=300,outcome="error");self.seed(age=-60)
        self.assertEqual(len(self.read()["points"]),1)

    def test_missing_database_not_created_and_error_sanitized(self):
        missing=Path(self.tmp.name)/"missing"/"absent.sqlite3"
        self.app.config["CURRENT_WAITS_DB"]=missing
        response=self.get()
        self.assertEqual(response.status_code,503)
        self.assertFalse(missing.parent.exists())
        self.assertNotIn("sqlite",response.get_data(as_text=True))
        self.assertNotIn(str(missing),response.get_data(as_text=True))

    def test_read_during_wal_write_does_not_modify_database(self):
        self.seed()
        self.db.execute("BEGIN IMMEDIATE");self.db.execute("UPDATE observations SET standby_wait_minutes=999")
        try:self.assertEqual(self.read()["points"][0]["posted_wait_minutes"],20)
        finally:self.db.rollback()
        self.assertEqual(self.db.execute("SELECT standby_wait_minutes FROM observations").fetchone()[0],20)

    def test_private_fields_not_exposed_and_no_provider_calls(self):
        self.seed()
        with patch("src.themeparks_provider.ThemeParksProvider.live",side_effect=AssertionError("No requests")):
            response=self.get()
        for token in ("SECRET","raw_body","C:/private","error logs","quality_flags"):
            self.assertNotIn(token,response.get_data(as_text=True))
        self.assertEqual(self.client.post("/api/history").status_code,405)

    def test_daylight_saving_labels_are_unambiguous(self):
        self.seed(when=datetime(2026,11,1,5,30,tzinfo=timezone.utc))
        self.seed(when=datetime(2026,11,1,6,30,tzinfo=timezone.utc))
        data=self.read(start="2026-11-01T05:00:00Z",end="2026-11-01T07:00:00Z")
        self.assertIn("EDT",data["points"][0]["time_display"])
        self.assertIn("EST",data["points"][1]["time_display"])

    def test_frontend_rendering_preserves_existing_sections(self):
        html=self.client.get("/").get_data(as_text=True)
        for text in ("Historical Wait Trends","Current Posted Waits",'id="prediction-form"','id="history-ride"',"Last 6 hours","Last 24 hours","Last 7 days","not measured queue durations or AI forecasts"):
            self.assertIn(text,html)
        with self.client.get("/static/history.js") as response:
            self.assertEqual(response.status_code,200)
            js=response.get_data(as_text=True)
            self.assertIn("point.break_before",js)
            self.assertNotIn("setInterval",js)


if __name__=="__main__":unittest.main()

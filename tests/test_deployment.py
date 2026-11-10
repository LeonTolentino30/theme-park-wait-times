import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from src import deployment_build as build
from src import ml_pipeline as ml


class PublicDeploymentTests(unittest.TestCase):
    def create(self,model=object()):
        with patch("app.load_new_model",return_value=model):
            return create_app({"PUBLIC_DEPLOYMENT":True,"DEBUG":True,"TESTING":True,"OBSERVED_DATA_ENABLED":True})

    def test_debug_forced_off_and_observations_disabled(self):
        app=self.create()
        self.assertFalse(app.debug)
        self.assertFalse(app.testing)
        self.assertFalse(app.config["OBSERVED_DATA_ENABLED"])

    def test_public_home_without_database_or_registry(self):
        app=self.create()
        with patch("app.read_current_waits",side_effect=AssertionError("Private reads forbidden")), patch("app.read_history",side_effect=AssertionError("Private reads forbidden")):
            client=app.test_client()
            response=client.get("/")
            self.assertEqual(response.status_code,200)
            html=response.get_data(as_text=True)
            for text in ("Current Posted Waits","Historical Wait Trends","not configured for this public demo","No synthetic history is substituted","Powered by ThemeParks.wiki","SYNTHETIC MODEL ESTIMATES, NOT LIVE PARK WAIT TIMES"):
                self.assertIn(text,html)
            self.assertEqual(client.get("/api/current-waits").status_code,503)
            self.assertEqual(client.get("/api/history?attraction_id=anything").status_code,503)
            self.assertNotIn("sqlite",client.get("/api/current-waits").get_data(as_text=True))

    def test_public_synthetic_prediction_still_works(self):
        client=self.create().test_client()
        with patch("app.predict_wait_time",return_value=89.075765):
            response=client.post("/api/predict",json={"ride":"Hulk Coaster","day":"Sat","hour":12})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.get_json()["predicted_minutes"],89)
            self.assertTrue(response.get_json()["synthetic"])

    def test_prediction_rate_limit(self):
        client=self.create().test_client()
        with patch("app.time.monotonic",return_value=100),patch("app.predict_wait_time",return_value=10):
            for _ in range(60):self.assertEqual(client.post("/api/predict",json={"ride":"Hulk Coaster","day":"Sat","hour":12}).status_code,200)
            response=client.post("/api/predict",json={"ride":"Hulk Coaster","day":"Sat","hour":12})
            self.assertEqual(response.status_code,429)
            self.assertEqual(response.headers["Retry-After"],"60")
        with patch("app.time.monotonic",return_value=161),patch("app.predict_wait_time",return_value=10):
            self.assertEqual(client.post("/api/predict",json={"ride":"Hulk Coaster","day":"Sat","hour":12}).status_code,200)

    def test_public_validation_body_limit_and_health(self):
        client=self.create().test_client()
        self.assertEqual(client.post("/api/predict",json={"ride":"wrong","day":"Sat","hour":12}).status_code,400)
        self.assertEqual(client.post("/api/predict",data=" "*5000,content_type="application/json").status_code,413)
        response=client.get("/healthz")
        self.assertEqual(response.get_json(),{"status":"ok"})
        self.assertEqual(response.status_code,200)
        self.assertIn("max-age",response.headers["Strict-Transport-Security"])
        self.assertEqual(response.headers["Referrer-Policy"],"no-referrer")
        self.assertEqual(self.create(None).test_client().get("/healthz").status_code,503)

    def test_public_prediction_failure_sanitized(self):
        client=self.create().test_client()
        with patch("app.predict_wait_time",side_effect=RuntimeError("SECRET path details")):
            response=client.post("/api/predict",json={"ride":"Hulk Coaster","day":"Sat","hour":12})
            self.assertEqual(response.status_code,503)
            self.assertNotIn("SECRET",response.get_data(as_text=True))


class DeploymentBuildTests(unittest.TestCase):
    def test_fresh_build_calls_protected_training(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(ml,"MODEL",Path(temporary)/"new.joblib"),patch.object(ml,"RUNTIME_REPORT",Path(temporary)/"manifest.json"),patch.object(ml,"train") as train:
                self.assertEqual(build.ensure_model(),"generated")
                train.assert_called_once_with()

    def test_existing_verified_build_not_overwritten_or_deserialized(self):
        with tempfile.TemporaryDirectory() as temporary:
            model=Path(temporary)/"model.joblib";model.write_bytes(b"private fixture")
            data=Path(temporary)/"data.csv";data.write_bytes(b"fixture csv")
            manifest=Path(temporary)/"manifest.json"
            manifest.write_text(json.dumps({"model":"models/synthetic_rf_v1.joblib","model_sha256":ml.sha256(model),"dataset_sha256":ml.sha256(data)}))
            with patch.object(ml,"MODEL",model),patch.object(ml,"DATA",data),patch.object(ml,"RUNTIME_REPORT",manifest),patch.object(ml,"train") as train,patch.object(ml.joblib,"load") as loader:
                self.assertEqual(build.ensure_model(),"verified-existing")
                train.assert_not_called();loader.assert_not_called()
                model.write_bytes(b"corrupt fixture")
                with self.assertRaises(RuntimeError):build.ensure_model()
                self.assertEqual(model.read_bytes(),b"corrupt fixture")

    def test_partial_artifacts_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            model=Path(temporary)/"model.joblib"
            manifest=Path(temporary)/"manifest.json";manifest.write_text('{}')
            with patch.object(ml,"MODEL",model),patch.object(ml,"RUNTIME_REPORT",manifest):
                with self.assertRaises(RuntimeError):build.ensure_model()
            self.assertFalse(model.exists())
            self.assertEqual(manifest.read_text(),'{}')

    def test_render_configuration_does_not_start_collector(self):
        root=Path(__file__).resolve().parents[1]
        config=(root/"render.yaml").read_text()
        self.assertIn('autoDeployTrigger: "off"',config)
        self.assertIn("gunicorn wsgi:application",config)
        self.assertIn("0.0.0.0:$PORT",config)
        self.assertIn("healthCheckPath: /healthz",config)
        self.assertNotIn("collection_pilot",config)
        self.assertNotIn("disk:",config)
        self.assertEqual((root/".python-version").read_text().strip(),"3.12.16")


if __name__=="__main__":unittest.main()

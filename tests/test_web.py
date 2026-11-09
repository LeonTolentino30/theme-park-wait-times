import unittest
from unittest.mock import patch
from html.parser import HTMLParser
from app import create_app, DISCLAIMER
from src.ml_pipeline import RIDES, DAYS


class Options(HTMLParser):
    def __init__(self):
        super().__init__()
        self.select = None
        self.options = {}
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self.select = attrs["id"]
            self.options[self.select] = []
        if tag == "option" and self.select and attrs.get("value"):
            self.options[self.select].append(attrs["value"])
    def handle_endtag(self, tag):
        if tag == "select":
            self.select = None


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    def post(self, **changes):
        payload = {"ride": "Hulk Coaster", "day": "Sat", "hour": 12}
        payload.update(changes)
        return self.client.post("/api/predict", json=payload)

    def test_home_and_exact_supported_options(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(DISCLAIMER, html)
        parser = Options()
        parser.feed(html)
        self.assertEqual(parser.options["ride"], list(RIDES))
        self.assertEqual(parser.options["day"], list(DAYS))
        self.assertEqual(parser.options["hour"], [str(h) for h in range(8, 23)])

    def test_valid_request_response_structure_and_prediction(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(set(data), {"ride", "day", "hour", "predicted_minutes", "synthetic", "disclaimer"})
        self.assertEqual(data["predicted_minutes"], 89)
        self.assertIs(type(data["predicted_minutes"]), int)
        self.assertTrue(data["synthetic"])
        self.assertEqual(data["disclaimer"], DISCLAIMER)
        self.assertEqual((data["ride"], data["day"], data["hour"]), ("Hulk Coaster", "Sat", 12))

    def test_boundary_hours(self):
        for hour in (8, 22):
            with self.subTest(hour=hour):
                self.assertEqual(self.post(hour=hour).status_code, 200)

    def test_invalid_rides(self):
        for ride in ("unknown", "hulk coaster", "Hulk Coaster ", None, 1, []):
            with self.subTest(ride=ride):
                response = self.post(ride=ride)
                self.assertEqual(response.status_code, 400)
                self.assertIn("ride", response.get_json()["error"])

    def test_invalid_days(self):
        for day in ("Saturday", "sat", None, 1, []):
            with self.subTest(day=day):
                response = self.post(day=day)
                self.assertEqual(response.status_code, 400)
                self.assertIn("day", response.get_json()["error"])

    def test_invalid_hours(self):
        for hour in (7, 23, "12", 12.0, True, None, [], {}):
            with self.subTest(hour=hour):
                response = self.post(hour=hour)
                self.assertEqual(response.status_code, 400)
                self.assertIn("hour", response.get_json()["error"])

    def test_missing_and_extra_fields(self):
        for payload in ({}, {"ride": "Hulk Coaster"}, {"ride": "Hulk Coaster", "day": "Sat", "hour": 12, "model_path": "untrusted"}):
            self.assertEqual(self.client.post("/api/predict", json=payload).status_code, 400)

    def test_malformed_and_nonobject_json(self):
        for body in ('{bad', 'null', '[]', '"text"', '5'):
            response = self.client.post("/api/predict", data=body, content_type="application/json")
            self.assertEqual(response.status_code, 400)
            self.assertIn("error", response.get_json())

    def test_non_json_content_type(self):
        self.assertEqual(self.client.post("/api/predict", data="ride=Hulk").status_code, 400)

    def test_oversized_request(self):
        response = self.client.post("/api/predict", data=" " * 5000, content_type="application/json")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json(), {"error": "Request is too large."})

    def test_model_loaded_once_through_phase_one_loader(self):
        with patch("app.load_new_model", return_value=object()) as loader:
            app = create_app()
            client = app.test_client()
            with patch("app.predict_wait_time", return_value=10.5) as predict:
                for _ in range(2):
                    response = client.post("/api/predict", json={"ride": "Hulk Coaster", "day": "Sat", "hour": 12})
                    self.assertEqual(response.get_json()["predicted_minutes"], 11)
                self.assertEqual(predict.call_count, 2)
            loader.assert_called_once_with()

    def test_model_unavailable_and_prediction_failure_are_sanitized(self):
        with patch("app.load_new_model", side_effect=RuntimeError("SECRET C:/private/model.joblib")):
            client = create_app().test_client()
            response = client.post("/api/predict", json={"ride": "Hulk Coaster", "day": "Sat", "hour": 12})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("SECRET", response.get_data(as_text=True))
            self.assertEqual(client.get("/").status_code, 200)
        with patch("app.predict_wait_time", side_effect=RuntimeError("SECRET C:/private/model.joblib")):
            response = self.post()
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("SECRET", response.get_data(as_text=True))
            self.assertNotIn("Traceback", response.get_data(as_text=True))

    def test_methods_debug_headers_and_static_assets(self):
        self.assertFalse(self.app.debug)
        self.assertEqual(self.client.get("/api/predict").status_code, 405)
        self.assertEqual(self.client.get("/missing").status_code, 404)
        response = self.client.get("/")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        for asset in ("app.js", "style.css"):
            with self.client.get("/static/" + asset) as asset_response:
                self.assertEqual(asset_response.status_code, 200)

    def test_repeatable_predictions(self):
        self.assertEqual(self.post().get_json(), self.post().get_json())


if __name__ == "__main__":
    unittest.main()

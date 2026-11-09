import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import joblib
import numpy as np

from src import ml_pipeline as ml


class PredictionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = ml.read_dataset()
        cls.model = ml.make_pipeline(20).fit(cls.df[ml.FEATURES], cls.df.wait_time)

    def test_exact_feature_order_and_encoding(self):
        transformer = self.model.named_steps["features"]
        expected = [f"ride_{ride}" for ride in ml.RIDES] + [f"day_{day}" for day in ml.DAYS] + ["hour"]
        self.assertEqual(transformer.get_feature_names_out().tolist(), expected)
        frame = ml.input_frame("VelociCoaster", "Sun", 22)
        encoded = transformer.transform(frame)[0]
        expected_values = [int(ride == "VelociCoaster") for ride in ml.RIDES] + [int(day == "Sun") for day in ml.DAYS] + [22]
        np.testing.assert_array_equal(encoded, expected_values)

    def test_valid_inputs_and_boundaries(self):
        for ride in ml.RIDES:
            for day in ml.DAYS:
                for hour in (8, 22):
                    with self.subTest(ride=ride, day=day, hour=hour):
                        value = ml.predict_wait_time(ride, day, hour, model=self.model)
                        self.assertTrue(np.isfinite(value))
                        self.assertGreaterEqual(value, 0)

    def test_invalid_inputs_rejected_before_loading(self):
        bad = [(ride, "Mon", 12) for ride in ("unknown", "velocicoaster", "VelociCoaster ", None, [])]
        bad += [("VelociCoaster", day, 12) for day in ("Monday", "mon", " Mon", None, [])]
        bad += [("VelociCoaster", "Mon", hour) for hour in (7, 23, 12.0, "12", True, None, float("nan"))]
        with patch.object(ml, "load_new_model") as loader:
            for args in bad:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    ml.predict_wait_time(*args)
            loader.assert_not_called()

    def test_reproducible_predictions(self):
        second = ml.make_pipeline(20).fit(self.df[ml.FEATURES], self.df.wait_time)
        np.testing.assert_array_equal(self.model.predict(self.df[ml.FEATURES]), second.predict(self.df[ml.FEATURES]))

    def test_reproducible_evaluation(self):
        self.assertEqual(ml.evaluate(self.df, 10), ml.evaluate(self.df, 10))

    def test_group_holdout_disjoint_and_complete(self):
        groups, partitions = ml.splits(self.df)
        for name, (train, test) in partitions.items():
            self.assertEqual(set(train) & set(test), set())
            self.assertEqual(set(train) | set(test), set(range(len(self.df))))
        train, test = partitions["held_out_combinations"]
        self.assertEqual(set(groups[train]) & set(groups[test]), set())
        train, test = partitions["random_rows"]
        self.assertGreater(len(set(groups[train]) & set(groups[test])), 0)

    def test_metrics_known_values(self):
        scores = ml.metrics([1, 3], [2, 2])
        self.assertEqual(scores, {"mae_minutes": 1.0, "rmse_minutes": 1.0, "r2": 0.0})

    def test_new_serialization_roundtrip_and_integrity_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            model_path = Path(temporary) / "new.joblib"
            report_path = Path(temporary) / "report.json"
            joblib.dump(self.model, model_path)
            report_path.write_text(json.dumps({"model": "models/synthetic_rf_v1.joblib", "model_sha256": ml.sha256(model_path)}))
            with patch.object(ml, "MODEL", model_path), patch.object(ml, "REPORT", report_path), patch.object(ml, "RUNTIME_REPORT", report_path):
                loaded = ml.load_new_model()
                self.assertEqual(ml.predict_wait_time("Hulk Coaster", "Sat", 12, model=loaded), ml.predict_wait_time("Hulk Coaster", "Sat", 12, model=self.model))
                report_path.write_text(json.dumps({"model": "models/synthetic_rf_v1.joblib", "model_sha256": "wrong"}))
                with patch.object(ml.joblib, "load") as loader, self.assertRaises(ValueError):
                    ml.load_new_model()
                loader.assert_not_called()

    def test_training_refuses_existing_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            model_path = Path(temporary) / "new.joblib"
            model_path.write_bytes(b"preserve")
            with patch.object(ml, "MODEL", model_path), self.assertRaises(FileExistsError):
                ml.train()
            self.assertEqual(model_path.read_bytes(), b"preserve")

    def test_saved_production_model_matches_fresh_200_tree_fit(self):
        saved = ml.load_new_model()
        fresh = ml.make_pipeline().fit(self.df[ml.FEATURES], self.df.wait_time)
        np.testing.assert_array_equal(saved.predict(self.df[ml.FEATURES]), fresh.predict(self.df[ml.FEATURES]))
        report = json.loads(ml.model_manifest_path().read_text(encoding="utf-8"))
        self.assertEqual(report["dataset_sha256"], ml.sha256(ml.DATA))
        self.assertEqual(report["feature_names"], saved.named_steps["features"].get_feature_names_out().tolist())
        self.assertEqual(saved.named_steps["regressor"].n_estimators, 200)

    def test_cli_valid_prediction(self):
        result = subprocess.run([sys.executable, "-B", "-m", "src.ml_pipeline", "predict", "--ride", "Hulk Coaster", "--day", "Sat", "--hour", "12"], cwd=ml.ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = ml.predict_wait_time("Hulk Coaster", "Sat", 12)
        self.assertIn(f"{expected:.6f} minutes", result.stdout)
        self.assertIn("not a real-world wait time", result.stdout)

    def test_fresh_checkout_trains_without_changing_published_report(self):
        # Copy reviewed source + CSV only: no model/manifest or private data.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "data/raw").mkdir(parents=True)
            (root / "reports").mkdir()
            (root / "src/ml_pipeline.py").write_bytes(Path(ml.__file__).read_bytes())
            (root / "data/raw/wait_times.csv").write_bytes(ml.DATA.read_bytes())
            published = root / "reports/synthetic_rf_v1.json"
            published.write_text('{"published_evaluation": "preserve this report"}')
            original = published.read_bytes()
            command = [sys.executable, "-B", "-m", "src.ml_pipeline", "train"]
            result = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            model_path = root / "models/synthetic_rf_v1.joblib"
            runtime = root / "models/synthetic_rf_v1.manifest.json"
            self.assertTrue(model_path.is_file())
            manifest = json.loads(runtime.read_text())
            self.assertEqual(manifest["model_sha256"], ml.sha256(model_path))
            self.assertEqual(published.read_bytes(), original)
            hashes = (ml.sha256(model_path), ml.sha256(runtime))
            repeat = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertNotEqual(repeat.returncode, 0)
            self.assertEqual((ml.sha256(model_path), ml.sha256(runtime)), hashes)
            self.assertEqual(published.read_bytes(), original)
            prediction = subprocess.run([sys.executable, "-B", "-m", "src.ml_pipeline", "predict", "--ride", "Hulk Coaster", "--day", "Sat", "--hour", "12"], cwd=root, capture_output=True, text=True)
            self.assertEqual(prediction.returncode, 0, prediction.stderr)
            self.assertIn("Synthetic-model estimate", prediction.stdout)

    def test_existing_runtime_manifest_blocks_training_without_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            model_path = Path(temporary) / "new.joblib"
            runtime = Path(temporary) / "new.manifest.json"
            runtime.write_text('{"preserve": true}')
            with patch.object(ml, "MODEL", model_path), patch.object(ml, "RUNTIME_REPORT", runtime), patch.object(ml, "read_dataset") as reader:
                with self.assertRaises(FileExistsError):
                    ml.train()
                reader.assert_not_called()
            self.assertFalse(model_path.exists())
            self.assertEqual(runtime.read_text(), '{"preserve": true}')

    def test_existing_install_manifest_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "absent.json"
            with patch.object(ml, "RUNTIME_REPORT", runtime):
                self.assertEqual(ml.model_manifest_path(), ml.REPORT)
                runtime.write_text('{}')
                self.assertEqual(ml.model_manifest_path(), runtime)

    def test_present_bad_manifest_never_falls_back_to_published_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            model_path = Path(temporary) / "new.joblib"
            runtime = Path(temporary) / "new.manifest.json"
            published = Path(temporary) / "published.json"
            model_path.write_bytes(b"not deserialized")
            published.write_text(json.dumps({"model": "models/synthetic_rf_v1.joblib", "model_sha256": ml.sha256(model_path)}))
            runtime.write_text(json.dumps({"model": "models/synthetic_rf_v1.joblib", "model_sha256": "wrong"}))
            with patch.object(ml, "MODEL", model_path), patch.object(ml, "RUNTIME_REPORT", runtime), patch.object(ml, "REPORT", published), patch.object(ml.joblib, "load") as loader:
                with self.assertRaises(ValueError):
                    ml.load_new_model()
                loader.assert_not_called()

    def test_cli_rejects_invalid_hour(self):
        result = subprocess.run([sys.executable, "-B", "-m", "src.ml_pipeline", "predict", "--ride", "Hulk Coaster", "--day", "Sat", "--hour", "12.5"], cwd=ml.ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid int value", result.stderr)


if __name__ == "__main__":
    unittest.main()

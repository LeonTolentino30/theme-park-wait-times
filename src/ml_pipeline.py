"""Reproducible training on synthetic wait times. Never reads legacy models."""
from pathlib import Path
import argparse
import hashlib
import json
import platform
from numbers import Integral

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/raw/wait_times.csv"
MODEL = ROOT / "models/synthetic_rf_v1.joblib"
REPORT = ROOT / "reports/synthetic_rf_v1.json"  # Published evaluation; never rewritten by training.
RUNTIME_REPORT = MODEL.with_suffix(".manifest.json")
SEED = 42
FEATURES = ["ride", "day", "hour"]
RIDES = ("Forbidden Journey", "Hagrid's Motorbike", "Hulk Coaster",
         "Revenge of the Mummy", "Rip Ride Rockit", "Spider-Man",
         "Transformers", "VelociCoaster")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def input_frame(ride, day, hour):
    if not isinstance(ride, str) or ride not in RIDES:
        raise ValueError(f"ride must be one of {RIDES}")
    if not isinstance(day, str) or day not in DAYS:
        raise ValueError(f"day must be one of {DAYS}")
    if isinstance(hour, bool) or not isinstance(hour, Integral) or not 8 <= hour <= 22:
        raise ValueError("hour must be an integer from 8 through 22")
    return pd.DataFrame([[ride, day, int(hour)]], columns=FEATURES)


def read_dataset(path=DATA):
    df = pd.read_csv(path)
    if list(df.columns) != FEATURES + ["wait_time"] or df.empty or df.isna().any().any():
        raise ValueError("Expected nonempty, complete ride/day/hour/wait_time data")
    if set(df.ride) != set(RIDES) or set(df.day) != set(DAYS):
        raise ValueError("Dataset ride/day categories differ from reviewed synthetic schema")
    for row in df.itertuples(index=False):
        input_frame(row.ride, row.day, row.hour)
    if not pd.api.types.is_numeric_dtype(df.wait_time) or not np.isfinite(df.wait_time).all() or (df.wait_time < 0).any():
        raise ValueError("wait_time must contain finite, nonnegative numbers")
    return df


def make_pipeline(n_estimators=200):
    transform = ColumnTransformer([
        ("categories", OneHotEncoder(categories=[list(RIDES), list(DAYS)],
                                     handle_unknown="error", sparse_output=False), ["ride", "day"]),
        ("hour", "passthrough", ["hour"]),
    ], verbose_feature_names_out=False)
    return Pipeline([("features", transform),
                     ("regressor", RandomForestRegressor(n_estimators=n_estimators,
                                                         random_state=SEED, n_jobs=1))])


def splits(df):
    groups = df.groupby(FEATURES, sort=True).ngroup().to_numpy()
    indices = np.arange(len(df))
    random_split = train_test_split(indices, test_size=0.2, random_state=SEED)
    grouped_split = next(GroupShuffleSplit(n_splits=1, test_size=0.2,
                                          random_state=SEED).split(df, groups=groups))
    return groups, {"random_rows": random_split, "held_out_combinations": grouped_split}


def metrics(actual, predicted):
    return {"mae_minutes": float(mean_absolute_error(actual, predicted)),
            "rmse_minutes": float(np.sqrt(mean_squared_error(actual, predicted))),
            "r2": float(r2_score(actual, predicted))}


def evaluate(df, n_estimators=200):
    groups, partitions = splits(df)
    results = {}
    for name, (train, test) in partitions.items():
        x_train, x_test = df.iloc[train][FEATURES], df.iloc[test][FEATURES]
        y_train, y_test = df.iloc[train].wait_time, df.iloc[test].wait_time
        baseline = DummyRegressor(strategy="mean").fit(np.zeros((len(train), 1)), y_train)
        forest = make_pipeline(n_estimators).fit(x_train, y_train)
        results[name] = {
            "train_rows": len(train), "test_rows": len(test),
            "overlapping_feature_combinations": len(set(groups[train]) & set(groups[test])),
            "baseline_mean": metrics(y_test, baseline.predict(np.zeros((len(test), 1)))),
            "random_forest": metrics(y_test, forest.predict(x_test)),
        }
    return results


def train():
    # New artifacts are write-once; never overwrite the legacy model or previous runs.
    if MODEL.exists() or RUNTIME_REPORT.exists():
        raise FileExistsError("Runtime model/manifest already exists; choose a reviewed new version before training")
    df = read_dataset()
    results = evaluate(df)
    final_model = make_pipeline().fit(df[FEATURES], df.wait_time)
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    with MODEL.open("xb") as stream:
        joblib.dump(final_model, stream)
    report = {
        "dataset": "data/raw/wait_times.csv", "dataset_sha256": sha256(DATA),
        "model": "models/synthetic_rf_v1.joblib", "model_sha256": sha256(MODEL),
        "seed": SEED, "n_estimators": 200, "n_jobs": 1,
        "rows": len(df), "unique_feature_combinations": int(df.groupby(FEATURES).ngroups),
        "feature_names": final_model.named_steps["features"].get_feature_names_out().tolist(),
        "versions": {"python": platform.python_version(), "numpy": np.__version__,
                     "pandas": pd.__version__, "scikit-learn": sklearn.__version__,
                     "joblib": joblib.__version__},
        "evaluation": results,
        "final_fit": "All CSV rows; reported holdout scores belong to separate evaluation models.",
        "limitations": ["All observations are synthetic, not real-world wait times.",
                        "Random-row splits share repeated feature combinations; grouped scores keep combinations disjoint.",
                        "Grouped holdout is not temporal validation and does not test new rides or real observations.",
                        "No tuning or test-driven model selection was performed; these are single fixed holdouts."],
    }
    with RUNTIME_REPORT.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(report, indent=2))


def model_manifest_path():
    # Existing locally verified v1 installs predate the runtime manifest. Retain
    # their published-report hash check; never fall back if a manifest is present.
    return RUNTIME_REPORT if RUNTIME_REPORT.exists() else REPORT


def load_new_model():
    # Only this rebuild's fixed artifact is eligible; no arbitrary or legacy path.
    # A matching local report detects corruption, not authenticity of downloaded files.
    report = json.loads(model_manifest_path().read_text(encoding="utf-8"))
    if report.get("model") != "models/synthetic_rf_v1.joblib" or sha256(MODEL) != report.get("model_sha256"):
        raise ValueError("New model integrity check failed")
    return joblib.load(MODEL)


def predict_wait_time(ride, day, hour, *, model=None):
    frame = input_frame(ride, day, hour)
    if model is None:
        model = load_new_model()
    value = float(model.predict(frame)[0])
    if not np.isfinite(value) or value < 0:
        raise ValueError("Model returned an invalid prediction")
    return value


def main():
    parser = argparse.ArgumentParser(description="Synthetic-model wait estimates; not live park waits")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("train")
    predict = commands.add_parser("predict")
    predict.add_argument("--ride", required=True)
    predict.add_argument("--day", required=True, choices=DAYS)
    predict.add_argument("--hour", required=True, type=int, choices=range(8, 23))
    args = parser.parse_args()
    try:
        if args.command == "train":
            train()
        else:
            value = predict_wait_time(args.ride, args.day, args.hour)
            print(f"Synthetic-model estimate: {value:.6f} minutes (not a real-world wait time)")
    except (ValueError, FileNotFoundError, FileExistsError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()

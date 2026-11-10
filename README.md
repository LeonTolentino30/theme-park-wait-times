# Queue Atlas: Universal Orlando Wait-Time Explorer

A local Flask application with synthetic Random Forest wait estimates, a read-only current-waits dashboard, and historical charts for Universal Orlando. Predictions use simulated data and are not validated real-world forecasts. Real-data views require separately collected private ThemeParks.wiki observations; a fresh checkout does not include them.

## Data and scope

`data/raw/wait_times.csv` has 5,000 rows, eight rides, seven weekday abbreviations, and integer hours 8 through 22. There are 839 unique ride/day/hour combinations out of 840 possible. `src/generate_dataset.py` documents the generator: manually assigned ride popularity plus weekend and hourly effects and random noise. This rebuild reads the CSV unchanged; it does not rerun the generator. There are no real observation dates, weather, closures, holidays, or live crowd measurements.

## Reproducible setup (PowerShell)

Use Python 3.12 (validated with 3.12.7). Open PowerShell in this repository's root. Model binaries, virtual environments and real provider observations are intentionally excluded from Git.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m pip check

# Generate a fresh synthetic model and its local integrity manifest.
.\venv\Scripts\python.exe -B -m src.ml_pipeline train

# Run the complete offline test suite.
.\venv\Scripts\python.exe -B -m unittest discover -s tests -v

# Start only Flask; no collection is started.
.\venv\Scripts\python.exe -B app.py
```

Open http://127.0.0.1:5000/. Debug and the reloader are disabled. The model is trained only from the included synthetic CSV and reviewed source; no provider requests, private registry, API key or historical download are needed to train, test or use synthetic predictions. Tests use temporary databases and mocked providers.

Training writes `models/synthetic_rf_v1.joblib` and `models/synthetic_rf_v1.manifest.json`, both Git-ignored. It never rewrites the published evaluation at `reports/synthetic_rf_v1.json`. If either runtime artifact already exists, training refuses to overwrite it; do not rerun training on an existing working install. Partial runtime outputs also require manual review rather than automatic deletion/replacement.

Existing verified v1 installations without a runtime manifest continue using their original evaluation-report hash check. If a runtime manifest exists, the loader uses it and never falls back to the published report on integrity failure. No old untrusted model is loaded.

The real-data dashboard/history gracefully show unavailable or insufficient-data messages until an approved private attraction registry and separately collected observations exist. `private/` is not included in the repository. Collection is a separate, explicitly authorized action; starting Flask never starts the collector. See the provider and collection documentation below. Historical Git commits still contain the formerly tracked environment and old model; current setup does not use those files.

Optional CLI prediction:

```powershell
.\venv\Scripts\python.exe -B -m src.ml_pipeline predict --ride "Hulk Coaster" --day Sat --hour 12
```

The preserved legacy training scripts are historical: do not run `src/train_model.py`, which targets the old model filename. Use the reviewed module above. The Python entry point remains `from src.utils import predict_wait_time`; importing it does not load a model or train anything.

Inputs are exact, case-sensitive dataset ride names; weekdays `Mon Tue Wed Thu Fri Sat Sun`; and integer hours 8-22 inclusive. Strings, floats, and booleans are rejected as Python hour inputs. Unknown values, whitespace differences, and unsupported inputs are rejected rather than normalized. The preprocessing pipeline stores all 16 encoded features and their order together with the forest; no separate training-columns file is required.

Rides: Forbidden Journey, Hagrid's Motorbike, Hulk Coaster, Revenge of the Mummy, Rip Ride Rockit, Spider-Man, Transformers, VelociCoaster.

## Evaluation

All seeds are 42. The forest uses 200 trees and one worker; no tuning was performed. A mean-only baseline is fit on the training labels for each split. Preprocessing and model fitting occur separately inside each training partition. MAE and RMSE are minutes (lower is better); R^2 measures explained variance relative to a constant mean, not percentage accuracy (higher is better; negative means worse than the test-set mean).

| Holdout | Model | MAE | RMSE | R^2 |
|---|---|---:|---:|---:|
| Random rows | Training-mean baseline | 16.7085 | 20.4698 | -0.0005 |
| Random rows | Random Forest | 8.0044 | 9.8673 | 0.7675 |
| Unseen combinations | Training-mean baseline | 15.2101 | 18.9815 | -0.0053 |
| Unseen combinations | Random Forest | 8.2708 | 10.1408 | 0.7131 |

The random split uses 4,000 training and 1,000 test rows and shares 582 feature combinations. `GroupShuffleSplit` holds out 20% of unique ride/day/hour groups: 4,024 training and 976 test rows, with zero shared combinations. This is a harder test of unseen combinations within known ride/day categories. It is not a temporal forecast evaluation, an unseen-ride test, or evidence of real-world accuracy. Both holdouts are single fixed splits with no confidence intervals. Irreducible generator noise limits accuracy; repeated combinations are independent simulated draws rather than longitudinal observations.

After evaluation, a separate final model is fit to all 5,000 rows for inference. Reported scores belong to the holdout evaluation models, not to a test set kept aside from that final all-row model. Deterministic refitting is tested within the recorded environment; identical results across different library versions/platforms are not guaranteed.

## Artifacts and trust

- `models/synthetic_rf_v1.joblib`: freshly fit final preprocessing + forest pipeline. It remains ignored by the existing `models/` rule and must be regenerated when setting up a fresh checkout.
- `reports/synthetic_rf_v1.json`: published evaluation snapshot; training does not modify it.
- `models/synthetic_rf_v1.manifest.json`: generated local runtime metadata, integrity hash, evaluation and library versions for the newly trained model; ignored by Git.
- `models/wait_time_model.pkl`: preserved old artifact, never deserialized or overwritten by the new pipeline.

Joblib files can execute code. The new loader accepts only this rebuild's fixed v1 path and verifies its SHA-256 against the local runtime manifest (or the original verified report on existing v1 installs) before loading. That check detects accidental changes; it does not authenticate files if an attacker replaces both report and artifact. Only use artifacts generated locally from reviewed source. Never substitute downloaded or untrusted joblib files.

## Tests and next development step

Tests cover exact encoding order and values, all ride/day combinations at hour boundaries, invalid inputs before model loading, known metric calculations, disjoint grouped splits, deterministic fits/evaluation, fresh serialization roundtrips, integrity failures before deserialization, overwrite refusal, CLI errors/success, and agreement between the saved 200-tree model and an independently refitted model.

Before describing this as a real forecasting system, obtain licensed real historical observations with timestamps, document provenance and collection quality, and evaluate against a chronological holdout and suitable baselines. A web API and frontend can follow once that scope is agreed.

## Phase 2: local website

From PowerShell:

```powershell
# Open PowerShell in the repository root (the folder containing app.py).
.\venv\Scripts\python.exe -B app.py
```

Open **http://127.0.0.1:5000/**. Stop with Ctrl+C. The entry point explicitly binds to `127.0.0.1`, disables debug mode and the reloader, and loads the new Phase 1 model once. Do not use a public host binding. This is a local Flask development server, not a deployment setup. If port 5000 is occupied, stop the other local service before launching.

Flask and its required dependencies were installed with approval and pinned in `requirements.txt`. No ML dependencies were upgraded. A fresh environment needs the manifest installed with your approval and locally generated trusted Phase 1 artifacts. Do not replace the artifact with downloaded joblib files.

`GET /` serves the responsive frontend. JavaScript must be enabled for form submission. Selectors are rendered from the Phase 1 ride/day constants and hours 8 through 22. The frontend validates selections, prevents duplicate submissions, provides a 15-second request timeout, and displays accessible loading, error, and result feedback. All results carry the label **SYNTHETIC MODEL ESTIMATES, NOT LIVE PARK WAIT TIMES**.

`POST /api/predict` requires `Content-Type: application/json` and exactly these fields:

```json
{"ride": "Hulk Coaster", "day": "Sat", "hour": 12}
```

Successful response:

```json
{"predicted_minutes":89,"ride":"Hulk Coaster","day":"Sat","hour":12,"synthetic":true,"disclaimer":"SYNTHETIC MODEL ESTIMATES, NOT LIVE PARK WAIT TIMES"}
```

Minutes are rounded to the nearest whole minute, with halves rounded up. Server validation reuses Phase 1; unsupported values return HTTP 400 with an `error` message. Wrong field sets, malformed/non-object JSON, and non-JSON bodies also return 400. Requests over 4 KB return 413. Missing/corrupt artifacts or prediction failures return a sanitized 503; the page remains available. Visitors never receive exception text, filesystem paths, or model internals. There are no external fonts, image requests, accounts, or live feeds.

Run all Phase 1 and Flask endpoint tests:

```powershell
.\venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

The original data, notebooks, both model artifacts, and evaluation report remain preserved. The old artifact is never loaded by the website. The joblib/NumPy deprecation warning noted in Phase 1 may appear in local console output; it does not affect returned JSON. The local virtual environment and both model artifacts remain on disk but are excluded from the prepared repository tree.

## Phase 3B: provider preparation

A standard-library ThemeParks.wiki adapter and 20 mocked provider tests are available. The private proposed registry has seven verified active identities and one retired unresolved identity. It is separate from the synthetic website and model. See [provider documentation and proposed pilot](docs/themeparks_provider.md). Provider preparation itself never starts polling or creates a database. Collection requires separate explicit authorization. Provider records and the proposed registry are intentionally kept under Git-ignored `private/`.

## Phase 3C: finite SQLite pilot

A foreground, seven-day ThemeParks.wiki collector provides private SQLite storage, persistent backoff/deadline, duplicate prevention, and a read-only coverage report. It is operated separately from Flask; starting the website never starts collection. See [start, stop, resume and reporting instructions](docs/collection_pilot.md).

## Phase 4: stored Universal Orlando waits

The homepage includes **Current Posted Waits** from the collector's latest successful SQLite observations, with park grouping, status handling, timestamps, freshness labels, and ThemeParks.wiki attribution. The new `GET /api/current-waits` endpoint reads SQLite in read-only mode without calling the provider or interrupting the collector. [Dashboard behaviour, tests, and Flask-only restart instructions](docs/current_waits_dashboard.md).

## Phase 5: historical wait analytics

**Historical Wait Trends** plots real stored posted waits for the seven active attractions, with 6-hour, 24-hour and 7-day windows. The read-only `/api/history` endpoint validates attraction/date inputs; closures, missing waits and collection gaps break the chart. No values are interpolated or generated. [Viewing instructions and API details](docs/historical_analytics.md).

## Render deployment preparation

A production WSGI entry point, safe build helper and `render.yaml` are prepared but not deployed. The initial public mode serves synthetic predictions only; real-data sections remain explicitly unavailable and private observations are never uploaded or substituted. [Deployment commands, environment variables, limitations and approval steps](docs/render_deployment.md).

### Interactive attraction map

See [local map review and geographic provenance](docs/park_map.md) for the seven supported attractions, OpenStreetMap licensing, tile privacy, and local preview instructions. The map displays locations only; synthetic estimates remain a separate labeled demo.

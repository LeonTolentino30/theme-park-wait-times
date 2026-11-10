# Phase 7B: final verification report

Checked November 10, 2026. No commit, push, Render service creation or deployment occurred. The active collector and database were not modified. No design changes were made.

## Result

Prepared for a $0-compute public **synthetic portfolio demo**, with Linux verification still outstanding. Do not describe Windows checks as Linux/Gunicorn production evidence. No Linux environment is available: Docker is absent and WSL reports it is not installed. No Linux software/interpreter was installed and no external compute was provisioned.

## Render configuration validation

Parsed render.yaml with PyYAML and validated against the live official schema at https://render.com/schema/render.yaml.json using jsonschema. The initial unquoted `off` became YAML boolean false; changed it to `autoDeployTrigger: "off"` and updated the existing regression assertion. The corrected complete document validates successfully, with only a Free web service and no database/disk/worker. This is local validation against the official schema, not Render account/API acceptance or an actual deployment.

The configured Python version is 3.12.16, with build/start commands below. Both PYTHON_VERSION and .python-version agree. PORT is provided by Render and the server binds to 0.0.0.0. Auto-deploys are off; creating a service can still start its initial deployment.

## Tests and public behavior

All **123 tests passed**. The WSGI callable was separately verified under Windows Python 3.12.7:

- `/healthz`: 200, status only.
- `/`: 200 with clear synthetic-versus-unconfigured-observation labels and provider attribution.
- `/api/predict`: synthetic result 89 minutes for Hulk Coaster, Sat, hour 12.
- `/api/current-waits` and `/api/history`: sanitized 503 unavailable states. No private reads, no provider requests, no synthetic observations substituted.
- Twenty valid predictions at two threads passed; this was an in-process Flask client workload, not a Gunicorn/network benchmark.

Reviewed debug-off enforcement, 4 KB request bodies, strict prediction validation, generic errors, CSP/nosniff/no-store/referrer/HSTS headers, disabled public observed-data reads, and the 60/minute-per-worker prediction guard. The rate limiter is intentionally basic and resets on restart; it is not distributed abuse protection. Public mode never exposes the local raw/history feed, and has no environment variable that enables it accidentally.

## Dependency checks

All 14 direct pinned versions (including Gunicorn 26.2.0) exist on PyPI, declare Python constraints compatible with 3.12, and offer Linux or universal wheels. Their PyPI version metadata listed no vulnerability advisories at check time. This is metadata review, not a complete audit of transitive dependencies, a Linux pip installation, or proof that packages are vulnerability-free. Gunicorn was not installed/run on Windows. Validator tools were installed only in the previously approved isolated environment.

## Measured memory -- Windows only

| Measurement with OMP_NUM_THREADS=1 and OPENBLAS_NUM_THREADS=1 | MiB |
|---|---:|
| Loaded public WSGI RSS | 178.54 |
| After 20 predictions | 180.55 |
| WSGI peak working set | 194.91 |
| WSGI private committed memory | 150.11 |
| Isolated fresh-build sampled process-tree peak working set | 194.88 |
| Isolated fresh-build sampled process-tree private commit peak | 154.09 |

The fresh build completed successfully in about 5.4 seconds on this desktop. Initial measurement without numerical thread limits showed about 1,115 MiB Windows private commitment despite only about 197 MiB working set, supporting retention of both thread-limit environment variables. Windows private commitment, working set, and Linux cgroup usage are different metrics.

Render's Free instance is currently 512 MB RAM / 0.1 CPU. The measurements suggest a one-worker demo may fit, but do not certify it: Linux libraries, Python 3.12.16, Gunicorn master/worker overhead, allocation accounting, concurrent requests and the CPU quota were not measured. Desktop request/build timing is not Render timing. Keep one worker/two threads initially and verify real Linux peak memory and latency before declaring capacity. Do not automatically upgrade to paid compute if it fails.

## Remaining blockers

1. Run an isolated Linux check with Python 3.12.16: install requirements-render.txt, generate/verify artifacts, run all tests, start the exact Gunicorn command, and request health/home/prediction/unavailable endpoints over HTTP.
2. Measure the Linux process/container during build, cold start and two-thread predictions. Verify it stays safely within the Free instance memory budget and health/time limits. No such measurement has passed here.
3. Render account-side configuration/availability and usage/billing controls have not been accessed. A schema-valid blueprint does not guarantee successful provisioning.
4. Real-data publishing remains outside this demo: no private SQLite upload, registry upload, public feed or collected-history export. Enabling those requires a separate provider-terms/privacy review. The repository owner still has not selected a project LICENSE.

## Exact next steps for a $0-compute launch -- require separate approval

1. Obtain approval for a Linux verification route (for example a free public-repository CI job) before installing/provisioning anything. Do not install WSL/Docker or create a paid runner/service silently. Complete the outstanding Linux checks above.
2. After review and explicit authorization, commit/push the Phase 7 files. This phase did neither.
3. In a Free/Hobby Render workspace, create **Web Service**, connect the existing GitHub repository and main branch, select **Python** and explicitly choose **Free ($0/month)**. No paid workspace upgrade, database, disk or background worker is required. If payment details or a paid option is demanded, stop and review rather than accept charges.
4. Use Python 3.12.16 and the environment variables from render.yaml. Set the exact build/start commands below, health path `/healthz`, and disable automatic deploys. Creating the service/Blueprint can launch a deployment and must not happen before approval.
5. Inspect the first build, startup logs, health and memory/usage metrics. Expect synthetic predictions to work and observed-data endpoints to be unavailable. Do not copy private collector data to make those sections appear populated.
6. Keep usage within free allowances; monitor build minutes/bandwidth. Free compute is $0, not unlimited free usage. Current rules: 750 shared free instance-hours/month, 15-minute idle spin-down, cold-start delay, ephemeral runtime storage and no persistent disks. Without a payment method, excess allowances may suspend service/builds; adding payment details can permit overage billing. Do not enable billable add-ons or automatic paid upgrades.

Build:

```sh
pip install -r requirements-render.txt && python -B -m src.deployment_build
```

Start:

```sh
gunicorn wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 60 --access-logfile - --error-logfile -
```

No requests to ThemeParks.wiki occur during public app startup/serving. Model creation happens only in isolated/new build directories; existing model artifacts remain protected against overwrite. Render Free is suitable here as a hobby/portfolio demo, not an availability-guaranteed production service.

## Official references

- https://render.com/docs/blueprint-spec
- https://render.com/schema/render.yaml.json
- https://render.com/docs/web-services
- https://render.com/docs/compute-plans
- https://render.com/docs/free
- https://www.python.org/downloads/release/python-31216/
- https://pypi.org/project/gunicorn/26.2.0/
- https://www.themeparks.wiki/terms

# Phase 7A: Render preparation -- no deployment performed

## Release scope and readiness

The initial public release is a **synthetic prediction demo**. It preserves the current design and exposes no provider observations. The WSGI entry point forces observed-data reads off even if a private database is accidentally present. Current Posted Waits and Historical Wait Trends display explicit unconfigured/unavailable states; no synthetic rows substitute for observed waits. No collector is started by build or startup.

The configuration passed the current official Render JSON Schema in Phase 7B. Linux/Python 3.12.16/Gunicorn startup and Render-side build/deploy/resource measurements remain unverified; see [production verification results](production_verification.md). Gunicorn is UNIX-specific; the Windows Flask tests do not establish Linux server compatibility. No application package was installed into the active environment. YAML/schema validators were installed with approval into an isolated verification environment. The separate Render manifest pins Gunicorn 26.2.0, an established release; base Windows requirements are unchanged. Check current dependency security/release notes before the actual release.

Python security baseline: https://www.python.org/downloads/release/python-31216/ . Do not deploy the older local 3.12.7 patch merely to match the development machine. Verify the prepared 3.12.16 pin on Linux before deployment; this preparation does not upgrade the active interpreter or collector.

## Exact commands and configuration

Build (Render Linux shell, project root):

```sh
pip install -r requirements-render.txt && python -B -m src.deployment_build
```

Startup:

```sh
gunicorn wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 60 --access-logfile - --error-logfile -
```

`wsgi:application` imports the Flask factory with PUBLIC_DEPLOYMENT enabled. `python app.py` remains the local-only 127.0.0.1 development command; do not use it as Render's production server. Render supplies PORT. Gunicorn listens on the assigned interface/port behind Render's HTTPS proxy. One worker/two threads is a conservative initial memory choice, not a load-tested capacity promise.

`render.yaml` declares a Python web service, free compute, health check `/healthz`, and `autoDeployTrigger: "off"`. Preparing or committing this file does not deploy it. Creating a Blueprint can initiate its first deployment even with automatic later deploys off; do not create a service/Blueprint until separately authorized. No paid resource, disk, database or background worker is requested.

Environment variables:

| Variable | Value / purpose |
|---|---|
| PYTHON_VERSION | 3.12.16, the current Python 3.12 security release; also recorded in .python-version. Local verification still uses the preserved 3.12.7 interpreter |
| PORT | Supplied by Render; do not hardcode a local-only port |
| PYTHONUNBUFFERED | 1, stream logs |
| OMP_NUM_THREADS | 1, bound numerical-library thread use |
| OPENBLAS_NUM_THREADS | 1, bound numerical-library thread use |

No API key, Flask secret key or private-database credential is required: the demo uses no sessions/accounts or provider calls. Do not add private registry/database uploads or secret environment files. DEBUG is forced false in the production factory configuration regardless of Flask environment flags. There is deliberately no environment switch to enable real observations in this release; changing that requires reviewed code and data-use approval.

## Model artifacts and filesystem behavior

On a clean deployment build, `src.deployment_build` calls the existing protected trainer. It generates the model and runtime manifest from the committed synthetic CSV. The published evaluation report stays untouched. Training is never run in a request or at worker startup.

If both generated artifacts exist, the build helper checks their model/data SHA-256 metadata and reuses them without deserializing or overwriting them. Partial artifacts, mismatched data, and bad hashes fail closed. For an intentionally new model/version, use a reviewed clean Render build/cache workflow; the helper never deletes or replaces files. Do not run the build helper against the existing local model that predates runtime manifests; preserve that install and test builds in an isolated checkout instead.

Generated model artifacts are included in that deployment's built filesystem, not in Git. Runtime filesystem writes are ephemeral on Render; never rely on local SQLite there for a durable collector. A restart uses the deployed build artifacts, while runtime-created files may disappear. No collector data is copied from the local machine. Free web services cannot attach persistent disks; a future real-data architecture requires separate design and approval.

## Public endpoint review

- GET `/`: synthetic form plus clearly unconfigured observed-data sections; static assets are served by Flask/Gunicorn without a CDN dependency.
- POST `/api/predict`: existing strict ride/day/integer-hour validation and 4 KB request limit. Public mode limits prediction requests to 60/minute per worker using a thread-safe in-memory global window; 429 includes Retry-After. This is a basic single-instance resource guard, not distributed per-user abuse protection. It resets on restart; scaling would require shared edge/application limits. No client IP or forwarded header is trusted for identity.
- GET `/api/current-waits` and `/api/history`: sanitized 503 unconfigured responses in public mode, without any private reads. They are not provider-data redistribution endpoints on this release.
- GET `/healthz`: status only, 200 with a loaded model and 503 otherwise. It does not expose model metadata or filesystem paths.

Exceptions stay generic to visitors. CSP/nosniff/no-store headers remain; public mode adds HSTS and no-referrer. There are no uploads, sessions, admin endpoints, credentials, polling, or collection tasks. The old untrusted model is never loaded. Existing real-data readers retain short read-only SQLite behavior for local use, outside this public mode.

## Provider terms, privacy and licensing

Reviewed November 10, 2026: https://www.themeparks.wiki/terms . The provider permits user-facing value-added apps and derived forecasts with linked attribution, while prohibiting feed mirroring, re-API services and substantial bulk redistribution. Attribution alone does not grant redistribution rights. The existing raw-point/history JSON endpoints were designed for a local app; do not enable them publicly or upload collected observations without a fresh review of downstream access and, where necessary, written provider permission. Keep linked Powered by ThemeParks.wiki credit in the observation sections and preserve all synthetic-vs-observed labels. Do not imply Universal/provider endorsement.

The repository still has no project LICENSE. The owner should choose a licence before promising third-party reuse; this is separate from permission to display provider data. Dependency licences and attribution must also be retained. Historical Git commits still contain the formerly tracked environment, old model and machine paths; this preparation does not erase them. No private observations are included in the deployment tree.

## Render plan limitations and next approval

Reviewed official docs: https://render.com/docs/deploy-flask , https://render.com/docs/python-version , https://render.com/docs/free , https://render.com/docs/blueprint-spec . Pin Python rather than taking Render's changing default. Free services spin down after 15 idle minutes and may take about a minute to wake; 750 free instance-hours/month are shared across the workspace. Bandwidth/build-minute allowances and potential overage billing/suspension still apply. Free compute has no persistent disks, no shell access and no one-off jobs; do not treat a free web service as an always-on collector or production SLA. Verify current pricing/quotas before creating a service.

Before approval to publish/deploy: review this diff, confirm the Free plan, verify Linux dependency installation and Gunicorn health/startup, and assess Linux memory usage (training/importing sklearn and a 200-tree model may stress small instances). No deployment has been created or accessed. No account credentials are needed for preparation. Actual deployment may require connecting GitHub and accepting Render's service/billing terms, which is a separate action.

Tests:

```powershell
.\venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

New tests cover public privacy/debug controls, absent observations, health, request limits/errors and non-overwriting build behavior. The collector process/database are not touched by production tests.

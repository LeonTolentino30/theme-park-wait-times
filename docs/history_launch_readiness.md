# Phase 8P: historical launch readiness

Local verification only. No Git publication, Render settings change, deployment, cloud write or collector access is performed by this phase.

## Verified behavior

The production WSGI path securely retrieves the private manifest and immutable snapshot using only the read identity. Snapshot checksum/schema/mappings are validated before charts render. The approved snapshot contains 602 real observations across seven attractions; all 21 attraction/window combinations render. Coverage is November 8, 2026 19:43:55.693279 UTC through November 10, 2026 19:39:38.614260 UTC. Snapshot generation is November 11, 2026 03:07:13.542339 UTC. Coverage is discontinuous, including a roughly 39-hour gap. The 7-day control therefore shows incomplete history, not seven days of continuous samples.

Public windows are archive-only: 6, 24 or 168 hours ending at the last recorded observation. Their headings, coverage and generation dates are shown in America/New_York. Closed, DOWN, missing and gap states break the chart; zero remains numeric. No interpolation is used. The selector derives from the seven-attraction registry independently of unavailable current waits. Private/local and synthetic fixture demos retain their existing controls.

Public /api/history remains disabled. HTML/SVG is human-facing display, not a raw JSON/download feed; it still necessarily conveys plotted values to the visitor. Linked ThemeParks.wiki credit remains visible. There is no collector SQLite fallback in production.

## Secure local verification

From the project root, run:

```powershell
powershell -NoProfile -File tools/Test-QueueAtlasHistoryReadiness.ps1
```

This imports the existing Windows DPAPI credential bundle outside OneDrive, passes only reader keys to a short-lived child, removes inherited publisher variables, and enables history only inside that child. It uses a Flask test client (no listening web server), blocks SQLite and saves only aggregate verification results in the protected local QueueAtlas credentials directory. It makes no cloud writes and exports no observation file. The command is Windows-specific; Render does not use DPAPI or this launcher.

## Future Render configuration -- separate approval required

Use the existing Free web service; do not create a database, disk, worker or collector. Existing commands remain:

```sh
pip install -r requirements-render.txt && python -B -m src.deployment_build
gunicorn wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 60 --access-logfile - --error-logfile -
```

Set these through Render's secret settings, never source/Git/chat:

- QUEUE_ATLAS_HISTORY_ENABLED=1 (only at separately approved activation)
- QUEUE_ATLAS_R2_ACCOUNT_ID: existing account ID
- QUEUE_ATLAS_R2_BUCKET=queue-atlas-history
- QUEUE_ATLAS_R2_READ_ACCESS_KEY_ID: bucket-scoped Object Read identity
- QUEUE_ATLAS_R2_READ_SECRET_ACCESS_KEY: its secret

Never configure either QUEUE_ATLAS_R2_PUBLISH_* variable on Render. Enabled production configuration now refuses them, including empty entries. Preserve numerical-thread limits OMP_NUM_THREADS=1 and OPENBLAS_NUM_THREADS=1, debug disabled, one worker/two threads, and autoDeployTrigger off. Do not upload the local project directory or private files as build inputs; use a reviewed Git checkout.

The cache is memory-only, bounded and lock-protected, with five-minute conditional refresh on requests; there is no polling job. A restart/cold start retrieves the private pair again. Storage failures show a sanitized unavailable message and cooldown rather than fabricated or unvalidated data. Render Free's ephemeral disk is not needed for history. Its idle sleep/cold-start and outbound traffic/usage limits still apply. Linux memory/performance for this added path has not been measured here; the actual snapshot is only 176,510 bytes, but decoded copies and concurrent SVG generation have additional overhead. Earlier Windows memory measurements are not Linux proof.

Rollback: disable QUEUE_ATLAS_HISTORY_ENABLED and restart only the website after approval. Leave the collector and private objects untouched. Restoring an older manifest additionally requires a worker restart because the cache rejects generation rollback.

## Rights and launch gates

Current ThemeParks.wiki terms expressly permit human-facing dashboards and analytics with linked attribution, while prohibiting re-APIs, bulk feeds and third-party embedded widgets. Do not add raw downloads or a public snapshot URL. Private bucket settings remain as manually verified by the owner; S3 read credentials cannot independently attest account management configuration. No settings were changed.

Before launch: review and approve the entire accumulated historical-integration Git diff, publish the code separately, approve Render read-only secret configuration and manual deployment/activation, then verify health, one prediction, all chart controls, unavailable current waits and disabled raw endpoints on the actual service. No step has been performed by this document. Do not print credentials in build/runtime logs.

References: https://www.themeparks.wiki/terms and https://render.com/docs/free .

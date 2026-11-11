# Phase 8H: fixture-only private history architecture

## Scope and safety

This phase never reads the real collector database, exports real observations, contacts ThemeParks.wiki, creates cloud resources, or changes the collector. `tests/fixtures/history_snapshot_v1.json` contains invented records for seven existing public attraction identities. They are not actual ThemeParks.wiki data. `dataset_kind=synthetic_test_fixture` is mandatory; the loader rejects any production dataset kind. The fixture file is outside Flask static routes.

Default `app.py` local history continues using the existing local SQLite/JSON/chart path. Default `wsgi.py` public mode remains unchanged and does not enable fixture history or private observations. No deployment configuration is changed. Explicit `HISTORY_SNAPSHOT_STORE` injection enables the new server chart path and always disables the SQLite/current-waits paths, even if the app configuration otherwise requests local observations. This prevents fallback from fixtures to real data.

## Local demonstration (no database access)

From the project root, in a separate terminal:

```powershell
.\venv\Scripts\python.exe -B -m src.history_fixture_demo
```

Open http://127.0.0.1:5001 and scroll to Historical Wait Trends. Do not stop or restart the collector or the existing website. The demo binds only to loopback, uses no reloader, and disables debug mode. Choose a ride, 6 hours / 24 hours / 7 days, and Archived fixture observation period. Select Show wait trends. The fixed invented fixture covers November 1, 2026, including the New York DST fallback. Switching to Current time uses a genuinely rolling window; if the fixture is outside that window, the message reports no observations rather than silently shifting dates.

All fixture charts, tooltips, and the section itself identify invented test data. ThemeParks.wiki attribution remains visible, with an explicit statement that fixture values were not provided by ThemeParks.wiki. No raw-observation arrays are returned to the browser. This opt-in demo also shows the existing synthetic predictor and map; map tiles still require user consent.

## Snapshot v1 contract

The root has exactly: `schema_version` (integer 1), `dataset_kind` (synthetic_test_fixture), `source` (ThemeParks.wiki, describing the future integration), `generated_at`, `coverage_start`, `coverage_end`, and `records`.

Each record has exactly `attraction_id`, `observed_at`, `provider_last_changed_at` (nullable), `status`, `wait_minutes` (nullable), `present` (boolean), and `quality_flags`. Fetch/observation time is distinct from provider last-change time. Times must be valid UTC whole-second ISO timestamps ending in Z. Coverage is at most seven days, observations lie inside it, generation is not before coverage, and future-generated snapshots cannot be displayed. IDs must exist in a reviewed public registry. Duplicate attraction/time pairs, non-integer or negative waits, unsupported states/versions, unknown fields, duplicate JSON keys and nonfinite values are rejected. V1 accepts waits through 1,440 minutes as an explicit defensive bound; a larger value must trigger review rather than silent truncation. Recognized quality flags are missing_entity, missing_wait, unknown_status and stale_provider.

Size bounds: 8 MB per object and 20,000 observations per snapshot. These limits are intentional initial bounds, not an unlimited archive service. The validator takes a registry parameter so more reviewed attractions can be added without a snapshot-format change; the live application's registry remains the existing seven.

JSON is canonicalized (sorted keys, compact separators, UTF-8). Immutable object keys are `history/v1/<sha256>.json`; differing content gets a different key. A small manifest supplies the version, fixture kind, exact key, SHA-256 and byte length. The loader validates these before use and revalidates the entire snapshot. Hashes detect changes relative to a trusted manifest; they do not authenticate a malicious storage operator or someone who can replace both manifest and object.

## Storage and rendering

`SnapshotStore.get(key) -> bytes` is a deliberately narrow interface. Only a read-only in-memory implementation exists now. There is no R2 SDK, credential lookup, cloud write method, database exporter, or arbitrary path/URL parameter. The reviewed fixture loader reads only its fixed fixture path.

`GET /history/chart?attraction_id=<reviewed UUID>&hours=6|24|168&mode=archive|rolling` returns person-facing HTML/SVG. Query strings are limited to 512 bytes. It accepts no arbitrary dates, raw format, bulk selection or duplicate parameters. It does not return public observation JSON. The existing `/api/history` stays disabled in public and fixture modes; only the existing ordinary local app retains its private local JSON functionality.

Server-side rendering retains the chart geometry, styles and event treatment. CLOSED, DOWN, REFURBISHMENT, UNKNOWN, UNAVAILABLE, absent observations and missing waits break the line, even if a closed row contains a numeric wait. A genuine present OPERATING zero remains zero. Quality-marked stale-provider records are not plotted as numeric waits. Gaps exceeding 450 seconds break the line; no interpolation, carry-forward, synthetic gap rows or inferred closure periods are created. At least two numeric times are required. The server labels every date in America/New_York with EST/EDT. Archived windows explicitly end at the snapshot coverage boundary; rolling windows end at current UTC time. Requested bounds and actual displayed sample bounds are separate. Coverage older than 30 minutes is labeled historical/not live. Snapshot generation time is shown independently.

The history selector uses public attraction metadata, independently of current-waits availability. Existing local chart controls and chart behavior remain supported. The fixture branch in history.js requests only the HTML chart fragment; the default branch remains the existing local chart implementation.

Rate limiting is a bounded, thread-safe global allowance of 30 chart requests/minute per process, including invalid requests. This is an initial single-worker Free-service resource bound, not distributed abuse prevention or permission to redistribute data. Responses use no-store, the existing CSP and frame-ancestors none. Storage/integrity failures return a generic 503; invalid inputs return 400 and limits return 429 with Retry-After. User-controlled strings are escaped in HTML and SVG. No bucket URLs, internal paths, credentials, raw responses, provider timestamps, or source records are exposed as an API. Displayed chart values can still be read or reconstructed; SVG is not a secrecy mechanism.

## Proposed R2 integration - NOT IMPLEMENTED OR ACTIVATED

After separate approvals:

1. Create a private R2 Standard bucket with public access disabled. Approve any billing/account enrollment first. No real snapshot belongs in public Git or static assets.
2. Use a publisher identity with only the required object write permissions, retained locally. Use a separate bucket-scoped Object Read identity in Render environment variables. Never put either key in the browser, repository, logs, or fixture file. R2 Object Read permissions may include listing, so do not claim finer permissions than the provider supplies.
3. Build a bounded, authenticated R2 adapter. Use a fixed configured bucket/endpoint, connection/read deadlines, an 8 MB streaming cap, no caller-supplied storage paths, and bounded retry/backoff for outages. Validate manifest and digest before rendering. Do not add a raw object proxy or public presigned download links.
4. Develop an explicitly approved read-only SQLite extraction plan using short WAL-aware reads from successful runs, then release the read transaction before processing/upload. Approve the allowed attractions, fields, date range, quality rules and retention period. Exclude runs.raw_body, request headers, logs and machine paths. Do not copy a live SQLite file while ignoring its WAL.
5. Add an authenticated production dataset kind only after provider/data approval; current code intentionally refuses it. Validate with a local private dry run before uploading anything.
6. Publish immutable objects conditionally (do not overwrite an existing object under a content-addressed key), validate upload, then update the manifest atomically. Cache one validated bounded snapshot per worker; use manifest ETags and a short refresh TTL on chart requests. Render restarts reload from private storage, not an ephemeral database. On stale or failed refresh, label an approved last-known-good snapshot honestly or show unavailable; never invent records.
7. Initially publish manually. A later separate publisher could refresh every 15 minutes while approved collection is active; it must not extend the seven-day collector or create a scheduled task without approval. A stopped pilot produces an archive, not fresh observations. Request-driven Render reads need no background poller or recurring deployment.
8. Run fixture, Linux/WSGI, security and private integration verification. Separately approve Git publication and Render configuration/deployment changes. Keep real observations out of Git throughout.

Estimated incremental cost is $0/month at this pilot's scale within current R2 Standard allowances: 10 GB-month storage, 1 million Class A operations and 10 million Class B operations per month, with free egress. At seven rides/five-minute cadence, a theoretical seven-day ceiling is 14,112 ride records before outages/backoff, compatible with the initial limits. These are estimates, not a database audit. R2 is metered beyond its free allowances and may require billing enrollment; Render's existing bandwidth/build allowances also still apply. No account, billing action or resource has been created.

Official references reviewed for the architecture: https://developers.cloudflare.com/r2/pricing/ , https://developers.cloudflare.com/r2/api/tokens/ , https://render.com/docs/free . Recheck pricing and credential capabilities before provisioning.

## Licensing and approvals remaining

Current ThemeParks.wiki terms expressly permit user-facing dashboards/analytics with linked attribution, but prohibit feed/API/bulk redistribution: https://www.themeparks.wiki/terms . This chart-only design follows that distinction; rendering does not independently confer redistribution rights. Exact approval or written clarification is still needed if a public numerical observation API, bulk export, third-party widget, broader retention/distribution arrangement, or another form of downstream data delivery is proposed. Do not silently re-enable public /api/history.

Before real access, obtain owner approval for a concrete read-only extraction scope and quality/retention plan. Before cloud use, obtain approval for the provider account, private bucket, billing terms, credential separation, allowed uploaded data and region. Before publication/deployment, obtain separate Git/Render approval. Request approval for any new dependency installation. No real database inspection, export, upload, production dataset activation or deployment is authorized by this fixture-only phase.


## Phase 8J compatibility update

[Exact timestamp and fixture-only extraction preparation](history_extraction.md) supersedes the original whole-second-only restriction above. Both v1 and v2 now preserve UTC whole/fractional timestamps with up to six digits, using Z or +00:00. V2 adds a required matching park_id. Existing v1 fixture checksum behavior is unchanged. The new extractor is gated to owned temporary synthetic databases; no real-source path, real snapshot activation, or cloud integration is enabled.


## Phase 8K local collected-data opt-in

[Authorized private snapshot generation and preview](history_real_snapshot.md) adds an explicitly gated collected_observations kind for v2 only. Default loading/public deployment still refuses real snapshots. Only the loopback-only private preview may enable them; fixture labels remain unchanged in fixture mode. No public JSON feed or cloud integration is enabled.

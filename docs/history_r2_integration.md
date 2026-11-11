# Phase 8L: private R2 reads and production charts (NOT ACTIVATED)

This is local implementation and fixture-based testing only. No real collector database or snapshot is read, no credentials are loaded for actual use, no R2 request is made, and no account, bucket, upload, paid service, Git publication or deployment is performed. The Phase 8K bundle is not touched. Default production history remains disabled.

## Architecture

Future private R2 manifest/object GET -> checksum/schema validation -> one bounded in-memory snapshot cache per worker -> existing validated history-window logic -> server-rendered HTML/SVG chart. The browser receives chart HTML only; bucket keys, storage addresses, credentials, manifests and observation JSON are not returned. Chart values remain human-readable/reconstructable; server rendering is not an access-control or licensing workaround.

R2ReadOnlyStore implements the S3-compatible object GET subset with SigV4 (region auto) using Python's standard library. Its signer is tested against AWS's public S3 signing vector, whose example credentials are not actual secrets. No SDK/package installation is needed. Hostnames are constructed only from a validated 32-character account ID, with the fixed r2.cloudflarestorage.com suffix. There is no caller-supplied URL/host, redirect following, upload, listing, delete, public URL or presigned-download functionality. Server TLS verification stays enabled and proxy environment settings are not used.

The bucket must remain private with public access disabled and no browser CORS/data-download configuration. Render must receive a separate bucket-scoped Object Read identity, not the publisher's write credentials. Some provider Object Read permissions also allow listing; the application does not use that capability. Code cannot establish a credential's granted permissions without provisioning/access checks; least-privilege configuration remains a future operator responsibility.

Only manifest.json and immutable history/v1-or-v2/SHA256.json objects are accepted. Manifests are capped at 16 KiB and objects at 8 MB. Responses stream with a maximum size; unexpected compression, oversized/truncated bodies, redirects, bad statuses, invalid ETags and malformed JSON fail closed. Connection/socket timeout is two seconds, with a four-second elapsed body/request budget per object. Operating-system DNS resolution is not independently interruptible with this standard-library approach; a real Linux/network test remains required. Cold reads can need two sequential requests and must be checked against the frontend's ten-second timeout.

## Cache behavior

- No startup network I/O, polling thread, scheduled job, disk cache or SQLite fallback.
- First chart request retrieves manifest and object; checksum, byte count, canonical encoding, schema/version agreement, UTC precision, duplicate detection, attraction/park mappings and dataset kind are validated before adoption.
- Production accepts collected_observations; synthetic_test_fixture is accepted only through an explicit fixture-test/demo constructor seam, never through environment configuration.
- Default cache TTL is 300 seconds, bounded to 30-900 in code. Hot requests reuse validated bytes and return a copy, preventing callers from corrupting cache state.
- Refresh uses a signed If-None-Match manifest request. HTTP 304 is accepted only with a prior validated snapshot. Unchanged immutable content keys reuse already validated object bytes. A new manifest/object replaces the cache only after full validation. Registry changes force revalidation.
- Future-generated snapshots and generation-time rollback relative to a newer cached snapshot are rejected. Old historical coverage is allowed, but explicitly archived/labeled, never described as live.
- 429/503 Retry-After is respected (seconds or HTTP date), with a minimum five-second cooldown; other failures default to 30 seconds. There are no automatic retry loops. During a failed refresh/cooldown, the endpoint returns generic 503/Retry-After rather than quietly serving the old snapshot. Prior validated bytes remain only for a later conditional recovery.
- A lock prevents duplicate concurrent cold fetches. The cache is per worker and is lost on restart; private R2 is the eventual persistent source. One worker remains the initial Render configuration. Schema hashes assume a trusted private manifest/store, not a cryptographically authenticated publisher.

Snapshot generation time, coverage beginning/end, requested archived window and actual displayed sample bounds remain distinct. Z/+00:00 fractional timestamp strings are retained exactly. True operating zeroes remain numeric; CLOSED/DOWN/missing values and unrecorded gaps break the line. No interpolation, carry-forward or synthetic substitutions occur. Requested 6-hour, 24-hour and 7-day windows and seven approved ride choices remain intact. Source data may cover only part of a window; incomplete-history labels remain visible.

## Explicit environment configuration (future approval only)

| Variable | Purpose |
|---|---|
| QUEUE_ATLAS_HISTORY_ENABLED | Must explicitly equal 1 to activate; defaults to 0 |
| QUEUE_ATLAS_R2_ACCOUNT_ID | Reviewed Cloudflare account ID; no arbitrary endpoint |
| QUEUE_ATLAS_R2_BUCKET | Private bucket name |
| QUEUE_ATLAS_R2_READ_ACCESS_KEY_ID | Bucket-scoped read credential ID |
| QUEUE_ATLAS_R2_READ_SECRET_ACCESS_KEY | Matching read secret; Render secret setting only |

No actual values are included in code, documentation, manifests or browser responses. Do not paste credentials into Git or logs. production_config reads no credential variables when history is disabled. Enabled-but-incomplete configuration fails startup generically without printing values; storage outages after valid configuration affect charts, not model health/predictions/map. wsgi.py uses this default-off factory. render.yaml is unchanged, with automatic deployment still off; no environment flag has been set.

HISTORY_PRODUCTION_ENABLED requires PUBLIC_DEPLOYMENT and a ValidatedSnapshotCache, cannot combine with HISTORY_PRIVATE_LOCAL or an injected local snapshot store, and forces observed SQLite access off. Public /api/history and /api/current-waits stay unavailable; only /history/chart serves HTML/SVG. Input/query bounds, 30 requests/minute per worker, no-store, frame-ancestors none and sanitized errors remain in place. No route proxies arbitrary bucket objects.

## Fully offline local demonstration

From the project root:

```powershell
.\venv\Scripts\python.exe -B -m src.history_production_fixture_demo
```

Open http://127.0.0.1:5003. Choose a ride, 6 hours / 24 hours / 7 days, and Archived fixture observation period. All values are invented test fixtures and labeled accordingly. The demo exercises the production cache/chart integration using an in-memory fake object store, not R2, credentials, real snapshots or SQLite. Debug/reloader are off and the server binds only to loopback. Existing fixture/private local previews and the normal local SQLite chart path remain separate and functional.

## Tests, limits and remaining approval gates

Tests use fake credentials, invented fixtures and mocked HTTPS only; real observation files/database paths and outbound sockets are blocked during the full suite. Coverage includes the official signing vector, size bounds, redirect refusal, authentication errors, HTTP status redaction, ETags, TTL, concurrent refresh, invalid manifests/checksums/schemas, time rollback, outages/backoff, all 21 chart combinations, JSON isolation and default-off activation. Production-shaped fixtures are explicitly invented even when their test dataset-kind field exercises collected-data branches. Browser visual and live R2/Linux interoperability testing remain outstanding; mocked compatibility must not be reported as a successful cloud integration.

Before activation: approve a private cloud account/bucket and billing terms, set bucket-scoped read/publisher credentials separately, approve exactly which real snapshot may be uploaded and its retention/region, resolve the existing OneDrive/device-only-storage concern independently, and approve any required real integration test. Then separately approve Git publication, Render environment changes and deployment. No uploader is implemented in this phase. A later publisher should upload immutable objects first and update the manifest last, without altering or restarting the collector. Public display must stay within ThemeParks.wiki product-display permission, never a feed/API/bulk export.

Expected incremental cost at pilot scale is $0 within R2 Standard free allowances, but usage is metered and account/billing activation needs approval; Render bandwidth/resource allowances still apply. Recheck prices before provisioning. Official references reviewed: https://developers.cloudflare.com/r2/api/s3/api/ , https://developers.cloudflare.com/r2/api/tokens/ , https://developers.cloudflare.com/r2/pricing/ , https://docs.aws.amazon.com/AmazonS3/latest/developerguide/sig-v4-header-based-auth.html , https://www.themeparks.wiki/terms .

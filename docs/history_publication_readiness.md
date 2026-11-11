# Phase 8N: historical snapshot publication readiness (NO PUBLICATION)

The dry-run planner has no HTTP client, upload method, credential loader or SQLite connection. It validates the existing local v2 bundle, checks the explicitly approved digest, and returns metadata/order/rollback guidance only. Tests use invented fixtures. No real snapshot is copied into source or a build artifact.

## Bucket privacy verification

The official Cloudflare configuration GET endpoints are /accounts/{account_id}/r2/buckets/{bucket_name}/domains/managed and /domains/custom. The former returns the r2.dev enabled flag; the latter returns custom domains and their enabled flags. These require Cloudflare API bearer authentication. The existing saved credential sets contain only S3 Access Key IDs and Secret Access Keys, not a management bearer token. S3 secrets must not be repurposed as bearer tokens. The owner confirmed no management token is available, so authenticated public-surface verification is blocked. No account/token/bucket permission changes or unauthenticated guesses are made. Prior anonymous S3 denial does not establish that r2.dev/custom domains are disabled.

Before upload, obtain authenticated read-only configuration evidence showing r2.dev disabled and no enabled custom-domain public surfaces. A read-only management session/token can be supplied securely in a later authorized step; do not paste it into chat or source, or broaden existing publisher/Render permissions automatically.

## Exact-key synthetic cleanup

The isolated connectivity helper has an explicit -CleanupPhase8M mode. It is hardcoded to connectivity-tests/phase8m/5572205a51074e0fb76948a2afc608b4/probe.json. It first verifies the expected synthetic bytes with a signed read, deletes only that key with the publisher identity, then requires a signed 404 NoSuchKey. A replaced object is not deleted. No bulk listing/deletion, historical-object operation or bucket-setting change is implemented.

## Proposed publication (approval required)

Use src.history_publication_plan.plan_from_store with the existing local PrivateLocalStore. The current immutable key is determined by the manifest's approved SHA-256 under history/v2/. The only additional active key is manifest.json at the private bucket root. The aggregate report, original database, raw provider responses, local file paths, credentials and fixture files are not upload inputs.

1. Resolve privacy verification and obtain explicit approval for the specific digest, two keys, region/retention and real-data upload. Keep public history disabled.
2. Read and preserve any existing active manifest and ETag. Do not assume the bucket has no manifest merely because the synthetic probe was cleaned up.
3. Create the content-addressed snapshot with If-None-Match: *. If it exists, stop and verify exact approved content before deciding whether to reuse it; never overwrite differing content.
4. Read back with the Render read-only identity and verify byte length, SHA-256, schema and mappings.
5. Publish the manifest LAST, conditionally: If-None-Match: * for first publication, or If-Match against the backed-up active ETag for an update. A precondition failure stops the workflow. Cloudflare documents atomic conditional PutObject evaluation.
6. Read back and validate the manifest/object pair. This publishes only to private storage, not the public website.
7. Obtain separate Git publication and Render configuration/deployment/activation approvals. Only then set the history flag and verify charts/labels, raw JSON denial and no SQLite access.

## Rollback

Keep previous manifest bytes and its referenced immutable object. Disable QUEUE_ATLAS_HISTORY_ENABLED and restart only the website workers for an urgent display/privacy rollback, after appropriate approval. Restore a previous manifest conditionally against the current ETag if approved. Existing workers reject a snapshot generated earlier than the one in their cache; TTL expiry alone is insufficient, so restart only Flask/Gunicorn workers to clear those caches. Never change source timestamps merely to bypass this protection. For a first publication without a predecessor, leave history disabled rather than manufacturing a rollback manifest. Real objects remain private; deleting them requires separate retention/cleanup approval. The collector is never stopped/restarted.

## Render/build configuration

Required future variables: QUEUE_ATLAS_HISTORY_ENABLED (0 until separate activation approval), QUEUE_ATLAS_R2_ACCOUNT_ID, QUEUE_ATLAS_R2_BUCKET=queue-atlas-history, QUEUE_ATLAS_R2_READ_ACCESS_KEY_ID, QUEUE_ATLAS_R2_READ_SECRET_ACCESS_KEY. Enter actual read credentials through Render secret settings, never Git/files/logs. Publisher keys must not enter Render. No variable values or Render settings are changed in this phase.

A reviewed Git-based Render checkout contains source, synthetic CSV and evaluation material, not ignored private files. .gitignore excludes private/, environment files, credentials, databases, model binaries and virtual environments. Existing Windows-DPAPI credentials and secret-free probe reports are outside the repository/OneDrive. The build helper generates/verifies the synthetic model from code/CSV; it does not copy snapshots or read SQLite. Runtime history reads private R2 only after explicit activation. Validate the actual future source/build inventory again before publication; do not upload the local working directory wholesale or copy ignored private data into an artifact. Review environment variables/build logs during the separately approved deployment since secret settings are not artifact files but can be accessible to build processes.

## Provider rights and remaining approvals

ThemeParks.wiki terms explicitly welcome dashboards and human-facing display with linked Powered by ThemeParks.wiki credit. They prohibit re-API/feed/mirroring, bulk extraction and third-party embedded widgets. A user saving a generated chart is distinguished from a bulk data export. Private R2 storage is not a redistribution licence and signed GETs do not grant publication rights. Keep raw-observation JSON disabled. Obtain specific written clarification before expanding to a data API, downloadable observations, third-party widgets or other feed-like delivery. No blanket provider prohibition on the proposed human-facing charts was found, but the final display/security implementation must still match those terms before activation.

Unresolved gates: authenticated public-surface configuration evidence; any OneDrive/device-only-storage handling the owner requires; approval for a specific real-data upload/retention plan; then separate Git/Render publication, deployment and chart activation approvals. No step in this document authorizes or performs them.

Official references: https://developers.cloudflare.com/api/resources/r2/subresources/buckets/subresources/domains/subresources/managed/methods/list/ , https://developers.cloudflare.com/api/resources/r2/subresources/buckets/subresources/domains/subresources/custom/methods/list/ , https://developers.cloudflare.com/r2/api/s3/api/ , https://www.themeparks.wiki/terms .

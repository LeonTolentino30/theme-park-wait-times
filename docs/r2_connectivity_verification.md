# Phase 8M: synthetic-only live R2 connectivity verification

This phase permits only harmless invented test bytes under a new random connectivity-tests/phase8m/UUID/ prefix in queue-atlas-history. It never lists or reads historical objects, loads a real snapshot, queries SQLite, updates manifests, activates production history or deploys. No account or bucket is created.

## Credentials

The existing production reader requires QUEUE_ATLAS_R2_ACCOUNT_ID, QUEUE_ATLAS_R2_BUCKET, QUEUE_ATLAS_R2_READ_ACCESS_KEY_ID and QUEUE_ATLAS_R2_READ_SECRET_ACCESS_KEY. QUEUE_ATLAS_HISTORY_ENABLED remains 0/unset. The isolated connectivity helper additionally uses QUEUE_ATLAS_R2_PUBLISH_ACCESS_KEY_ID and QUEUE_ATLAS_R2_PUBLISH_SECRET_ACCESS_KEY. Publisher credentials must never be placed in Render.

Use R2's S3 Access Key ID / Secret Access Key pair from each named token, not a Cloudflare bearer token. Never paste keys into chat, source, screenshots, command arguments or output. The secure setup helper prompts using Read-Host -AsSecureString. It saves SecureStrings encrypted using Windows DPAPI at LocalApplicationData/QueueAtlas/credentials/r2-connectivity.clixml, outside OneDrive and Git. The directory is restricted to the current Windows user and SYSTEM. DPAPI requires the same user/machine to decrypt; it is not protection from software already running as that user. Existing credential files are not overwritten.

From the project root, run in your own local PowerShell terminal:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Set-QueueAtlasR2Credentials.ps1
```

The execution-policy override applies only to that process, not system policy. Tell the assistant only when setup is complete; never send credential values. The account ID is non-secret metadata, while both key IDs and secrets are entered masked and encrypted.

## Bounded probe

Test-QueueAtlasR2Connectivity.ps1 decrypts inside a local process, supplies credentials only through a short-lived child environment, and clears temporary references. It does not modify parent, user or machine environment variables. Python removes credential variables from its environment after constructing the required configurations; keys necessarily remain briefly in process memory for signing, not in source/logs/output. No credentials appear in command-line arguments. The launcher never prints raw exception details or environment values.

Run the live helper only under explicit connectivity authorization:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-QueueAtlasR2Connectivity.ps1
```

It conditionally PUTs one tiny JSON fixture with the publisher, GETs and byte-compares it with the existing read-only adapter transport, attempts a fresh synthetic PUT with the read-only identity, attempts DELETE of only the newly created fixture, tests an anonymous S3 GET, and confirms the fixture remains intact. Read-only mutations must return 403 AccessDenied; signature errors are not counted as permission verification. An unexpectedly permitted mutation stops the sequence. All keys are confined to the newly generated exact test prefix. No redirects, list calls, retries, production key-prefix changes or arbitrary object paths are allowed.

An anonymous S3 request may return 400 for missing authentication rather than 403. That counts as an authentication rejection only when the parsed, allowlisted error code and message category establish missing authorization/signature/authentication; an unrelated 400 is inconclusive. Raw XML/error messages are never printed. A read-only follow-up can classify an earlier anonymous response against the same fixture without repeating uploads or mutation checks.

Secret-free reports are written with exclusive creation beside the encrypted credential file, outside OneDrive and Git. A separate -AuditOnly launcher mode uses the actual credentials only in memory to check current Git-listed first-party text for exact-value leaks, excluding private data/model/environment directories. It makes zero network requests. This is a current-source scan, not a complete historical Git or operating-system-log audit. Public AWS example credentials in tests are documentation vectors, not actual keys.

The fixture is intentionally retained. No successful publisher DELETE cleanup is performed without a separate decision; read-only DELETE is expected to fail. Secret-free reports record the exact harmless test prefix for later cleanup approval.

## Scope of privacy verification

An anonymous GET denial confirms only the S3 endpoint's behavior for the tested known object. It does not independently establish that r2.dev and custom-domain public access are disabled; the S3 tokens do not grant management-plane access to audit those settings. Keep both public surfaces disabled in the R2 dashboard. Do not broaden token permissions merely to make this test audit management settings.

Official references: https://developers.cloudflare.com/r2/api/tokens/ , https://developers.cloudflare.com/r2/buckets/public-buckets/ , https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.utility/export-clixml . No real observations may be uploaded until separately approved.

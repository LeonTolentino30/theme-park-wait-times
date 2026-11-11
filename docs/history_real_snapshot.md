# Phase 8K: authorized private historical snapshot

This phase enables only the specifically approved local extraction interval: start 2026-11-08T19:43:55Z inclusive, end 2026-11-11T00:00:00Z exclusive. All seven previously approved attraction UUIDs must be explicitly supplied through ExtractionScope. The source is fixed to the existing private/themeparks/pilot/pilot.sqlite3; there is no arbitrary database/output path CLI. No cloud resource, upload, public data endpoint, collector restart or deployment is part of this phase.

## Source preservation and output

The fixed real entry point is src.history_real_snapshot. It refuses another extraction if its destination already exists. It requires the collector to be absent and the closed source to have no WAL/SHM/journal sidecars. It then uses SQLite mode=ro and immutable=1 only under those conditions, never against an active WAL database. No journal-mode changes or checkpoints run. The shared reader has column-level authorization, exact integer-microsecond bounds, row/time limits, query_only and a short read transaction. Its connection closes before snapshot processing. Source database/sidecar hashes are checked before and after; changes abort output. This closed-source method prevents SQLite from creating or altering sidecars. It is intentionally not a general live-WAL extraction service.

A version-2 collected_observations snapshot preserves original fetched/provider timestamp strings, all approved fields, nulls, operating states and necessary quality flags. Duplicate instants, bad park mappings, future provider times and invalid values fail validation. No rows are interpolated or invented. Checksums/content-addressing remain unchanged. Fixture-only validators/writers reject collected data unless a narrowly explicit allow_real argument is supplied.

Output is restricted to private/themeparks/snapshots/phase8k-20261108T194355Z-20261111T000000Z. Git must confirm it is ignored and untracked before extraction. A new bundle directory is created exclusively and protected with a verified Windows DACL limited to the current user and SYSTEM before records are written. The DACL is applied with the Windows access-control API through PowerShell and read back to verify protection and identities. Immutable object files, an aggregate-only report and the manifest use exclusive creation. Manifest publication occurs last. Existing directories/files are never overwritten or deleted. The local bundle contains no raw provider responses, headers, credentials, logs or source database copy. Aggregate results remain inside the private bundle rather than being inserted into public documentation.

Hash integrity assumes a trusted manifest and private storage. NTFS access restrictions do not encrypt data or control an existing OneDrive synchronization policy. No upload is requested by this code; review any independently configured synchronization of the project directory before treating it as strictly device-only storage.

## Local preview of the existing bundle

Do not regenerate the snapshot. From the project root in a separate terminal:

```powershell
.\venv\Scripts\python.exe -B -m src.history_private_demo
```

Open http://127.0.0.1:5002 and select a ride, 6 hours / 24 hours / 7 days, and Archived historical observation period. The preview reads only the validated local snapshot, never SQLite. Debug and the reloader are disabled. It rejects non-loopback clients, unrecognized Host values and any attempt to combine private mode with PUBLIC_DEPLOYMENT. Default wsgi.py is unchanged and cannot serve real snapshots. The existing local SQLite history and fixture demo remain separate.

The renderer labels collected data as archived posted waits, not live waits, AI forecasts or measured queue durations. Requested and actual sample periods are distinct. Missing leading/trailing intervals, insufficient coverage and collection gaps produce incomplete-history labels; closures/null values break the line. A status sample does not imply closure across unrecorded periods. Rolling mode uses actual current UTC time and may have no data; it never silently switches to the archive. The source files and browser response do not contain a raw-observation JSON endpoint. Charts are inherently human-readable, so SVG is not a secrecy or licensing workaround.

## Validation and next approvals

The automated suite uses invented temporary sources only and explicitly blocks the real collector path. Local tests cover immutable source preservation/no sidecar creation, precise authorization bounds, active-source refusal, hash/integrity errors, write-once behavior, public-mode rejection, remote/Host rejection, real/fixture labels, chart windows and incomplete edges. Visual iPhone/browser review remains manual if no browser automation is available.

ThemeParks.wiki terms permit internal analytics and user-facing dashboards, but not feed/API/bulk redistribution: https://www.themeparks.wiki/terms . Attribution remains linked in charts. This phase authorizes only private local output and local review, not publishing observations or derived files. Cloud storage, credentials/accounts, uploads, Git publication and Render changes require separate approval. No scheduled task or collector modification is authorized.

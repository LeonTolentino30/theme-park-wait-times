# Phase 8J: exact timestamps and fixture-only extraction preparation

## Authorization boundary

This implementation has NOT opened or queried the real collector database, generated a real snapshot, or uploaded anything. The Phase 8I aggregate assessment was separate authorization and is not reused here. No collector process, deadline, lock, model, real observation, cloud account, or deployment configuration is changed.

`FixtureDatabase` creates and owns a fresh temporary database using the collector's existing schema helper. `extract_fixture` accepts only that exact source type and an explicit `ExtractionScope`; it has no arbitrary real-path argument. Paths must remain inside the source's owned temporary directory. Symlink/path substitution and missing WAL sidecars fail before a reader connection opens. A real-data source type and CLI are deliberately absent. This is a guard against accidental real access, not a sandbox against a malicious Python caller rewriting program internals.

## Timestamp compatibility and schema versions

Snapshot UTC timestamps now accept whole seconds or 1-6 fractional digits, ending in Z or +00:00. The original JSON/SQLite strings are retained exactly, including trailing zeroes and UTC spelling. No fetched/provider timestamp is reformatted, truncated, rounded, or normalized for output. Non-UTC offsets, naive times, invalid dates, and more than six fractional digits are rejected; precision unsupported by Python's microsecond datetime must be reviewed rather than silently lost.

Ordering, duplicate detection and gap calculations use parsed instants, not lexical timestamp order. Z and +00:00 spellings of the same instant are duplicates for the same attraction. An additional microsecond remains a distinct observation. Integer microseconds since the UTC epoch drive SQLite filtering and ordering; julianday/float epoch comparisons are avoided because they can merge or misclassify boundary microseconds. New York EST/EDT conversion is for display only. DST fallback/spring-forward regression tests confirm UTC ordering remains intact.

V1 records remain supported, including the existing whole-second fixture and its original canonical checksum/key. V2 records add the required approved `park_id`, which must match the reviewed attraction registry. Existing fields remain attraction_id, observed_at (original fetch timestamp), provider_last_changed_at, status, wait_minutes, present and quality_flags. The source is ThemeParks.wiki; dataset_kind remains synthetic_test_fixture. Production-kind observations are still deliberately rejected.

Both versions retain canonical UTF-8 JSON, SHA-256/byte-length validation, strict manifest/content version agreement and immutable `history/v<version>/<sha256>.json` names. `write_fixture_bundle` creates a new directory and exclusive files; any existing destination is refused. The object is written first, manifest last. Partial bundles are not published, and no cleanup deletes or replaces a user's existing directory. Digest verification assumes a trusted manifest and does not authenticate a party able to replace both object and manifest.

## Extraction scope

All seven approved active attraction IDs must be explicitly supplied as a tuple; missing, extra, duplicate, unsupported or implicit allowlists are rejected. No new attractions are introduced. The only selected row data is:

- attraction ID and park ID;
- original fetch/observation and provider last-change timestamp strings;
- operating state, nullable posted standby wait, presence;
- recognized necessary quality flags.

Run ID/outcome/source and run park ID are used internally for eligibility, ordering and consistency checks, not exported. Successful ThemeParks.wiki runs only are eligible. Unrelated attraction IDs and out-of-range records are excluded. Wrong park IDs, invalid values, invalid timestamps, duplicate instants and unknown flags fail validation rather than being silently repaired. The permitted flags include the collector's missing_entity, unknown_status, missing_wait, non_operating_wait, missing_provider_timestamp and future_provider_timestamp, plus the previously supported stale_provider flag. A provider timestamp after fetch is still rejected by snapshot validation; supporting its flag name does not waive that rule.

Raw API bodies, headers, credentials, logs, errors, machine paths, pilot state and other unrelated fields are never selected. A read authorizer denies access to unapproved columns and SQL write operations.

Date interpretation is explicit: November 8 through November 15, 2026 are inclusive UTC calendar dates. The hard envelope is `2026-11-08T00:00:00Z <= start < end <= 2026-11-16T00:00:00Z`, and every individual scope is capped at seven days. An exact end instant is excluded. Thus the entire eight-date envelope cannot be extracted as one snapshot. Narrower bounds may include fractional seconds. The fixture CLI demonstrates November 8 00:00 through November 15 00:00 (exclusive); it does not read November 15 records. Any future real extraction must receive separately approved exact start/end bounds.

## Read-only mechanics and data semantics

The reader uses mode=ro, query_only, in-memory temporary SQL work, an explicit read transaction, 250 ms busy timeout, a three-second progress budget and a 20,001-row limit used to reject overflow beyond 20,000 records. Existing WAL/SHM sidecars are required before opening, so the reader does not create them or use immutable=1 to ignore a live WAL. It never sets journal mode or checkpoints. SQLite read locks/read marks are normal WAL reader coordination; no observation/database/WAL content is changed. Fixture tests verify database/WAL byte preservation and immediate writer access afterward.

The connection closes in finally before conversion, validation, packaging or filesystem writes. An extraction/validation failure produces no output artifact. A deliberately empty selection yields an empty fixture snapshot with an insufficient-chart state. No missing observation is filled, and closed or DOWN status never becomes a numeric zero. Genuine operating zero remains zero. Collection gaps over 450 seconds break charts; absence does not imply a closure covering the gap.

## Fixture aggregate dry run

From the project root:

```powershell
.\venv\Scripts\python.exe -B -m src.history_extraction --dry-run-fixtures
```

This command creates an owned temporary collector-schema database, seeds invented fractional-second records, reads it read-only, prints aggregate coverage/quality counts, and closes/removes only its own temporary fixture workspace. It has no --db/--database/real-path switch, creates no snapshot files, and performs no provider/network/cloud calls. Do not replace fixture inputs with real observations.

The expected fixture report has 49 records across seven rides: 21 numeric operating values, seven genuine zeroes, seven CLOSED, seven DOWN, 28 null waits and seven per-attraction gap events. Counts refer solely to invented test data. The summary contains coverage bounds and per-attraction counts, not individual observations, wait values or provider timestamp arrays.

The existing fixture web demonstration remains:

```powershell
.\venv\Scripts\python.exe -B -m src.history_fixture_demo
```

Open http://127.0.0.1:5001. Default local charts, prediction, map and production WSGI configuration remain unchanged.

## Separate approvals required before real processing

1. Approve exact UTC start/end instants (inclusive start, exclusive end), the seven-ID allowlist, these fields and quality rules. Decide how malformed/duplicate/future-timestamp records should be reviewed; no silent correction is proposed.
2. Approve a specific read-only real-source adapter and private destination/retention plan. Only then add the real-source entry point; this phase has none. Require existing WAL sidecars, short reads, no collector changes and a private aggregate dry run first.
3. Approve activation of a production dataset kind and actual local extraction/write-once snapshot creation. Preserve timestamp strings and continue to exclude raw responses, headers, errors and logs.
4. Separately approve cloud account/bucket/billing/credential setup and uploads, if wanted. Keep publisher and Render read credentials separate, and retain provider attribution and restrictions against API/feed/bulk redistribution.
5. Separately approve any new dependency installation, Git publication and Render configuration/deployment. No such action occurs here.

# Phase 3B: Universal Orlando provider adapter

Status: implemented and tested offline. Proposed identity registry awaits user review. No collection pilot, background task, live snapshot request, database, or history download has been activated.

## Documentation reviewed November 8, 2026

- Official API: https://www.themeparks.wiki/api
- API terms: https://www.themeparks.wiki/terms
- Conservative five-minute polling recommendation: https://www.themeparks.wiki/reliability
- Public coverage: https://www.themeparks.wiki/coverage
- Official retired-attraction reference: https://www.universalorlando.com/web/en/us/plan-your-visit/retired-attractions

Reviewed the current official API documentation and terms before requests. Used exactly one GET `/v1/entity/{park_id}/children` per park, for Universal Studios Florida and Universal Islands of Adventure. No destination, attraction-specific, live, schedule, or history requests were made. No key, account, package installation, or paid service was used.

## Private proposed registry

`private/themeparks/attraction_registry.proposed.json` contains eight mappings, evidence URLs, fetched/verified timestamps, provider names and IDs, retirement flags, and per-entry review status. Seven active identities were verified by exact provider name, ATTRACTION entity type, and park parent ID against the two directory responses. The overall registry remains proposed pending user review; technical identity verification is not approval to run a pilot.

Hollywood Rip Ride Rockit is retired and absent from the current Studios directory. Its historical UUID and exact historical provider name remain null/unverified and marked manual_review. Do not invent an ID or make extra discovery requests to resolve it without approval. Do not remove its option from the synthetic demo: the synthetic interface is intentionally preserved.

Directory snapshots and preservation hashes are also under `private/themeparks/`. `/private/` is Git-ignored. Do not force-add these files, copy provider response fixtures into tests, or expose the directory through Flask. Source code includes only the two park identifiers required to restrict endpoint access; tests use fabricated attraction IDs and values. No provider feed or captured dataset is published.

## Adapter contract

`src/themeparks_provider.py` uses only Python standard-library HTTP/JSON/date modules. Importing it does not make requests. It does not import the ML pipeline, deserialize any model, alter the synthetic app, write observations, or start threads/tasks.

`load_registry()` reads the private mapping. `ThemeParksProvider.directory(park_id)` and `.live(park_id, registry)` are explicit, one-shot library calls using documented public endpoints. These calls exist for future approved work; do not call them to start collection now. URLs are restricted to the two supported park IDs and `children`/`live` resources. Redirects are disabled. The default timeout is 15 seconds, the maximum accepted JSON response is 2 MB, and there are no implicit retries. No API keys are used or required for these endpoints.

`live()` returns immutable normalized observations for verified, non-retired mappings in the requested park. It never fetches rides individually. Unmapped entries in a park response are ignored; retired and unresolved mappings are excluded even if their records appear.

Observation fields:

- `alias`, `park_id`, `attraction_id`, `source` identify the record.
- `status` is OPERATING, DOWN, CLOSED, REFURBISHMENT, UNKNOWN for an unrecognized/missing status, or UNAVAILABLE for an entity absent from an otherwise valid response.
- `standby_wait_minutes` is a nonnegative integer or None. A supplied zero is retained, including on a closed record; status and `usable_wait` prevent confusing a closed zero with an operating walk-on. No wait is imputed or converted to zero. Single-rider/return-time queues are not mixed into standby.
- `provider_last_changed_at` is the provider's last-change timestamp normalized to UTC, or None. It is not claimed to be the last successful upstream observation.
- `fetched_at` is the timezone-aware UTC time after receipt/parsing; `present` distinguishes missing entities.
- `quality_flags` describe missing waits/timestamps/entities, unknown statuses, non-operating waits, or future provider timestamps.
- `usable_wait` means an operating record with a numeric wait. It does not certify freshness; the caller must additionally review quality flags and transport health before training/display.

Unknown status strings remain UNKNOWN for forward compatibility. Malformed envelopes, duplicate entities, invalid queue shapes, negative/noninteger waits, invalid/naive timestamps, oversized bodies, non-JSON content, and malformed JSON fail safely as `InvalidResponse`; partial batches are not silently accepted. HTTP/network/timeout errors raise sanitized `ProviderError`, not empty successful observations. Rate limits raise `RateLimited` carrying a parsed Retry-After delay (seconds or HTTP date), or None when unavailable. Error bodies are not exposed. Callers must not mistake errors for closed rides.

## Attribution and data-use restrictions

When displaying provider data, use a prominent linked **Powered by ThemeParks.wiki** credit to https://www.themeparks.wiki. Our unchanged synthetic demo currently displays no provider observations and must not imply they powered its estimates.

The reviewed terms allow personal, research and commercial products, internal analytics, recording your own live history for your product, and retaining derived forecast models. They prohibit redistribution/mirroring of a substantial feed, public raw-data APIs, bulk export, scraping to evade the API, and implying official park/provider endorsement. A matching code/software licence does not grant a licence to redistribute data. Keep observations private; do not publish them in GitHub or package them as test fixtures. Ask the provider about any ambiguous publication or redistribution use rather than assuming permission. Future terms/limits may change and must be rechecked before activation.

## Proposed five-minute pilot -- NOT ACTIVE

After the user approves the seven verified identities and a pilot:

1. Run a finite, locally controlled seven-day pilot, one park-level live GET per park every five minutes, staggered by a few seconds. This is at most 576 scheduled requests/day for both parks; seven ride observations per successful pair of requests. No per-ride polling or history backfill.
2. Start with a manually supervised, one-shot live smoke test. Review actual live field shapes before starting repeated reads: only directory shapes have been verified against the service in Phase 3B.
3. Keep outputs under ignored private storage; a reviewed local append-only format can precede a database. Record fetch/run success separately from observations. Do not persist HTTP bodies containing unexpected personal information. No storage writer or scheduler is implemented in this phase.
4. Honour Retry-After; suppress requests for the affected provider until its deadline. On missing Retry-After, use a conservative five-minute delay. For timeouts/5xx, use bounded exponential backoff and resume without catch-up bursts. Stop on persistent authentication/permission/schema errors rather than repeatedly hitting the service.
5. Record successful receipt time, last-change time, status, nullable wait and quality flags. Missing responses are collection failures, not zero-wait rows. A long-unchanged provider timestamp alone cannot establish staleness; consider transport success, park schedules and upstream health. Exclude future/invalid timestamps and questionable records from training pending review.
6. At the end, report coverage by park/ride/day, missing observations, status distributions, response errors, timestamp anomalies, and longest collection gaps. Seven days is an ingestion-quality test, not evidence of seasonal forecasting accuracy.
7. Stop after the approved pilot window. Request approval for continued collection, any database, or historical access. The synthetic predictions stay unchanged throughout.

## Tests

```powershell
.\venv\Scripts\python.exe -B -m unittest discover -s tests -p test_themeparks_provider.py -v
.\venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Provider tests are entirely offline and use fabricated fixtures. Existing Phase 1/2 tests still exercise the trusted synthetic model. Remaining uncertainties: retired historical UUID, real live-response compatibility until an approved smoke test, upstream freshness/completeness, and changing provider terms. A proposal is not an active collector.

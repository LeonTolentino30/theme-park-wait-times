# Finite seven-day private collection pilot

**Implemented and tested; collection runs separately from the website.** Starting or resuming a pilot is an explicit foreground action. Starting Flask or importing collector code never activates collection. No OS scheduled task is needed or created. Leave any existing pilot terminal running; do not launch a duplicate collector.

## Start, stop, resume

For a new explicitly authorized pilot, open PowerShell and keep the foreground terminal running:

```powershell
# Open PowerShell in the repository root (the folder containing app.py).
.\venv\Scripts\python.exe -B -m src.collection_pilot start
```

Ctrl+C gracefully stops the foreground process. Closing the terminal, sleep, or shutting down the computer stops/suspends collection; this implementation is not an unattended Windows service. Do not run another copy in another terminal. A kernel-managed lock prevents concurrent CLI processes; crashes automatically release the lock, so do not delete a lock file to force takeover.

To resume an authorized pilot that is stopped (not one already running):

```powershell
.\venv\Scripts\python.exe -B -m src.collection_pilot resume
```

Resume uses the original SQLite start time and deadline; it does not extend the window, restart seven days, or replay missed intervals. Running start against an initialized pilot fails. Resume after expiry makes no requests. Do not delete or edit the pilot database to reset the deadline. Registry identity must match the persisted pilot snapshot.

A schema/identity/permission failure, or a request exceeding its total wall-time budget, halts the pilot for manual review. Resume cannot automatically clear a persisted halt. Review the private logs with the project owner before deciding on a repair. Network timeouts/5xx follow persisted backoff rather than a rapid retry loop. SQLite or disk failures stop the program; an unfinished claimed run becomes interrupted on resume, never a fabricated observation.

## Storage and request policy

Everything is under Git-ignored `private/themeparks/pilot/`:

- `pilot.sqlite3`, plus SQLite `-wal`/`-shm` sidecars: pilot deadline, approved registry snapshot, park due times/failure counters, fetch attempts, observations, and raw JSON response bodies stored as private BLOBs.
- `pilot.log`: UTC foreground collector logs, including outcomes, errors, start/resume, and stop events.
- `pilot.lock`: harmless persistent file with a process-owned OS lock.

The production database is created only on explicit start/resume, never by importing the module, running tests, reading --help, or requesting a report. Tests create and remove only their own temporary databases. The collector uses standard-library sqlite3/HTTP/threading modules; no packages were installed.

Only ThemeParks.wiki is used. Exactly the seven approved active UUIDs are eligible; retired Rip Ride Rockit remains excluded. The website and synthetic API/model are not connected to the collector and continue displaying synthetic predictions.

One documented park-level `/v1/entity/{id}/live` request per due park, at most every 300 seconds; the second park initially starts five seconds later. No per-ride calls, directory refreshes, historical requests, or backfill. The timer is conservative: next due is measured from request completion. Longer Cache-Control max-age/s-maxage (accounting for Age) and Expires delays are respected. Thus the request ceiling is 576 per complete 24-hour window for two parks, usually fewer with latency/backoff. A last-change timestamp is not proof of last upstream refresh.

All times are stored as UTC epoch values or ISO timestamps with +00:00. Only report presentation converts to America/New_York. The deadline is start + 604800 seconds. A monotonic deadline additionally bounds the current process; clock rollback stops collection. A request is not started without its full timeout remaining before cutoff. A daemon performs the bounded HTTP call; only the foreground thread writes SQLite. If the total request budget is exceeded, the collector persists a halt and exits without retrying the lingering request. No new requests are started at/after expiry.

429 and any explicit Retry-After delay (including HTTP-date values and 503 responses) apply provider-wide and persist across resumes. Absent Retry-After, retries wait at least five minutes. Transient failures double the delay per park up to one hour; larger cache or Retry-After values are never shortened. No catch-up bursts. Unknown/malformed schemas and permission failures halt for review rather than repeatedly hitting the provider.

Each park attempt is claimed transactionally before HTTP. A unique (park, five-minute slot) key plus persisted next-due times prevents repeats. An OS lock prevents duplicate CLI sessions. Observations have a unique (run, attraction) primary key, and the entire normalized batch commits atomically with its run outcome. This is at-most-once attempt scheduling: a crash may lose a received batch, but will not replay that slot. Gaps are reported honestly.

Fields stored include park/attraction/source, fetch and provider-last-change time, status, nullable standby minutes, presence and quality flags. Closed zero, operating zero and unavailable/null remain distinct. A failed HTTP call records an error run with no observation rows. Malformed/oversized/non-JSON bodies fail safely; only bounded captured bodies are retained. Headers/bodies are private diagnostics, not a public endpoint or Git fixture. Provider last-change timestamps are not fresh-observation guarantees.

## Inspect and final report

The same read-only command works during the pilot or after its deadline; it never makes HTTP requests or creates a missing database:

```powershell
.\venv\Scripts\python.exe -B -m src.collection_pilot report
```

Optionally save the report privately after activation:

```powershell
.\venv\Scripts\python.exe -B -m src.collection_pilot report > .\private\themeparks\pilot\final_report.json
Get-Content .\private\themeparks\pilot\pilot.log -Tail 30
```

The report includes per-ride expected five-minute slots, record counts, coverage fraction, missing records, unattempted slots, absent entities, null waits, numeric operating waits, genuine operating zeroes and status distributions; overall run outcomes/non-success rate; longest successful-fetch gaps per park; original UTC start/deadline and New York deadline. These are ingestion-quality metrics, not model accuracy. Expected slots include interruptions and deliberate backoff; they are a nominal full-cadence denominator. Closed/unknown records count as ingested records, not usable training targets. Provider absence and collection failure are distinct. No missing records are filled with estimates.

Because response duration and the five-second stagger reduce cadence slightly, perfect nominal slot coverage is not promised. Seven days tests ingestion reliability; it does not establish seasonal forecasting performance. Data and reports remain private and must not be committed or published as a feed.

## Tests and permissions

```powershell
.\venv\Scripts\python.exe -B -m unittest discover -s tests -p test_collection_pilot.py -v
.\venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Collector tests use temporary SQLite databases, fake clocks, and fabricated providers; no live collection occurs. Adapter metadata tests mock responses. Existing synthetic-model tests refit temporary in-memory test models but do not replace saved artifacts.

Provider: [Powered by ThemeParks.wiki](https://www.themeparks.wiki). [API documentation](https://www.themeparks.wiki/api), [terms](https://www.themeparks.wiki/terms), [five-minute guidance](https://www.themeparks.wiki/reliability). Retaining live observations for your own product and derived forecasting is permitted; bulk export, mirroring, redistribution of a substantial feed and implied park/provider endorsement are prohibited. Keep raw records private and credit the provider if later displaying real observations. No API key, paid plan, scheduled task, or new dependency is needed for this pilot. Recheck terms before any later deployment or redistribution.

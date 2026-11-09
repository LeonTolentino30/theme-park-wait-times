# Phase 4: Current Posted Waits dashboard

The homepage retains Queue Atlas's synthetic prediction form and visual identity and adds a separate **Current Posted Waits** section. Seven approved active attractions are grouped by Islands of Adventure and Universal Studios Florida. Retired Rip Ride Rockit remains in the synthetic demo only.

## Reading observations safely

`src/current_waits.py` reads the approved private registry using the existing provider adapter's registry loader. It never creates a provider client or requests live data. It opens the collector's existing database with SQLite URI `mode=ro`, sets connection-local `query_only`, begins a short consistent read transaction, and closes promptly. It does not call the collector's connection/initialization functions, change schema/PRAGMAs on the database, take the pilot process lock, or checkpoint the WAL.

Each request selects the latest successful run for each park, then only its normalized observations for approved registry UUIDs. An absent entity is unavailable; an older numeric wait is not resurrected. A failed/in-progress newer run does not replace the last successfully stored batch. SELECT queries omit raw response BLOBs, run errors, log content and private filesystem paths. The active WAL writer can continue committing while the page reads a snapshot. No collector or provider code was modified in Phase 4.

The SQLite path and private registry are server-side configuration only. Missing, empty, corrupt or temporarily inaccessible databases produce a sanitized unavailable state, never a newly initialized/overwritten database. The rest of the homepage and synthetic predictions remain usable. Database/registry errors do not appear in visitor responses.

## API and presentation

`GET /api/current-waits` returns only application-facing cards: ride name, park, normalized status, displayed posted wait, collection time, provider last-change time and snapshot freshness. ISO timestamps are UTC; displayed dates use America/New_York. JSON is read-only and is not a historical/bulk export. Linked **Powered by ThemeParks.wiki** attribution is displayed on the page.

200 means at least one stored observation is available; other cards may still be unavailable. 503 means no observations or an unavailable database/registry; the response retains safe placeholder cards when mappings are available. POST is rejected. Cache-Control is no-store. Do not expose this local endpoint publicly as a replacement provider data feed.

- Operating numeric zero displays **0 min**.
- CLOSED/DOWN/REFURBISHMENT display their status, never a misleading zero-minute wait.
- Missing numeric waits display **Wait unavailable**; missing entities/data display **Unavailable**.
- Collection age up to 15 minutes is labelled **Recent stored observation**, never verified real-time data. More than 15 minutes is a **Historical snapshot**. Future/inconsistent timestamps are also treated conservatively. Missing/invalid collection timestamps make the card unavailable.
- Provider last-change time is separate from collection time. A value that has not changed for hours does not by itself prove an outage.

The initial page is server-rendered, so stored cards and attribution work without JavaScript. Reload the page or click **Refresh stored waits** to read newly committed observations. That button fetches only the local read-only endpoint; no automatic provider or database polling process exists. A local browser timer updates the age label of already displayed cards so old observations become historical without fetching data. Disabling JavaScript requires reloading to reassess age.

## Restart only Flask

Leave the terminal running `src.collection_pilot` untouched. In the separate terminal running `app.py`, press Ctrl+C, then:

```powershell
# Open PowerShell in the repository root (the folder containing app.py).
.\venv\Scripts\python.exe -B app.py
```

Open http://127.0.0.1:5000/ and reload the browser. Debug and reloader remain disabled and the app binds only to 127.0.0.1. If Flask is not running, simply launch this command in a new terminal. Do not terminate Python processes by name or stop the collector to restart the website.

The Flask process reads the database anew on every page/API request; it does not need restarting when the collector adds observations. The homepage can show different collection times for the two parks because their requests are staggered.

## Verification

New offline tests cover the API schema, seven-card grouping, freshness, future/invalid timestamps, missing/empty/corrupt databases, status semantics, latest-success selection, no fallback to obsolete per-ride values, safe responses, frontend rendering, and reads during an uncommitted WAL write. They use temporary fabricated observations and never contact ThemeParks.wiki or change the real collector database.

```powershell
.\venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Provider: [Powered by ThemeParks.wiki](https://www.themeparks.wiki). Posted observations may be delayed or wrong and are not measured time actually spent queuing. They are independent of the synthetic predictions. No models were retrained/replaced; no packages were installed.

# Interactive park map (Phase 8A)

The existing Flask homepage includes a self-hosted Leaflet 1.9.4 map. Public, reviewed GeoJSON is served by `GET /api/map-attractions?park=all` (also `islands` or `studios`). It contains only seven approved attraction IDs and geographic metadata; no database, provider API, model, or collector is accessed by this endpoint. The existing synthetic form and observation features are unchanged.

## Geographic provenance

Coordinates were verified against OpenStreetMap using one bounded Overpass directory query on 2026-11-10. Nodes use their mapped coordinates; building/station relations use their returned bounding-box centers. These are approximate attraction locations, not surveyed entrances, accessibility guidance, or walking routes.

| Attraction | Latitude | Longitude | OpenStreetMap source |
|---|---:|---:|---|
| VelociCoaster | 28.471099 | -81.4724911 | [Station/building relation 17295822](https://www.openstreetmap.org/relation/17295822) |
| Hagrid's Motorbike | 28.4734362 | -81.4735089 | [Node 8248132721](https://www.openstreetmap.org/node/8248132721) |
| Hulk Coaster | 28.4715017 | -81.4687757 | [Node 8248062617](https://www.openstreetmap.org/node/8248062617) |
| Spider-Man | 28.4700548 | -81.4696594 | [Building relation 2453688](https://www.openstreetmap.org/relation/2453688) |
| Forbidden Journey | 28.4718757 | -81.4739481 | [Building relation 17297382](https://www.openstreetmap.org/relation/17297382) |
| Revenge of the Mummy | 28.4768628 | -81.4699854 | [Node 4694456530](https://www.openstreetmap.org/node/4694456530) |
| Transformers | 28.4764 | -81.4684387 | [Node 5642698838](https://www.openstreetmap.org/node/5642698838) |

## Licensing, tiles, and privacy

GeoJSON geographic data is attributed to OpenStreetMap contributors and offered under [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/). Retain per-feature provenance and the attribution file when sharing this derived geographic dataset. Leaflet's BSD 2-Clause license is vendored with its JavaScript/CSS. No paid service or API key is required.

Standard raster tiles use `https://tile.openstreetmap.org/{z}/{x}/{y}.png` under the [OSMF tile usage policy](https://operations.osmfoundation.org/policies/tiles/). Visible linked attribution remains outside the map as well as in Leaflet. Tiles load only after the visitor chooses Load interactive map. Requests send the visitor's IP and site origin to OSM; no geolocation is requested. Browser caching is retained, with no proxy, offline storage, bulk download, prefetch, service worker, or automatic polling. The homepage referrer policy permits a valid origin Referer, as required by OSM. Leaflet needs inline style attributes; scripts remain restricted to same-origin resources.

OSM tiles are a best-effort community service without an availability guarantee. Recheck policy before any deployment or growth; switch to an appropriately licensed provider if capacity requires it. Failed tiles show a message while the local markers/list remain usable. Local catalog/library failure leaves the server-rendered attraction list available. No live waits are inferred or displayed on the map. The optional form link is explicitly a synthetic estimate and never submits a prediction automatically.

## Local review

From the project root, run `venv\Scripts\python.exe -B app.py` in a separate terminal. Open http://127.0.0.1:5000 and scroll to Find your next adventure. Do not restart the collector. If Flask is already running, restart only its terminal to load code changes.

Choose Load interactive map, test both park filters, select markers and list entries, and follow the synthetic estimate link. At iPhone-width viewport (390 px), verify stacked layout, pinch zoom, zoom controls, touch targets, and readable attribution. For a physical iPhone, localhost refers to the phone; use browser device emulation for this local-only review rather than exposing Flask publicly. Block tile.openstreetmap.org in browser developer tools to review tile-failure feedback; disable JavaScript to review the attraction list fallback. Do not automate tile panning or download tiles in tests.

Automated tests cover verified coordinates/provenance, exact approved IDs, filtering and invalid queries, malformed/missing catalogs, public-mode isolation, rendering, security headers, and vendored asset integrity. Physical iPhone interaction and visual browser QA remain manual checks.


## Phase 8D: deployed stylesheet integrity fix

Read-only checks of the deployed page and its two Leaflet assets confirmed that the CSS returned HTTP 200 with the correct text/css MIME type, but its SHA-256 did not match the template integrity attribute. The original downloaded CSS was 14,806 bytes with 661 CRLF line endings; Git stored the equivalent 14,145-byte LF file. Linux/Render therefore served different bytes and browsers rejected the stylesheet. The JS integrity matched. Without the official stylesheet, tiles lose absolute positioning and the viewport loses its Leaflet clipping, explaining scattered tiles outside the map. This is a deployment asset-integrity issue, not evidence of an iOS-only Leaflet defect.

Vendor CSS/JS now use explicit LF Git attributes. The template's CSS SRI matches the LF file; the stylesheet otherwise remains upstream-identical, verified by reconstructing its original CRLF hash in a test. Leaflet CSS loads before Queue Atlas overrides. The map viewport additionally has explicit 100% width, maximum width and clipping, with shrinkable grid/sidebar tracks. Existing touch zoom, consent, filters, markers, and ResizeObserver/invalidateSize handling remain unchanged. No tile requests or private observations are needed to test this fix. Public responses were no-store, and the same mismatch is reproducible from committed bytes; caching is not required to explain the failure.

Regression tests hash actual Flask-served assets against rendered integrity attributes, check LF checkout stability, official CSS essentials, load order, mobile viewport structure, and public-mode isolation. No browser automation package is installed in the project environment. Real Safari rendering at 375/390/430 px, pinch/pan, orientation changes, and filter changes still require manual browser verification after local preview or an approved deployment. No deployment is part of this fix.

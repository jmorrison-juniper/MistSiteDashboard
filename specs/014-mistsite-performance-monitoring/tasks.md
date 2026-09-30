# MistSiteDashboard performance monitoring tasks

## Task 1: Add request-level timing wrapper
- Wrap Flask route handlers with a standard latency monitor.
- Record duration, status, payload size, and route name.
- Acceptance: every dashboard route emits a consistent timing event.
- Status: Done. App-level Flask hooks emit route timing, status, payload bytes, item counts, site ID, sanitized query keys, errors, and optional peak bytes without per-route edits.

## Task 2: Add connection-level timing
- Instrument `MistConnection` session creation and API fetch paths.
- Record call count, response size, and slow operation markers.
- Acceptance: network time is distinguishable from processing time.
- Status: Done. `MistConnection` methods emit total, network, and processing timings, `_get_session` logs cache hit/miss, and a `requests` response hook records per-HTTP-call elapsed time, status, endpoint path, payload size, and retry history count.

## Task 3: Break down site health profiling
- Separate `get_site_health` stages into template fetch, mapping, device stats fetch, and summary generation.
- Acceptance: each stage can be compared independently.
- Status: Done. `get_site_health` emits stage timings for template fetch, WLAN mapping, device stats fetch, device categorization, and summary build.

## Task 4: Add allocation tracing for large site responses
- Run `tracemalloc` and sampling on large list-heavy responses.
- Acceptance: churn hotspots are identifiable by function and row count.
- Status: Done. `PERF_TRACEMALLOC=1` enables peak allocation bytes on route and Mist API method events; it remains off by default.

## Task 5: Add benchmark harness for dashboard routes
- Run representative requests against small and large site payloads.
- Capture p50/p95 duration and response size.
- Acceptance: route latency is known before optimization begins.
- Status: Done. `scripts/benchmark_routes.py` runs the Flask test client with a fake Mist connection for small, medium, or large synthetic payloads and prints p50/p95 latency and response size by route.

## Task 6: Finalize telemetry schema
- Normalize fields across route and connection monitors.
- Acceptance: all new events follow one schema and can be aggregated by route or function.
- Status: Done. `perf_monitor.py` defines the shared JSON schema and emits all performance records on the dedicated `msd.perf` logger.
